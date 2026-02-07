# SPDX-License-Identifier: GPL-2.0-only
"""
OCI container runtime abstraction.

Supports podman, docker, and buildah as backends. Provides:
- Image building with layer caching
- Container execution with bind mounts and cache mounts
- Image tagging and registry push/pull
- Layer inspection and reuse

Design choices:
- Prefer podman (daemonless, rootless) over docker
- Use buildah for fine-grained layer control
- Cache mounts for ccache, download dirs, package caches
- Reproducible builds via --timestamp=0 and sorted layer content
"""

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ContainerRun:
    """Specification for running a build step inside a container."""

    image: str
    command: list[str]
    bind_mounts: list[tuple[str, str, str]] = field(
        default_factory=list
    )  # (host_path, container_path, mode)
    cache_mounts: list[tuple[str, str]] = field(
        default_factory=list
    )  # (cache_id, container_path)
    env: dict[str, str] = field(default_factory=dict)
    workdir: str = "/build"
    user: str = ""
    network: str = "host"
    memory_limit: str = ""
    cpu_limit: str = ""
    timeout: int = 7200  # seconds


@dataclass
class ImageBuild:
    """Specification for building an OCI image."""

    containerfile: str
    context_dir: str
    tag: str
    build_args: dict[str, str] = field(default_factory=dict)
    cache_from: list[str] = field(default_factory=list)
    target_stage: str = ""
    labels: dict[str, str] = field(default_factory=dict)
    timestamp: Optional[int] = 0  # 0 = epoch for reproducibility


