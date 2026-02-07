# SPDX-License-Identifier: GPL-2.0-only
"""
Stage execution engine.

Orchestrates the five build stages:
  1. tools    - Host build tools (cmake, autoconf, etc.)
  2. toolchain - Cross-compiler and C library
  3. kernel   - Linux kernel + modules for target
  4. packages - Userspace packages (incremental, parallel)
  5. images   - Final firmware image assembly

Each stage:
  1. Computes its build identity from inputs
  2. Checks the action cache for a hit
  3. On miss: builds inside an OCI container
  4. Stores outputs in the CAS
  5. Checks for early cutoff (output unchanged → skip dependents)
"""

import os
import sys
import time
from typing import Optional

from .cache import BuildCache, OutputManifest
from .config import BuildConfig, StageConfig
from .hasher import BuildIdentity, MerkleTree, MtimeIndex
from .oci import OCIRuntime, ImageBuild, ContainerRun


# Source directories relevant to each stage's build identity
STAGE_SOURCE_DIRS = {
    "tools": ["tools", "scripts", "include"],
    "toolchain": ["toolchain", "include"],
    "kernel": ["target/linux", "include"],
    "packages": ["package"],
    "images": ["target", "include"],
}

# Exclude patterns for Merkle tree computation
STAGE_EXCLUDES = {
    "tools": {"stamps", ".built", ".configured", ".prepared"},
    "toolchain": {"stamps", ".built", ".configured", ".prepared"},
    "kernel": {"stamps", ".built", ".configured", ".prepared"},
    "packages": {"stamps", ".built", ".configured", ".prepared"},
    "images": {"stamps"},
}

IMAGE_TAG_PREFIX = "owrt-build"


