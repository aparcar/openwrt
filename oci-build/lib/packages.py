# SPDX-License-Identifier: GPL-2.0-only
"""
Incremental package builder (Alpine-style).

Each package is an independent build unit with its own:
- Build identity (hash of source + deps + config)
- Cached output in the CAS
- OCI layer containing its installed files

Packages are built in parallel waves determined by the dependency graph.
The early cutoff optimization skips rebuilding dependents when a
package's output is identical to its cached version.

Workflow:
  1. Scan package tree for Makefiles → dependency graph
  2. Compute build identities for all packages
  3. Determine rebuild set (changed + reverse deps)
  4. Sort rebuild set into parallel waves
  5. Execute waves, building packages in parallel
  6. Store outputs in CAS, update action cache
"""

import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .cache import BuildCache, OutputManifest
from .config import BuildConfig
from .graph import DependencyGraph, PackageNode, parse_openwrt_package_makefile
from .hasher import BuildIdentity, MerkleTree, MtimeIndex, hash_file, hash_string
from .oci import OCIRuntime, ContainerRun


@dataclass
class PackageBuildResult:
    """Result of building a single package."""

    name: str
    cache_hit: bool = False
    success: bool = False
    output_hash: str = ""
    build_identity: str = ""
    duration_seconds: float = 0.0
    error: str = ""
    early_cutoff: bool = False


class PackageScanner:
    """Scans the OpenWrt package tree and builds the dependency graph."""

    def __init__(self, topdir: str, mtime_index: Optional[MtimeIndex] = None):
        self.topdir = topdir
        self._mtime_index = mtime_index

    def scan(self, package_dirs: Optional[list[str]] = None) -> DependencyGraph:
        """Scan package directories and build dependency graph.

        First tries the pre-generated .packageinfo (fast path), then
        falls back to scanning individual Makefiles.
        """
        # Fast path: use pre-generated packageinfo
        packageinfo = os.path.join(self.topdir, "tmp", ".packageinfo")
        if os.path.exists(packageinfo):
            from .graph import parse_openwrt_packageinfo
            graph = parse_openwrt_packageinfo(packageinfo)
            if graph.nodes:
                return graph

        # Slow path: scan Makefiles directly
        if package_dirs is None:
            package_dirs = ["package"]

        graph = DependencyGraph()
        for pkg_dir in package_dirs:
            full_dir = os.path.join(self.topdir, pkg_dir)
            self._scan_dir(full_dir, graph)

        return graph

    def _scan_dir(self, directory: str, graph: DependencyGraph) -> None:
        """Recursively scan for package Makefiles."""
        if not os.path.isdir(directory):
            return

        makefile = os.path.join(directory, "Makefile")
        if os.path.isfile(makefile):
            node = parse_openwrt_package_makefile(makefile)
            if node:
                node.source_dir = directory
                graph.add_node(node)
            return  # Don't recurse into package dirs

        try:
            for entry in os.scandir(directory):
                if entry.is_dir() and not entry.name.startswith("."):
                    self._scan_dir(entry.path, graph)
        except PermissionError:
            pass


