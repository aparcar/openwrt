"""
Build configuration - loads and manages user build settings.

Handles:
- Loading config.yaml user configuration
- Merging with target defaults
- Kernel config overrides
- Package selection
"""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Any, Set

import yaml

from .config import Config


def parse_size(size_str: str) -> int:
    """Parse size string with K/M/G suffix to bytes."""
    if not size_str:
        return 0

    size_str = size_str.strip().upper()
    multipliers = {'K': 1024, 'M': 1024**2, 'G': 1024**3}

    if size_str[-1] in multipliers:
        return int(size_str[:-1]) * multipliers[size_str[-1]]
    return int(size_str)


@dataclass
class ImageConfig:
    """Image generation settings."""
    rootfs_size: int = 100 * 1024 * 1024  # 100M default
    kernel_size: int = 16 * 1024 * 1024   # 16M default
    compression: str = 'gzip'
    rootfs_type: str = 'squashfs'

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ImageConfig':
        """Create from config dict."""
        return cls(
            rootfs_size=parse_size(data.get('rootfs_size', '100M')),
            kernel_size=parse_size(data.get('kernel_size', '16M')),
            compression=data.get('compression', 'gzip'),
            rootfs_type=data.get('rootfs_type', 'squashfs'),
        )


@dataclass
class BuildOptions:
    """Build process settings."""
    jobs: Optional[int] = None  # None = auto-detect
    verbose: bool = False
    continue_on_error: bool = False
    dl_dir: Optional[Path] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'BuildOptions':
        """Create from config dict."""
        dl_dir = data.get('dl_dir')
        return cls(
            jobs=data.get('jobs'),
            verbose=data.get('verbose', False),
            continue_on_error=data.get('continue_on_error', False),
            dl_dir=Path(dl_dir) if dl_dir else None,
        )