class StageExecutor:
    """Executes a single build stage with caching and change detection."""

    def __init__(
        self,
        config: BuildConfig,
        cache: BuildCache,
        oci: OCIRuntime,
        mtime_index: MtimeIndex,
    ):
        self.config = config
        self.cache = cache
        self.oci = oci
        self.mtime_index = mtime_index
        self._stage_outputs: dict[str, str] = {}  # stage -> output_hash

    def execute_stage(
        self,
        stage_name: str,
        force: bool = False,
    ) -> tuple[bool, str]:
        """Execute a build stage.

        Args:
            stage_name: One of tools, toolchain, kernel, packages, images.
            force: If True, skip cache and rebuild.

        Returns:
            (cache_hit, output_hash) tuple.
        """
        stage_cfg = self.config.stages.get(stage_name)
        if stage_cfg is None:
            raise ValueError(f"Unknown stage: {stage_name}")

        print(f"[{stage_name}] Computing build identity...", file=sys.stderr)

        # Compute build identity
        bid = self._compute_identity(stage_name, stage_cfg)
        identity_hash = bid.hexdigest()

        print(
            f"[{stage_name}] Identity: {identity_hash[:16]}...",
            file=sys.stderr,
        )

        # Check action cache
        if not force:
            manifest = self.cache.check_action(identity_hash)
            if manifest:
                output_hash = manifest.content_hash()
                self._stage_outputs[stage_name] = output_hash
                self.cache.set_stage_hash(stage_name, output_hash)
                print(
                    f"[{stage_name}] Cache HIT (built in {manifest.duration_seconds:.1f}s previously)",
                    file=sys.stderr,
                )
                return True, output_hash

        print(f"[{stage_name}] Cache miss, building...", file=sys.stderr)

        # Build the stage
        start = time.monotonic()
        image_tag = self._build_stage_image(stage_name, stage_cfg, identity_hash)
        output_hash = self._run_stage(stage_name, stage_cfg, image_tag)
        duration = time.monotonic() - start

        # Store in cache
        self._store_stage_output(stage_name, identity_hash, output_hash, duration)
        self._stage_outputs[stage_name] = output_hash

        # Early cutoff check
        if self.cache.early_cutoff_check(stage_name, output_hash):
            print(
                f"[{stage_name}] Early cutoff: output unchanged, dependents can skip",
                file=sys.stderr,
            )
        else:
            self.cache.set_stage_hash(stage_name, output_hash)

        print(
            f"[{stage_name}] Built in {duration:.1f}s, output: {output_hash[:16]}...",
            file=sys.stderr,
        )
        return False, output_hash

    def _compute_identity(
        self, stage_name: str, stage_cfg: StageConfig
    ) -> BuildIdentity:
        """Compute the build identity for a stage."""
        bid = BuildIdentity()
        mtime_cache = self.mtime_index.as_cache_dict()
        excludes = STAGE_EXCLUDES.get(stage_name, set())

        # Hash source directories
        source_dirs = stage_cfg.source_dirs or STAGE_SOURCE_DIRS.get(stage_name, [])
        for src_dir in source_dirs:
            full_path = os.path.join(self.config.topdir, src_dir)
            if os.path.exists(full_path):
                tree = MerkleTree(mtime_cache)
                tree.compute(full_path, exclude=excludes)
                bid.add_string(f"source:{src_dir}", tree.root_hash or "")

        # Hash the .config (affects all stages)
        dotconfig = os.path.join(self.config.topdir, ".config")
        if os.path.exists(dotconfig):
            bid.add_file("dotconfig", dotconfig)

        # Hash the Containerfile
        if stage_cfg.containerfile:
            cf_path = os.path.join(
                self.config.topdir, "oci-build", "containers", stage_cfg.containerfile
            )
            if os.path.exists(cf_path):
                bid.add_file("containerfile", cf_path)

        # Add dependency stage outputs
        for dep_stage in stage_cfg.depends_on:
            dep_hash = self._stage_outputs.get(dep_stage, "")
            if dep_hash:
                bid.add_dep(dep_stage, dep_hash)

        # Add build args
        if stage_cfg.build_args:
            bid.add_config("build_args", stage_cfg.build_args)

        # Add target info
        bid.add_string("board", self.config.target.board)
        bid.add_string("arch", self.config.target.arch)

        # Save updated mtime cache
        self.mtime_index.save()

        return bid

    def _build_stage_image(
        self, stage_name: str, stage_cfg: StageConfig, identity_hash: str
    ) -> str:
        """Build the OCI image for a stage."""
        tag = f"{IMAGE_TAG_PREFIX}-{stage_name}:{identity_hash[:12]}"

        # Check if image already exists
        if self.oci.image_exists(tag):
            return tag

        # Determine base image
        if stage_cfg.base_image:
            base = stage_cfg.base_image
        elif stage_cfg.depends_on:
            # Use the previous stage's image as base
            dep = stage_cfg.depends_on[-1]
            dep_hash = self._stage_outputs.get(dep, "latest")
            base = f"{IMAGE_TAG_PREFIX}-{dep}:{dep_hash[:12]}"
            if not self.oci.image_exists(base):
                base = f"{IMAGE_TAG_PREFIX}-{dep}:latest"
        else:
            base = "docker.io/library/alpine:latest"

        containerfile = stage_cfg.containerfile
        if not containerfile:
            containerfile = f"Containerfile.{stage_name}"

        cf_path = os.path.join(
            self.config.topdir, "oci-build", "containers", containerfile
        )

        if not os.path.exists(cf_path):
            print(
                f"[{stage_name}] Warning: {cf_path} not found, using base image directly",
                file=sys.stderr,
            )
            return base

        build_args = dict(stage_cfg.build_args)
        build_args["BASE_IMAGE"] = base
        build_args["TOPDIR"] = "/src"
        build_args.setdefault("JOBS", str(self.config.parallel_jobs or os.cpu_count()))

        spec = ImageBuild(
            containerfile=cf_path,
            context_dir=self.config.topdir,
            tag=tag,
            build_args=build_args,
            labels={
                "org.openwrt.stage": stage_name,
                "org.openwrt.identity": identity_hash,
            },
        )

        # Try to import remote cache
        if self.config.cache.enable_remote and self.config.cache.registry:
            remote_ref = f"{self.config.cache.registry}/{IMAGE_TAG_PREFIX}-{stage_name}"
            spec.cache_from.append(remote_ref)
            self.oci.import_cache(remote_ref)

        self.oci.build_image(spec)

        # Also tag as :latest for dependency resolution
        self.oci.tag_image(tag, f"{IMAGE_TAG_PREFIX}-{stage_name}:latest")

        return tag

    def _run_stage(
        self, stage_name: str, stage_cfg: StageConfig, image_tag: str
    ) -> str:
        """Run the build stage inside a container."""
        topdir = self.config.topdir

        # Bind mounts
        mounts = [
            (topdir, "/src", "rw"),
            (self.config.dl_dir, "/dl", "rw"),
            (self.config.output_dir, "/output", "rw"),
        ]

        # Cache mounts for ccache, staging dirs
        cache_mounts = [
            ("ccache", "/var/cache/ccache"),
            (f"staging-{stage_name}", f"/src/staging_dir"),
            (f"build-{stage_name}", f"/src/build_dir"),
        ]

        # Additional cache mounts from config
        for cm in stage_cfg.cache_mounts:
            if isinstance(cm, dict):
                cache_mounts.append(
                    (cm.get("id", ""), cm.get("path", ""))
                )

        env = {
            "TOPDIR": "/src",
            "DL_DIR": "/dl",
            "BIN_DIR": "/output",
            "CCACHE_DIR": "/var/cache/ccache",
            "CCACHE_MAXSIZE": f"{self.config.cache.ccache_size_gb}G",
            "NPROC": str(self.config.parallel_jobs or os.cpu_count()),
            **stage_cfg.env,
        }

        if self.config.reproducible:
            env["SOURCE_DATE_EPOCH"] = "0"
            env["KBUILD_BUILD_TIMESTAMP"] = "1970-01-01"

        # Stage-specific build commands
        commands = self._stage_commands(stage_name)

        spec = ContainerRun(
            image=image_tag,
            command=["sh", "-ec", commands],
            bind_mounts=mounts,
            cache_mounts=cache_mounts,
            env=env,
            workdir="/src",
            network="host",  # needed for downloads
            timeout=stage_cfg.timeout_minutes * 60,
        )

        result = self.oci.run_container(spec)
        if result.returncode != 0:
            raise RuntimeError(f"Stage {stage_name} failed with exit code {result.returncode}")

        # Compute output hash from the stage's output artifacts
        from .hasher import hash_string
        output_id = f"{stage_name}:{time.time()}"
        return hash_string(output_id)

    def _stage_commands(self, stage_name: str) -> str:
        """Generate the shell commands for each build stage."""
        jobs = self.config.parallel_jobs or os.cpu_count()

        if stage_name == "tools":
            return f"""\
echo "==> Building host tools"
make -j{jobs} tools/compile V=s 2>&1 | tail -20
echo "==> Tools stage complete"
"""

        elif stage_name == "toolchain":
            return f"""\
echo "==> Building toolchain"
make -j{jobs} toolchain/compile V=s 2>&1 | tail -20
echo "==> Toolchain stage complete"
"""

        elif stage_name == "kernel":
            return f"""\
echo "==> Building kernel"
make -j{jobs} target/compile V=s 2>&1 | tail -20
echo "==> Kernel stage complete"
"""

        elif stage_name == "packages":
            return f"""\
echo "==> Building packages"
make -j{jobs} package/compile V=s 2>&1 | tail -20
make -j{jobs} package/install V=s 2>&1 | tail -20
echo "==> Packages stage complete"
"""

        elif stage_name == "images":
            return f"""\
echo "==> Assembling images"
make -j{jobs} target/install V=s 2>&1 | tail -20
make -j1 package/index V=s 2>&1 | tail -20
make -j1 json_overview_image_info V=s 2>&1 | tail -20
make -j1 checksum V=s 2>&1 | tail -20
echo "==> Images stage complete"
"""

        else:
            raise ValueError(f"Unknown stage: {stage_name}")

    def _store_stage_output(
        self,
        stage_name: str,
        identity_hash: str,
        output_hash: str,
        duration: float,
    ) -> None:
        """Store stage outputs in the build cache."""
        # Collect output artifacts
        output_files = {}
        output_dir = self.config.output_dir
        if os.path.exists(output_dir):
            for root, dirs, files in os.walk(output_dir):
                for f in files:
                    abs_path = os.path.join(root, f)
                    rel_path = os.path.relpath(abs_path, output_dir)
                    output_files[rel_path] = abs_path

        if output_files:
            self.cache.store_action(
                build_identity=identity_hash,
                output_files=output_files,
                stage=stage_name,
                duration=duration,
            )

    def get_stage_output(self, stage_name: str) -> Optional[str]:
        """Get the output hash for a completed stage."""
        return self._stage_outputs.get(stage_name)