class OCIRuntime:
    """Abstraction over OCI container runtimes (podman/docker/buildah)."""

    def __init__(
        self,
        runtime: str = "podman",
        verbose: bool = False,
        dry_run: bool = False,
    ):
        self.runtime = self._detect_runtime(runtime)
        self.verbose = verbose
        self.dry_run = dry_run
        self._buildah = self._find_executable("buildah")

    @staticmethod
    def _detect_runtime(preferred: str) -> str:
        """Find available container runtime."""
        for rt in [preferred, "podman", "docker"]:
            try:
                subprocess.run(
                    [rt, "version"],
                    capture_output=True,
                    timeout=10,
                )
                return rt
            except (FileNotFoundError, subprocess.TimeoutExpired):
                continue
        raise RuntimeError(
            "No container runtime found. Install podman or docker."
        )

    @staticmethod
    def _find_executable(name: str) -> Optional[str]:
        """Check if an executable is available."""
        try:
            subprocess.run(
                [name, "--version"], capture_output=True, timeout=5
            )
            return name
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return None

    def _run_cmd(
        self,
        cmd: list[str],
        capture: bool = False,
        timeout: int = 7200,
        env: Optional[dict] = None,
    ) -> subprocess.CompletedProcess:
        """Execute a command, optionally capturing output."""
        if self.verbose:
            print(f"  $ {' '.join(cmd)}", file=sys.stderr)

        if self.dry_run:
            return subprocess.CompletedProcess(cmd, 0, b"", b"")

        kwargs = {
            "timeout": timeout,
            "env": {**os.environ, **(env or {})},
        }
        if capture:
            kwargs["capture_output"] = True
        else:
            kwargs["stdout"] = sys.stderr if self.verbose else subprocess.DEVNULL
            kwargs["stderr"] = subprocess.STDOUT

        return subprocess.run(cmd, **kwargs)

    def build_image(self, spec: ImageBuild) -> str:
        """Build an OCI image from a Containerfile.

        Returns the image ID.
        """
        cmd = [self.runtime, "build"]

        # Reproducibility: fixed timestamp
        if spec.timestamp is not None and self.runtime == "podman":
            cmd.extend(["--timestamp", str(spec.timestamp)])

        # Build arguments
        for key, value in spec.build_args.items():
            cmd.extend(["--build-arg", f"{key}={value}"])

        # Cache sources
        for cache_ref in spec.cache_from:
            cmd.extend(["--cache-from", cache_ref])

        # Target stage for multi-stage builds
        if spec.target_stage:
            cmd.extend(["--target", spec.target_stage])

        # Labels
        for key, value in spec.labels.items():
            cmd.extend(["--label", f"{key}={value}"])

        # Tag and context
        cmd.extend([
            "--layers",
            "--force-rm",
            "-f", spec.containerfile,
            "-t", spec.tag,
            spec.context_dir,
        ])

        result = self._run_cmd(cmd, timeout=14400)  # 4 hour timeout for builds
        if result.returncode != 0:
            raise RuntimeError(f"Image build failed: {spec.tag}")

        return self.image_id(spec.tag)

    def run_container(self, spec: ContainerRun) -> subprocess.CompletedProcess:
        """Run a command inside a container.

        Uses bind mounts for source trees and cache mounts for
        persistent caches (ccache, downloads, etc).
        """
        cmd = [self.runtime, "run", "--rm"]

        # Working directory
        cmd.extend(["-w", spec.workdir])

        # Network mode
        cmd.extend(["--network", spec.network])

        # Resource limits
        if spec.memory_limit:
            cmd.extend(["--memory", spec.memory_limit])
        if spec.cpu_limit:
            cmd.extend(["--cpus", spec.cpu_limit])

        # User
        if spec.user:
            cmd.extend(["--user", spec.user])

        # Environment variables
        for key, value in spec.env.items():
            cmd.extend(["-e", f"{key}={value}"])

        # Bind mounts (source tree, output dirs)
        for host_path, container_path, mode in spec.bind_mounts:
            mount_spec = f"type=bind,src={host_path},dst={container_path}"
            if mode:
                mount_spec += f",{mode}"
            cmd.extend(["--mount", mount_spec])

        # Cache mounts (persistent across builds, not in final image)
        for cache_id, container_path in spec.cache_mounts:
            # Named volumes act as persistent cache mounts
            cmd.extend([
                "--mount",
                f"type=volume,src=owrt-cache-{cache_id},dst={container_path}",
            ])

        # Image and command
        cmd.append(spec.image)
        cmd.extend(spec.command)

        return self._run_cmd(cmd, timeout=spec.timeout)

    def image_exists(self, tag: str) -> bool:
        """Check if an image exists locally."""
        result = self._run_cmd(
            [self.runtime, "image", "exists", tag],
            capture=True,
            timeout=10,
        )
        return result.returncode == 0

    def image_id(self, tag: str) -> str:
        """Get the image ID for a tag."""
        result = self._run_cmd(
            [self.runtime, "image", "inspect", tag, "--format", "{{.Id}}"],
            capture=True,
            timeout=10,
        )
        if result.returncode != 0:
            return ""
        return result.stdout.decode().strip()

    def image_layers(self, tag: str) -> list[str]:
        """Get the layer digests for an image."""
        result = self._run_cmd(
            [self.runtime, "image", "inspect", tag],
            capture=True,
            timeout=10,
        )
        if result.returncode != 0:
            return []
        try:
            data = json.loads(result.stdout)
            if isinstance(data, list):
                data = data[0]
            return data.get("RootFS", {}).get("Layers", [])
        except (json.JSONDecodeError, IndexError):
            return []

    def tag_image(self, source: str, target: str) -> None:
        """Add a tag to an image."""
        self._run_cmd(
            [self.runtime, "tag", source, target], timeout=30
        )

    def push_image(self, tag: str, compression: str = "zstd") -> None:
        """Push an image to a registry."""
        cmd = [self.runtime, "push"]
        if self.runtime == "podman":
            cmd.extend(["--compression-format", compression])
        cmd.append(tag)
        self._run_cmd(cmd, timeout=3600)

    def pull_image(self, tag: str) -> bool:
        """Pull an image from a registry. Returns True on success."""
        result = self._run_cmd(
            [self.runtime, "pull", tag], capture=True, timeout=3600
        )
        return result.returncode == 0

    def remove_image(self, tag: str) -> None:
        """Remove a local image."""
        self._run_cmd(
            [self.runtime, "rmi", "-f", tag], capture=True, timeout=30
        )

    def create_layer_from_dir(
        self, base_image: str, directory: str, tag: str, comment: str = ""
    ) -> str:
        """Create a new image layer from a directory using buildah.

        This enables per-package OCI layers: each package's installed files
        become a layer that can be stacked to compose the final image.

        Falls back to Containerfile-based approach if buildah unavailable.
        """
        if not self._buildah:
            return self._create_layer_via_containerfile(
                base_image, directory, tag, comment
            )

        # Use buildah for fine-grained layer control
        container = None
        try:
            # Create working container from base
            result = self._run_cmd(
                [self._buildah, "from", base_image],
                capture=True,
                timeout=60,
            )
            container = result.stdout.decode().strip()

            # Copy directory contents into container
            self._run_cmd(
                [self._buildah, "copy", container, directory, "/"],
                timeout=300,
            )

            # Commit as new image
            commit_cmd = [self._buildah, "commit", "--rm"]
            if comment:
                commit_cmd.extend(["--message", comment])
            # Reproducible timestamp
            commit_cmd.extend(["--timestamp", "0"])
            commit_cmd.extend([container, tag])

            self._run_cmd(commit_cmd, timeout=300)
            container = None  # --rm already removed it
            return self.image_id(tag)

        finally:
            if container:
                self._run_cmd(
                    [self._buildah, "rm", container],
                    capture=True,
                    timeout=30,
                )

    def _create_layer_via_containerfile(
        self, base_image: str, directory: str, tag: str, comment: str
    ) -> str:
        """Fallback: create a layer using a generated Containerfile."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            cf_path = os.path.join(tmpdir, "Containerfile")
            with open(cf_path, "w") as f:
                f.write(f"FROM {base_image}\n")
                f.write(f"COPY . /\n")
                if comment:
                    f.write(f'LABEL description="{comment}"\n')

            spec = ImageBuild(
                containerfile=cf_path,
                context_dir=directory,
                tag=tag,
            )
            return self.build_image(spec)

    def export_cache(
        self, tag: str, registry: str, compression: str = "zstd"
    ) -> None:
        """Export image as cache to a registry (max mode for all layers)."""
        if self.runtime == "docker":
            cmd = [
                self.runtime, "buildx", "build",
                "--cache-to",
                f"type=registry,ref={registry},mode=max,compression={compression}",
                "--push",
                "-t", tag,
            ]
        else:
            # Podman: push directly, layers are cached in registry
            self.push_image(tag, compression=compression)

    def import_cache(self, registry: str) -> bool:
        """Import cache from a registry."""
        return self.pull_image(registry)

    def cleanup_old_images(self, prefix: str, keep_count: int = 5) -> None:
        """Remove old images matching a prefix, keeping the most recent."""
        result = self._run_cmd(
            [
                self.runtime, "images",
                "--format", "{{.Repository}}:{{.Tag}} {{.CreatedAt}}",
                "--filter", f"reference={prefix}*",
                "--sort", "created",
            ],
            capture=True,
            timeout=30,
        )
        if result.returncode != 0:
            return

        lines = result.stdout.decode().strip().split("\n")
        if len(lines) <= keep_count:
            return

        for line in lines[:-keep_count]:
            tag = line.split()[0]
            self.remove_image(tag)
