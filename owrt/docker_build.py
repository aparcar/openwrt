"""
Docker image build management with content-based caching.

This module handles building the container hierarchy:
  owrt-base -> owrt-tools -> owrt-toolchain-<target>

Images are tagged with content hashes derived from:
- Dockerfile contents
- Tool definitions (tool.yaml files)
- Toolchain patches and configuration

This enables efficient caching - images are only rebuilt when
their inputs actually change.
"""

import hashlib
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple


@dataclass
class ImageInfo:
    """Information about a Docker image."""
    name: str
    tag: str
    content_hash: str
    exists: bool
    full_tag: str

    @property
    def image_ref(self) -> str:
        """Full image reference (name:tag)."""
        return f"{self.name}:{self.tag}"


class DockerImageBuilder:
    """
    Builds Docker images with content-based caching.

    Images are tagged with hashes of their inputs so that:
    - Unchanged inputs = same tag = no rebuild needed
    - Changed inputs = new tag = rebuild triggered
    """

    def __init__(
        self,
        project_dir: Path,
        registry: Optional[str] = None,
        verbose: bool = False,
    ):
        """
        Initialize the Docker image builder.

        Args:
            project_dir: Root directory of the project (contains docker/, owrt/, tools/)
            registry: Optional Docker registry prefix (e.g., 'ghcr.io/openwrt')
            verbose: Enable verbose output
        """
        self.project_dir = project_dir
        self.registry = registry
        self.verbose = verbose
        self.docker_dir = project_dir / 'docker'

        # Cache for computed hashes
        self._hash_cache: Dict[str, str] = {}

    def _log(self, msg: str):
        """Print message if verbose."""
        if self.verbose:
            print(msg)

    def _image_name(self, name: str) -> str:
        """Get full image name with optional registry prefix."""
        if self.registry:
            return f"{self.registry}/{name}"
        return name

    def _run_docker(self, args: List[str], check: bool = True) -> subprocess.CompletedProcess:
        """Run a Docker command."""
        cmd = ['docker'] + args
        self._log(f"  $ {' '.join(cmd)}")
        return subprocess.run(
            cmd,
            capture_output=not self.verbose,
            text=True,
            check=check,
        )

    def _image_exists(self, image_ref: str) -> bool:
        """Check if a Docker image exists locally."""
        result = self._run_docker(['image', 'inspect', image_ref], check=False)
        return result.returncode == 0

    def _hash_file(self, path: Path) -> str:
        """Compute SHA256 hash of a file."""
        h = hashlib.sha256()
        h.update(path.read_bytes())
        return h.hexdigest()

    def _hash_directory(self, path: Path, pattern: str = '*') -> str:
        """Compute hash of all files in a directory matching pattern."""
        h = hashlib.sha256()
        if path.exists():
            for f in sorted(path.rglob(pattern)):
                if f.is_file():
                    # Include relative path for position-sensitivity
                    rel_path = f.relative_to(path)
                    h.update(str(rel_path).encode())
                    h.update(f.read_bytes())
        return h.hexdigest()

    # =========================================================================
    # Base Image
    # =========================================================================

    def compute_base_hash(self) -> str:
        """
        Compute content hash for the base image.

        Includes:
        - Dockerfile.base contents
        """
        cache_key = 'base'
        if cache_key in self._hash_cache:
            return self._hash_cache[cache_key]

        h = hashlib.sha256()

        # Dockerfile.base
        dockerfile = self.docker_dir / 'Dockerfile.base'
        if dockerfile.exists():
            h.update(b'dockerfile.base:')
            h.update(dockerfile.read_bytes())

        result = h.hexdigest()[:12]
        self._hash_cache[cache_key] = result
        return result

    def get_base_image_info(self) -> ImageInfo:
        """Get information about the base image."""
        content_hash = self.compute_base_hash()
        name = self._image_name('owrt-base')
        tag = content_hash
        full_tag = f"{name}:{tag}"

        return ImageInfo(
            name=name,
            tag=tag,
            content_hash=content_hash,
            exists=self._image_exists(full_tag),
            full_tag=full_tag,
        )

    def build_base(self, force: bool = False) -> ImageInfo:
        """
        Build the base image if needed.

        Returns:
            ImageInfo for the base image
        """
        info = self.get_base_image_info()

        if info.exists and not force:
            print(f"  Base image up-to-date: {info.image_ref}")
            return info

        print(f"  Building base image: {info.image_ref}")

        self._run_docker([
            'build',
            '-f', str(self.docker_dir / 'Dockerfile.base'),
            '-t', info.image_ref,
            # Also tag as 'latest' for convenience
            '-t', f"{info.name}:latest",
            str(self.project_dir),
        ])

        info.exists = True
        return info

    # =========================================================================
    # Tools Image
    # =========================================================================

    def compute_tools_hash(self) -> str:
        """
        Compute content hash for the tools image.

        Includes:
        - Base image hash (cascading dependency)
        - Dockerfile.tools contents
        - All tool.yaml definitions
        - Tool build scripts (tool.py)
        """
        cache_key = 'tools'
        if cache_key in self._hash_cache:
            return self._hash_cache[cache_key]

        h = hashlib.sha256()

        # Include base hash for cascading rebuilds
        h.update(b'base:')
        h.update(self.compute_base_hash().encode())

        # Dockerfile.tools
        dockerfile = self.docker_dir / 'Dockerfile.tools'
        if dockerfile.exists():
            h.update(b'dockerfile.tools:')
            h.update(dockerfile.read_bytes())

        # Tool builder script
        tool_py = self.project_dir / 'owrt' / 'tool.py'
        if tool_py.exists():
            h.update(b'tool.py:')
            h.update(tool_py.read_bytes())

        # All tool definitions
        tools_dir = self.project_dir / 'owrt' / 'tools'
        if tools_dir.exists():
            for tool_dir in sorted(tools_dir.iterdir()):
                tool_yaml = tool_dir / 'tool.yaml'
                if tool_yaml.exists():
                    h.update(f'tool:{tool_dir.name}:'.encode())
                    h.update(tool_yaml.read_bytes())

        # OpenWrt tool patches (from tools/ directory)
        openwrt_tools = self.project_dir / 'tools'
        if openwrt_tools.exists():
            for patches_dir in sorted(openwrt_tools.rglob('patches')):
                if patches_dir.is_dir():
                    for patch in sorted(patches_dir.glob('*.patch')):
                        rel = patch.relative_to(openwrt_tools)
                        h.update(f'patch:{rel}:'.encode())
                        h.update(patch.read_bytes())

        result = h.hexdigest()[:12]
        self._hash_cache[cache_key] = result
        return result

    def get_tools_image_info(self) -> ImageInfo:
        """Get information about the tools image."""
        content_hash = self.compute_tools_hash()
        name = self._image_name('owrt-tools')
        tag = content_hash
        full_tag = f"{name}:{tag}"

        return ImageInfo(
            name=name,
            tag=tag,
            content_hash=content_hash,
            exists=self._image_exists(full_tag),
            full_tag=full_tag,
        )

    def build_tools(self, force: bool = False) -> ImageInfo:
        """
        Build the tools image if needed.

        Automatically builds base image first if needed.

        Returns:
            ImageInfo for the tools image
        """
        # Ensure base is built first
        base_info = self.build_base(force=force)

        info = self.get_tools_image_info()

        if info.exists and not force:
            print(f"  Tools image up-to-date: {info.image_ref}")
            return info

        print(f"  Building tools image: {info.image_ref}")

        self._run_docker([
            'build',
            '-f', str(self.docker_dir / 'Dockerfile.tools'),
            '--build-arg', f'BASE_IMAGE={base_info.image_ref}',
            '-t', info.image_ref,
            '-t', f"{info.name}:latest",
            str(self.project_dir),
        ])

        info.exists = True
        return info

    # =========================================================================
    # Toolchain Image
    # =========================================================================

    def compute_toolchain_hash(self, target: str) -> str:
        """
        Compute content hash for a toolchain image.

        Includes:
        - Tools image hash (cascading dependency)
        - Dockerfile.toolchain contents
        - Target configuration
        - Toolchain builder script
        - Toolchain patches (binutils, gcc, musl)
        """
        cache_key = f'toolchain:{target}'
        if cache_key in self._hash_cache:
            return self._hash_cache[cache_key]

        h = hashlib.sha256()

        # Include tools hash for cascading rebuilds
        h.update(b'tools:')
        h.update(self.compute_tools_hash().encode())

        # Target name
        h.update(b'target:')
        h.update(target.encode())

        # Dockerfile.toolchain
        dockerfile = self.docker_dir / 'Dockerfile.toolchain'
        if dockerfile.exists():
            h.update(b'dockerfile.toolchain:')
            h.update(dockerfile.read_bytes())

        # Toolchain builder script
        toolchain_py = self.project_dir / 'owrt' / 'toolchain.py'
        if toolchain_py.exists():
            h.update(b'toolchain.py:')
            h.update(toolchain_py.read_bytes())

        # Target configuration
        target_yaml = self.project_dir / 'targets' / target / 'target.yaml'
        if target_yaml.exists():
            h.update(b'target.yaml:')
            h.update(target_yaml.read_bytes())

        # Toolchain patches
        toolchain_dir = self.project_dir / 'toolchain'
        if toolchain_dir.exists():
            for component in ['binutils', 'gcc', 'musl']:
                comp_dir = toolchain_dir / component
                if comp_dir.exists():
                    for patches_dir in sorted(comp_dir.rglob('patches*')):
                        if patches_dir.is_dir():
                            for patch in sorted(patches_dir.glob('*.patch')):
                                rel = patch.relative_to(toolchain_dir)
                                h.update(f'toolchain-patch:{rel}:'.encode())
                                h.update(patch.read_bytes())

        result = h.hexdigest()[:12]
        self._hash_cache[cache_key] = result
        return result

    def get_toolchain_image_info(self, target: str) -> ImageInfo:
        """Get information about a toolchain image."""
        content_hash = self.compute_toolchain_hash(target)
        # Convert target to valid Docker tag (x86/64 -> x86-64)
        tag_target = target.replace('/', '-')
        name = self._image_name(f'owrt-toolchain-{tag_target}')
        tag = content_hash
        full_tag = f"{name}:{tag}"

        return ImageInfo(
            name=name,
            tag=tag,
            content_hash=content_hash,
            exists=self._image_exists(full_tag),
            full_tag=full_tag,
        )

    def build_toolchain(self, target: str, force: bool = False) -> ImageInfo:
        """
        Build a toolchain image for a specific target.

        Automatically builds tools image first if needed.

        Args:
            target: Target name (e.g., 'mediatek/filogic', 'x86/64')
            force: Force rebuild even if image exists

        Returns:
            ImageInfo for the toolchain image
        """
        # Ensure tools is built first
        tools_info = self.build_tools(force=force)

        info = self.get_toolchain_image_info(target)

        if info.exists and not force:
            print(f"  Toolchain image up-to-date: {info.image_ref}")
            return info

        print(f"  Building toolchain image: {info.image_ref}")
        print(f"    Target: {target}")

        self._run_docker([
            'build',
            '-f', str(self.docker_dir / 'Dockerfile.toolchain'),
            '--build-arg', f'TOOLS_IMAGE={tools_info.image_ref}',
            '--build-arg', f'TARGET={target}',
            '-t', info.image_ref,
            '-t', f"{info.name}:latest",
            str(self.project_dir),
        ])

        info.exists = True
        return info

    # =========================================================================
    # Status and Utilities
    # =========================================================================

    def status(self) -> Dict[str, ImageInfo]:
        """
        Get status of all images.

        Returns:
            Dictionary mapping image type to ImageInfo
        """
        result = {
            'base': self.get_base_image_info(),
            'tools': self.get_tools_image_info(),
        }

        # Check for existing toolchain images
        try:
            proc = self._run_docker(
                ['images', '--format', '{{.Repository}}:{{.Tag}}', self._image_name('owrt-toolchain-*')],
                check=False,
            )
            if proc.returncode == 0 and proc.stdout:
                for line in proc.stdout.strip().split('\n'):
                    if line and ':' in line:
                        # Extract target from image name
                        name = line.split(':')[0]
                        if 'owrt-toolchain-' in name:
                            target_slug = name.split('owrt-toolchain-')[-1]
                            # Convert back to target format (x86-64 -> x86/64)
                            target = target_slug.replace('-', '/', 1)
                            result[f'toolchain:{target}'] = self.get_toolchain_image_info(target)
        except Exception:
            pass

        return result

    def clean(self, keep_base: bool = False):
        """
        Remove all owrt images.

        Args:
            keep_base: If True, keep the base image
        """
        patterns = ['owrt-tools', 'owrt-toolchain-*']
        if not keep_base:
            patterns.insert(0, 'owrt-base')

        for pattern in patterns:
            name = self._image_name(pattern)
            # List images matching pattern
            proc = self._run_docker(
                ['images', '--format', '{{.Repository}}:{{.Tag}}', name],
                check=False,
            )
            if proc.returncode == 0 and proc.stdout:
                for image in proc.stdout.strip().split('\n'):
                    if image:
                        print(f"  Removing: {image}")
                        self._run_docker(['rmi', '-f', image], check=False)

    def export_hashes(self) -> Dict[str, str]:
        """
        Export all computed hashes as a dictionary.

        Useful for CI to check if images need rebuilding.
        """
        return {
            'base': self.compute_base_hash(),
            'tools': self.compute_tools_hash(),
        }