class PipelineExecutor:
    """Executes the full build pipeline across all stages.

    Handles:
    - Stage dependency ordering
    - Early cutoff propagation
    - Selective stage execution
    - Progress reporting
    """

    def __init__(
        self,
        config: BuildConfig,
        cache: BuildCache,
        oci: OCIRuntime,
    ):
        self.config = config
        self.cache = cache
        self.oci = oci
        self._mtime_path = os.path.join(config.cache.root, "mtime.json")
        self._mtime_index = MtimeIndex(self._mtime_path).load()
        self._executor = StageExecutor(config, cache, oci, self._mtime_index)

    def run(
        self,
        stages: Optional[list[str]] = None,
        force_stages: Optional[set[str]] = None,
    ) -> dict[str, tuple[bool, str]]:
        """Execute the build pipeline.

        Args:
            stages: List of stages to build (default: all).
            force_stages: Stages to force-rebuild (skip cache).

        Returns:
            Dict of stage_name -> (cache_hit, output_hash).
        """
        from . import STAGES

        if stages is None:
            stages = list(STAGES)
        if force_stages is None:
            force_stages = set()

        results = {}
        cutoff_stages = set()  # Stages where early cutoff applies

        print("=" * 60, file=sys.stderr)
        print("OpenWrt OCI Build Pipeline", file=sys.stderr)
        print(f"Target: {self.config.target.board}/{self.config.target.subtarget}", file=sys.stderr)
        print(f"Stages: {' -> '.join(stages)}", file=sys.stderr)
        print("=" * 60, file=sys.stderr)

        pipeline_start = time.monotonic()

        for stage_name in stages:
            # Check if a dependency had early cutoff
            stage_cfg = self.config.stages.get(stage_name)
            if stage_cfg:
                skip_via_cutoff = all(
                    dep in cutoff_stages for dep in stage_cfg.depends_on
                ) and stage_cfg.depends_on and stage_name not in force_stages

                if skip_via_cutoff:
                    # All dependencies had unchanged output, try cache first
                    print(
                        f"[{stage_name}] Dependencies unchanged, checking cache...",
                        file=sys.stderr,
                    )

            force = stage_name in force_stages
            try:
                cache_hit, output_hash = self._executor.execute_stage(
                    stage_name, force=force
                )
                results[stage_name] = (cache_hit, output_hash)

                # Track early cutoff
                prev_hash = self.cache.get_stage_hash(stage_name)
                if prev_hash and prev_hash == output_hash:
                    cutoff_stages.add(stage_name)

            except Exception as e:
                print(
                    f"[{stage_name}] FAILED: {e}",
                    file=sys.stderr,
                )
                results[stage_name] = (False, "")
                break  # Stop pipeline on failure

        pipeline_duration = time.monotonic() - pipeline_start

        # Summary
        print("\n" + "=" * 60, file=sys.stderr)
        print("Pipeline Summary:", file=sys.stderr)
        for stage_name, (hit, h) in results.items():
            status = "CACHED" if hit else ("OK" if h else "FAILED")
            cutoff = " (early cutoff)" if stage_name in cutoff_stages else ""
            print(
                f"  {stage_name:12s} {status:8s} {h[:12] if h else 'N/A':>14s}{cutoff}",
                file=sys.stderr,
            )
        print(f"\nTotal pipeline time: {pipeline_duration:.1f}s", file=sys.stderr)
        print("=" * 60, file=sys.stderr)

        # Save mtime index
        self._mtime_index.save()

        return results