@dataclass
class BuildConfig:
    """Complete build configuration."""

    # Target selection
    target_name: str
    profile_name: str = 'generic'

    # Package selection
    packages: List[str] = field(default_factory=list)
    exclude_packages: List[str] = field(default_factory=list)

    # Kernel config overrides
    kernel_config: Dict[str, str] = field(default_factory=dict)

    # Image settings
    image: ImageConfig = field(default_factory=ImageConfig)

    # Build options
    build: BuildOptions = field(default_factory=BuildOptions)

    # Source overrides for development
    source_overrides: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    # Loaded target config (populated by load_target)
    _target_config: Optional[Config] = field(default=None, repr=False)

    @classmethod
    def load(cls, config_path: Optional[Path] = None) -> 'BuildConfig':
        """Load build configuration from file.

        Args:
            config_path: Path to config.yaml. If None, searches:
                1. ./config.yaml
                2. POC_DIR/config.yaml

        Returns:
            BuildConfig instance

        Raises:
            FileNotFoundError: If no config file found
            ValueError: If config is invalid
        """
        # Find config file
        if config_path is None:
            poc_dir = Path(__file__).parent.parent
            search_paths = [
                Path.cwd() / 'config.yaml',
                poc_dir / 'config.yaml',
            ]
            for path in search_paths:
                if path.exists():
                    config_path = path
                    break
            else:
                raise FileNotFoundError(
                    "No config.yaml found. Create one from config.yaml.example"
                )

        # Load YAML
        with open(config_path) as f:
            data = yaml.safe_load(f) or {}

        # Validate required fields
        if 'target' not in data:
            raise ValueError("config.yaml must specify 'target'")

        # Parse kernel config (normalize CONFIG_ prefix)
        kernel_config = {}
        for key, value in data.get('kernel', {}).items():
            # Ensure CONFIG_ prefix
            if not key.startswith('CONFIG_'):
                key = f'CONFIG_{key}'
            kernel_config[key] = str(value)

        # Create instance
        config = cls(
            target_name=data['target'],
            profile_name=data.get('profile', 'generic'),
            packages=data.get('packages', []),
            exclude_packages=data.get('exclude_packages', []),
            kernel_config=kernel_config,
            image=ImageConfig.from_dict(data.get('image', {})),
            build=BuildOptions.from_dict(data.get('build', {})),
            source_overrides=data.get('source_overrides', {}),
        )

        return config

    @classmethod
    def from_args(
        cls,
        target: str,
        profile: str = 'generic',
        packages: Optional[List[str]] = None,
        verbose: bool = False,
    ) -> 'BuildConfig':
        """Create config from command-line arguments (no file).

        Useful for simple builds without a config file.
        """
        return cls(
            target_name=target,
            profile_name=profile,
            packages=packages or [],
            build=BuildOptions(verbose=verbose),
        )

    def load_target(self) -> Config:
        """Load the target configuration.

        Returns:
            Target Config instance
        """
        if self._target_config is None:
            self._target_config = Config.load_target(self.target_name)
        return self._target_config

    def get_all_packages(self) -> List[str]:
        """Get complete list of packages to build.

        Combines:
        - Target default_packages
        - User-specified packages
        - Minus excluded packages

        Returns:
            Deduplicated list of package names
        """
        target = self.load_target()

        # Start with target defaults
        all_packages = set(target.default_packages)

        # Add user packages
        all_packages.update(self.packages)

        # Remove excluded
        all_packages -= set(self.exclude_packages)

        return sorted(all_packages)

    def get_kernel_modules(self) -> List[str]:
        """Get list of kernel modules to include.

        Extracts kmod-* packages from the package list.
        """
        return [p for p in self.get_all_packages() if p.startswith('kmod-')]

    def get_kernel_config_overrides(self) -> Dict[str, str]:
        """Get kernel configuration overrides.

        Returns dict of CONFIG_* -> value mappings.
        """
        return self.kernel_config.copy()

    def get_kernel_config_fragment(self) -> str:
        """Generate kernel config fragment from overrides.

        Returns content suitable for appending to kernel .config
        """
        lines = []
        for key, value in sorted(self.kernel_config.items()):
            if value.lower() == 'n':
                lines.append(f'# {key} is not set')
            else:
                lines.append(f'{key}={value}')
        return '\n'.join(lines) + '\n' if lines else ''

    def write_kernel_config_fragment(self, output_path: Path) -> Path:
        """Write kernel config fragment to file.

        Args:
            output_path: Where to write the fragment

        Returns:
            Path to written file
        """
        content = self.get_kernel_config_fragment()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(content)
        return output_path

    def get_source_override(self, package_name: str) -> Optional[Dict[str, Any]]:
        """Get source override for a package if defined.

        Args:
            package_name: Package to check

        Returns:
            Override dict or None
        """
        return self.source_overrides.get(package_name)

    def save(self, output_path: Path):
        """Save configuration to YAML file.

        Useful for saving generated/modified configs.
        """
        data = {
            'target': self.target_name,
            'profile': self.profile_name,
        }

        if self.packages:
            data['packages'] = self.packages
        if self.exclude_packages:
            data['exclude_packages'] = self.exclude_packages

        if self.kernel_config:
            # Strip CONFIG_ prefix for cleaner output
            data['kernel'] = {
                k.replace('CONFIG_', ''): v
                for k, v in self.kernel_config.items()
            }

        # Image settings (only non-defaults)
        image_data = {}
        if self.image.rootfs_size != 100 * 1024 * 1024:
            image_data['rootfs_size'] = f"{self.image.rootfs_size // (1024*1024)}M"
        if self.image.compression != 'gzip':
            image_data['compression'] = self.image.compression
        if self.image.rootfs_type != 'squashfs':
            image_data['rootfs_type'] = self.image.rootfs_type
        if image_data:
            data['image'] = image_data

        # Build options (only non-defaults)
        build_data = {}
        if self.build.jobs:
            build_data['jobs'] = self.build.jobs
        if self.build.verbose:
            build_data['verbose'] = True
        if self.build.continue_on_error:
            build_data['continue_on_error'] = True
        if self.build.dl_dir:
            build_data['dl_dir'] = str(self.build.dl_dir)
        if build_data:
            data['build'] = build_data

        if self.source_overrides:
            data['source_overrides'] = self.source_overrides

        with open(output_path, 'w') as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False)

    def __str__(self) -> str:
        """Human-readable summary."""
        lines = [
            f"Target: {self.target_name}",
            f"Profile: {self.profile_name}",
            f"Packages: {len(self.get_all_packages())} total",
        ]
        if self.kernel_config:
            lines.append(f"Kernel overrides: {len(self.kernel_config)}")
        return '\n'.join(lines)