class IncrementalPackageBuilder:
    """Builds packages incrementally with caching and parallelism.

    Key innovation: combines Merkle-tree change detection with
    content-addressable caching and early cutoff optimization.

    Build flow for each package:
      1. Compute Merkle hash of source directory
      2. Compute build identity = hash(source + dep_outputs + config)
      3. Check action cache → if hit, skip build
      4. On miss: build in OCI container
      5. Hash output → check for early cutoff
      6. Store in CAS, update action cache
    """

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
        self._output_hashes: dict[str, str] = {}  # pkg_name -> output_hash
        self._build_identities: dict[str, str] = {}  # pkg_name -> identity_hash

    def build_all(
        self,
        graph: DependencyGraph,
        force_packages: Optional[set[str]] = None,
        max_workers: Optional[int] = None,
    ) -> dict[str, PackageBuildResult]:
        """Build all packages in the graph incrementally.

        Args:
            graph: Package dependency graph.
            force_packages: Packages to force-rebuild.
            max_workers: Max parallel build workers.

        Returns:
            Dict of package_name -> PackageBuildResult.
        """
        if force_packages is None:
            force_packages = set()
        if max_workers is None:
            max_workers = self.config.parallel_jobs or os.cpu_count() or 1

        results: dict[str, PackageBuildResult] = {}

        # Phase 1: Compute all build identities
        print("[packages] Computing build identities...", file=sys.stderr)
        identities = self._compute_all_identities(graph)

        # Phase 2: Determine what needs rebuilding
        changed = set()
        for name, identity in identities.items():
            if name in force_packages:
                changed.add(name)
                continue
            cached = self.cache.check_action(identity)
            if cached is None:
                changed.add(name)
            else:
                # Cache hit
                results[name] = PackageBuildResult(
                    name=name,
                    cache_hit=True,
                    success=True,
                    output_hash=cached.content_hash(),
                    build_identity=identity,
                )
                self._output_hashes[name] = cached.content_hash()

        # Compute rebuild set (changed + reverse deps)
        rebuild_set = graph.rebuild_set(changed)

        # But apply early cutoff: if a dependency was rebuilt with same output,
        # its dependents don't need rebuilding
        # (This is refined during build as we learn actual outputs)

        if not rebuild_set:
            print("[packages] Everything up to date!", file=sys.stderr)
            return results

        print(
            f"[packages] {len(rebuild_set)} packages to build "
            f"({len(changed)} changed, {len(rebuild_set) - len(changed)} dependents)",
            file=sys.stderr,
        )

        # Phase 3: Build in topological waves
        subgraph = graph.filter_subgraph(rebuild_set)
        waves = subgraph.topological_waves()

        print(
            f"[packages] {len(waves)} build waves, max parallelism: {max(len(w) for w in waves)}",
            file=sys.stderr,
        )

        for wave_idx, wave in enumerate(waves):
            wave_start = time.monotonic()
            wave_results = self._build_wave(
                wave, graph, identities, max_workers
            )

            # Process results and check for early cutoff
            failed = False
            cutoff_count = 0
            for name, result in wave_results.items():
                results[name] = result
                if result.success:
                    self._output_hashes[name] = result.output_hash

                    # Early cutoff: if output unchanged, remove dependents from rebuild
                    prev_hash = self.cache.get_stage_hash(f"pkg:{name}")
                    if prev_hash and prev_hash == result.output_hash:
                        result.early_cutoff = True
                        cutoff_count += 1
                        # Remove dependents from future waves if they haven't changed
                        for rdep in graph.reverse_dependencies(name):
                            if rdep not in changed and rdep in rebuild_set:
                                rebuild_set.discard(rdep)

                    self.cache.set_stage_hash(f"pkg:{name}", result.output_hash)
                else:
                    failed = True

            wave_duration = time.monotonic() - wave_start
            status = (
                f"wave {wave_idx + 1}/{len(waves)}: "
                f"{len(wave)} packages in {wave_duration:.1f}s"
            )
            if cutoff_count:
                status += f" ({cutoff_count} early cutoff)"
            print(f"[packages] {status}", file=sys.stderr)

            if failed:
                print("[packages] Stopping due to build failure", file=sys.stderr)
                break

        return results

    def _compute_all_identities(
        self, graph: DependencyGraph
    ) -> dict[str, str]:
        """Compute build identities for all packages in the graph."""
        identities = {}
        mtime_cache = self.mtime_index.as_cache_dict()

        # Process in topological order so dep hashes are available
        try:
            waves = graph.topological_waves()
        except ValueError as e:
            print(f"[packages] Warning: {e}", file=sys.stderr)
            # Fall back to individual computation
            for name, node in graph.nodes.items():
                identities[name] = self._compute_package_identity(
                    node, mtime_cache, identities
                )
            return identities

        for wave in waves:
            for name in wave:
                node = graph.get_node(name)
                if node:
                    identities[name] = self._compute_package_identity(
                        node, mtime_cache, identities
                    )

        self._build_identities = identities
        self.mtime_index.save()
        return identities

    def _compute_package_identity(
        self,
        node: PackageNode,
        mtime_cache: dict,
        dep_identities: dict[str, str],
    ) -> str:
        """Compute build identity for a single package."""
        bid = BuildIdentity()

        # Source directory hash
        if node.source_dir and os.path.isdir(node.source_dir):
            tree = MerkleTree(mtime_cache)
            tree.compute(
                node.source_dir,
                exclude={".git", "stamps", ".built", ".configured", ".prepared"},
            )
            bid.add_string("source", tree.root_hash or "")

        # Dependency output hashes
        for dep in node.all_deps:
            dep_id = dep_identities.get(dep, "")
            if dep_id:
                bid.add_dep(dep, dep_id)

        # Target architecture
        bid.add_string("arch", self.config.target.arch)
        bid.add_string("board", self.config.target.board)

        # .config hash (package-relevant options)
        dotconfig = os.path.join(self.config.topdir, ".config")
        if os.path.exists(dotconfig):
            bid.add_file("dotconfig", dotconfig)

        return bid.hexdigest()

    def _build_wave(
        self,
        wave: list[str],
        graph: DependencyGraph,
        identities: dict[str, str],
        max_workers: int,
    ) -> dict[str, PackageBuildResult]:
        """Build a wave of packages in parallel."""
        results = {}

        if len(wave) == 1:
            # Single package, no need for thread pool
            name = wave[0]
            results[name] = self._build_package(name, graph, identities)
            return results

        # Parallel build using thread pool
        actual_workers = min(max_workers, len(wave))
        with ThreadPoolExecutor(max_workers=actual_workers) as executor:
            futures = {
                executor.submit(
                    self._build_package, name, graph, identities
                ): name
                for name in wave
            }

            for future in as_completed(futures):
                name = futures[future]
                try:
                    results[name] = future.result()
                except Exception as e:
                    results[name] = PackageBuildResult(
                        name=name,
                        success=False,
                        error=str(e),
                    )

        return results

    def _build_package(
        self,
        name: str,
        graph: DependencyGraph,
        identities: dict[str, str],
    ) -> PackageBuildResult:
        """Build a single package inside an OCI container."""
        start = time.monotonic()
        identity = identities.get(name, "")

        try:
            # Check cache one more time (may have been populated by another wave)
            cached = self.cache.check_action(identity)
            if cached:
                return PackageBuildResult(
                    name=name,
                    cache_hit=True,
                    success=True,
                    output_hash=cached.content_hash(),
                    build_identity=identity,
                )

            node = graph.get_node(name)
            if not node:
                return PackageBuildResult(
                    name=name,
                    success=False,
                    error=f"Package {name} not found in graph",
                )

            # Determine the package's relative path for make
            pkg_path = ""
            if node.source_dir:
                pkg_path = os.path.relpath(node.source_dir, self.config.topdir)

            if not pkg_path:
                return PackageBuildResult(
                    name=name,
                    success=False,
                    error=f"Cannot determine build path for {name}",
                )

            # Build inside container
            image_tag = "owrt-build-packages:latest"
            jobs = self.config.parallel_jobs or os.cpu_count() or 1

            build_cmd = f"""\
make -j{jobs} {pkg_path}/compile V=s 2>&1 | tail -5
"""
            spec = ContainerRun(
                image=image_tag,
                command=["sh", "-ec", build_cmd],
                bind_mounts=[
                    (self.config.topdir, "/src", "rw"),
                    (self.config.dl_dir, "/dl", "rw"),
                ],
                cache_mounts=[
                    ("ccache", "/var/cache/ccache"),
                    ("staging-packages", "/src/staging_dir"),
                    ("build-packages", "/src/build_dir"),
                ],
                env={
                    "TOPDIR": "/src",
                    "DL_DIR": "/dl",
                    "CCACHE_DIR": "/var/cache/ccache",
                    "CCACHE_MAXSIZE": f"{self.config.cache.ccache_size_gb}G",
                },
                workdir="/src",
                timeout=self.config.stages.get(
                    "packages", type("", (), {"timeout_minutes": 60})()
                ).timeout_minutes * 60,
            )

            result = self.oci.run_container(spec)
            duration = time.monotonic() - start

            if result.returncode != 0:
                return PackageBuildResult(
                    name=name,
                    success=False,
                    build_identity=identity,
                    duration_seconds=duration,
                    error=f"Build failed with exit code {result.returncode}",
                )

            # Compute output hash from built artifacts
            output_hash = hash_string(f"{name}:{identity}:{time.time()}")

            # Store in cache
            # Collect package output files (ipk/apk)
            pkg_output_dir = os.path.join(
                self.config.output_dir, "packages", self.config.target.arch_packages
            )
            output_files = {}
            if os.path.isdir(pkg_output_dir):
                for f in os.listdir(pkg_output_dir):
                    if f.startswith(name):
                        fpath = os.path.join(pkg_output_dir, f)
                        output_files[f] = fpath

            if output_files:
                manifest = self.cache.store_action(
                    build_identity=identity,
                    output_files=output_files,
                    stage="packages",
                    package=name,
                    duration=duration,
                )
                output_hash = manifest.content_hash()

            return PackageBuildResult(
                name=name,
                cache_hit=False,
                success=True,
                output_hash=output_hash,
                build_identity=identity,
                duration_seconds=duration,
            )

        except Exception as e:
            duration = time.monotonic() - start
            return PackageBuildResult(
                name=name,
                success=False,
                build_identity=identity,
                duration_seconds=duration,
                error=str(e),
            )


def build_packages_incremental(
    config: BuildConfig,
    cache: BuildCache,
    oci: OCIRuntime,
    packages: Optional[list[str]] = None,
    force: Optional[set[str]] = None,
) -> dict[str, PackageBuildResult]:
    """High-level entry point for incremental package building.

    Args:
        config: Build configuration.
        cache: Build cache.
        oci: Container runtime.
        packages: Specific packages to build (None = all).
        force: Packages to force-rebuild.

    Returns:
        Dict of package_name -> PackageBuildResult.
    """
    mtime_path = os.path.join(config.cache.root, "mtime.json")
    mtime_index = MtimeIndex(mtime_path).load()

    # Scan packages
    scanner = PackageScanner(config.topdir, mtime_index)
    graph = scanner.scan()

    if packages:
        # Build only specified packages + their dependencies
        keep = set()
        for pkg in packages:
            keep.add(pkg)
            keep.update(graph.transitive_deps(pkg))
        graph = graph.filter_subgraph(keep)

    # Build incrementally
    builder = IncrementalPackageBuilder(config, cache, oci, mtime_index)
    results = builder.build_all(graph, force_packages=force)

    # Summary
    cached = sum(1 for r in results.values() if r.cache_hit)
    built = sum(1 for r in results.values() if r.success and not r.cache_hit)
    failed = sum(1 for r in results.values() if not r.success)
    cutoff = sum(1 for r in results.values() if r.early_cutoff)

    print(
        f"\n[packages] Summary: {cached} cached, {built} built, "
        f"{failed} failed, {cutoff} early cutoff",
        file=sys.stderr,
    )

    return results
