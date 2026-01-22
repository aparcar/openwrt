"""
Configuration management for the build system.

Loads and validates target and package definitions from YAML files.
"""

import os
import re
import yaml
from pathlib import Path
from typing import Dict, List, Optional, Any


# Global default packages installed on all targets (from include/target.mk)
# These provide the minimal bootable system
GLOBAL_DEFAULT_PACKAGES = [
    # Core system
    'base-files',
    'busybox',
    'libc',
    # Init system
    'procd',
    'ubus',
    'uci',
    # Filesystem
    'fstools',
    # Networking
    'netifd',
    # Logging
    'logd',
    'urandom-seed',
    'urngd',
    # Remote access
    'dropbear',
    # SSL/TLS and package management
    'libustream-mbedtls',
    'ca-bundle',
    'uclient-fetch',
]


class Config:
    """Target configuration loaded from YAML."""

    def __init__(self, data: Dict[str, Any], base_dir: Path):
        self._data = data
        self._base_dir = base_dir

        # Core attributes
        self.name = data['name']
        self.board = data.get('board', self.name.split('-')[0])
        self.subtarget = data.get('subtarget', self.name.split('-')[1] if '-' in self.name else 'generic')
        self.description = data.get('description', '')
        self.arch = data['arch']
        self.cpu = data.get('cpu', {})
        self.toolchain = data['toolchain']
        self.features = data.get('features', [])
        self.default_packages = data.get('default_packages', ['base-files', 'busybox'])
        self.profiles = self._load_profiles(data.get('profiles', []))
        self.image = data.get('image', {})

        # Derived paths (needed before kernel loading)
        self._setup_paths()

        # Load and merge kernel configuration
        self.kernel = self._load_kernel_config(data.get('kernel', {}))

    def _load_profiles(self, inline_profiles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Load profiles from profiles/ subdirectory and merge with inline profiles.

        Profiles can be defined either:
        1. Inline in target.yaml under 'profiles:' key
        2. As individual YAML files in a profiles/ subdirectory

        Files in profiles/ take precedence over inline definitions with the same name.
        """
        profiles = {}

        # First, load inline profiles
        for profile in inline_profiles:
            name = profile.get('name')
            if name:
                profiles[name] = profile

        # Then, load profiles from profiles/ directory (overrides inline)
        profiles_dir = self._base_dir / 'profiles'
        if profiles_dir.exists() and profiles_dir.is_dir():
            for profile_file in sorted(profiles_dir.glob('*.yaml')):
                try:
                    with open(profile_file) as f:
                        profile_data = yaml.safe_load(f)
                    if profile_data and isinstance(profile_data, dict):
                        # Use filename (without .yaml) as profile name if not specified
                        name = profile_data.get('name', profile_file.stem)
                        profile_data['name'] = name
                        profiles[name] = profile_data
                except Exception as e:
                    print(f"Warning: Failed to load profile {profile_file}: {e}")

        # Return as list, maintaining order (inline first, then file-based)
        result = list(profiles.values())

        # If no profiles loaded, provide a default 'generic' profile
        if not result:
            result = [{'name': 'generic'}]

        return result

    def _setup_paths(self):
        """Set up all build paths."""
        # root_dir is the repository root - four levels up from target dir
        # _base_dir is target/linux/<board>/<subtarget>/, so parent.parent.parent.parent is the repo root
        self.root_dir = Path(os.environ.get('ROOT_DIR', self._base_dir.parent.parent.parent.parent))
        # Environment-based paths (set by Docker or manually)
        # Default to /openwrt (Docker mount) or root_dir (direct execution)
        openwrt_env = os.environ.get('OPENWRT_DIR')
        if openwrt_env:
            self.openwrt_dir = Path(openwrt_env)
        elif Path('/openwrt').exists():
            self.openwrt_dir = Path('/openwrt')
        else:
            # Fallback: when running directly on host, use root_dir
            self.openwrt_dir = self.root_dir
        # Backwards compat alias
        self.poc_dir = self.root_dir
        self.build_dir = Path(os.environ.get('BUILD_DIR', self.root_dir / 'build'))
        self.output_dir = Path(os.environ.get('OUTPUT_DIR', self.root_dir / 'build' / self.name))

        # Build subdirectories
        # Toolchain is per-target (different CPU features/optimizations)
        self.toolchain_dir = self.build_dir / 'toolchain' / self.name
        # Packages are per-architecture (same binaries work across targets with same arch)
        self.staging_dir = self.build_dir / 'staging' / self.arch
        self.packages_dir = self.build_dir / 'packages' / self.arch
        # Kernel is per-target (different configs, DTBs)
        self.kernel_build_dir = self.build_dir / 'kernel' / self.name
        # Rootfs is per-target (different package selections, configs)
        self.rootfs_dir = self.build_dir / 'rootfs' / self.name

        # Output directories (OpenWrt-style structure)
        # bin/targets/<board>/<subtarget>/ for images
        # bin/packages/<arch>/<feed>/ for packages
        self.bin_dir = self.root_dir / 'output'
        self.images_dir = self.bin_dir / 'targets' / self.board / self.subtarget
        self.packages_output_dir = self.bin_dir / 'packages' / self.arch

        # Download cache (shared across all targets at root level)
        self.dl_dir = Path(os.environ.get('DL_DIR', self.root_dir / 'dl'))

    def _load_kernel_config(self, target_kernel: Dict[str, Any]) -> Dict[str, Any]:
        """Load base kernel package and merge with target-specific overrides."""
        # Load base linux kernel package (in package/kernel/linux/)
        linux_pkg_file = self.root_dir / 'package' / 'kernel' / 'linux' / 'package.yaml'

        if linux_pkg_file.exists():
            with open(linux_pkg_file) as f:
                pkg_data = yaml.safe_load(f)

            # Extract base kernel info from package
            version = str(pkg_data.get('version', '6.12'))
            release = str(pkg_data.get('release', '65'))
            full_version = f"{version}.{release}"

            # Get source hash
            source = pkg_data.get('source', {})
            source_hash = source.get('hash', '')

            # Build base kernel config
            kernel = {
                'version': version,
                'full_version': full_version,
                'source_hash': source_hash,
            }

            # Get generic patch dirs from package
            pkg_kernel = pkg_data.get('kernel', {})
            generic_patches = pkg_kernel.get('generic_patch_dirs', [])
            generic_config = pkg_kernel.get('generic_config', '')
            generic_files = pkg_kernel.get('generic_files', [])

            # Resolve ${version} in paths
            generic_patches = [p.replace('${version}', version) for p in generic_patches]
            generic_config = generic_config.replace('${version}', version)
            generic_files = [f.replace('${version}', version) for f in generic_files]

            # Store for later use
            kernel['_generic_patch_dirs'] = generic_patches
            kernel['_generic_config'] = generic_config
            kernel['_generic_files'] = generic_files
        else:
            # Fallback if no linux package exists
            kernel = {
                'version': target_kernel.get('version', '6.12'),
                'full_version': target_kernel.get('full_version', '6.12.65'),
                'source_hash': target_kernel.get('source_hash', ''),
            }

        # Merge target-specific overrides
        for key in ['config_dir', 'dts_dir', 'load_address', 'entry_address', 'efi_stub']:
            if key in target_kernel:
                kernel[key] = target_kernel[key]

        # Target-specific patch dirs (appended after generic)
        if 'patch_dirs' in target_kernel:
            kernel['patch_dirs'] = target_kernel['patch_dirs']

        # Target-specific config fragments (new approach)
        if 'config_fragments' in target_kernel:
            kernel['config_fragments'] = target_kernel['config_fragments']

        return kernel

    def _resolve_path(self, path_str: str) -> Path:
        """Resolve a path that may be relative or contain variables.

        - If path starts with ${VAR}, expand the variable
        - If path is relative, resolve against _base_dir (target directory)
        - Return absolute Path object
        """
        # First expand any variables
        expanded = self._expand_vars(path_str)

        path = Path(expanded)

        # If still relative, resolve against target directory
        if not path.is_absolute():
            path = self._base_dir / path

        return path

    def _expand_vars(self, value: str) -> str:
        """Expand variables like ${OPENWRT_DIR} in strings."""
        if not isinstance(value, str):
            return value

        replacements = {
            '${OPENWRT_DIR}': str(self.openwrt_dir),
            '${POC_DIR}': str(self.poc_dir),
            '${BUILD_DIR}': str(self.build_dir),
            '${OUTPUT_DIR}': str(self.output_dir),
            '${TOOLCHAIN_DIR}': str(self.toolchain_dir),
        }

        for var, val in replacements.items():
            value = value.replace(var, val)

        return value

    def get_kernel_patch_dirs(self) -> List[Path]:
        """Get list of kernel patch directories in order.

        Combines generic patches from linux package with target-specific patches.
        Order: generic backport -> generic pending -> generic hack -> target patches
        """
        dirs = []

        # First, add generic patches from linux package (if loaded)
        for d in self.kernel.get('_generic_patch_dirs', []):
            # These are relative to OPENWRT_DIR
            path = self.openwrt_dir / d
            if path.exists():
                dirs.append(path)

        # Then add target-specific patches (may be relative to target dir)
        for d in self.kernel.get('patch_dirs', []):
            path = self._resolve_path(d)
            if path.exists():
                dirs.append(path)

        return dirs

    def get_kernel_config_files(self) -> List[Path]:
        """Get list of kernel config fragment files.

        If config_fragments is specified in target.yaml, use those files directly.
        Otherwise, fall back to the old config_dir based approach.

        Order: generic config -> target config fragments (or config_dir files)
        """
        version = self.kernel['version']
        files = []

        # Generic config from linux package
        generic_config_path = self.kernel.get('_generic_config', '')
        if generic_config_path:
            # Expand ${version} in the path
            generic_config_path = generic_config_path.replace('${version}', version)
            generic_config = self.openwrt_dir / generic_config_path
            if generic_config.exists():
                files.append(generic_config)
        else:
            # Fallback to default location
            generic_config = self.openwrt_dir / 'target' / 'linux' / 'generic' / f'config-{version}'
            if generic_config.exists():
                files.append(generic_config)

        # New approach: config_fragments (list of paths relative to target dir)
        if 'config_fragments' in self.kernel:
            for fragment in self.kernel['config_fragments']:
                path = self._resolve_path(fragment)
                if path.exists():
                    files.append(path)
            return files

        # Legacy approach: config_dir based
        config_dir = self._expand_vars(self.kernel.get('config_dir', ''))
        if config_dir:
            config_dir = Path(config_dir)

            # Target config
            target_config = config_dir / f'config-{version}'
            if target_config.exists():
                files.append(target_config)

            # Subtarget config
            subtarget_config = config_dir / self.subtarget / 'config-default'
            if subtarget_config.exists():
                files.append(subtarget_config)

        return files

    def get_dts_dir(self) -> Optional[Path]:
        """Get Device Tree Source directory."""
        dts_dir = self.kernel.get('dts_dir', '')
        if dts_dir:
            return Path(self._expand_vars(dts_dir))

        # Default location
        default_dir = self.openwrt_dir / 'target' / 'linux' / self.board / 'dts'
        if default_dir.exists():
            return default_dir

        return None

    @property
    def target_tuple(self) -> str:
        """Get the GNU target tuple."""
        if 'target_tuple' in self.toolchain:
            return self.toolchain['target_tuple']

        arch = self.arch
        libc = self.toolchain['libc']

        # Build tuple based on arch and libc
        if arch == 'aarch64':
            return f'aarch64-openwrt-linux-{libc}'
        elif arch == 'arm':
            suffix = 'gnueabi' if libc == 'glibc' else 'musleabi'
            return f'arm-openwrt-linux-{suffix}'
        elif arch in ('mips', 'mipsel'):
            return f'{arch}-openwrt-linux-{libc}'
        elif arch == 'x86_64':
            return f'x86_64-openwrt-linux-{libc}'
        else:
            return f'{arch}-openwrt-linux-{libc}'

    @property
    def cross_compile(self) -> str:
        """Get cross-compiler prefix."""
        return f'{self.target_tuple}-'

    def get_profile(self, name: str) -> Dict[str, Any]:
        """Get a specific profile by name."""
        for profile in self.profiles:
            if profile['name'] == name:
                return profile
        raise ValueError(f"Profile '{name}' not found for target {self.name}")

    def get_default_profile_name(self) -> str:
        """Get the name of the default profile for this target."""
        # Look for profile with default: true
        for profile in self.profiles:
            if profile.get('default', False):
                return profile['name']
        # Fall back to first profile or 'generic'
        if self.profiles:
            return self.profiles[0]['name']
        return 'generic'

    def get_profile_packages(self, profile_name: str) -> List[str]:
        """Get merged package list for a profile.

        Package sources (in order, later can override with -pkg):
        1. Global defaults (GLOBAL_DEFAULT_PACKAGES)
        2. Target defaults (default_packages in target.yaml)
        3. Profile packages (packages in profile yaml)

        Supports package exclusion with '-' prefix (e.g., '-dropbear').
        """
        packages = set(GLOBAL_DEFAULT_PACKAGES)

        # Add target defaults
        for pkg in self.default_packages:
            if pkg.startswith('-'):
                packages.discard(pkg[1:])
            else:
                packages.add(pkg)

        # Add profile packages
        try:
            profile = self.get_profile(profile_name)
            for pkg in profile.get('packages', []):
                if pkg.startswith('-'):
                    packages.discard(pkg[1:])
                else:
                    packages.add(pkg)
        except ValueError:
            pass  # Profile not found, use target defaults only

        return sorted(packages)

    @classmethod
    def load_target(cls, target_name: str) -> 'Config':
        """Load a target configuration by name.

        Target name format: <board>-<subtarget> (e.g., mediatek-filogic, armsr-armv8, x86-64)
        Target file location: target/linux/<board>/<subtarget>/target.yaml
        """
        # Find repository root (parent of owrt/ directory)
        owrt_dir = Path(__file__).parent
        root_dir = owrt_dir.parent

        # Parse target name into board and subtarget
        parts = target_name.split('-', 1)
        if len(parts) == 2:
            board, subtarget = parts
        else:
            board = target_name
            subtarget = 'generic'

        # Look for target.yaml in target/linux/<board>/<subtarget>/
        target_file = root_dir / 'target' / 'linux' / board / subtarget / 'target.yaml'
        if not target_file.exists():
            raise FileNotFoundError(f"Target definition not found: {target_file}")

        with open(target_file) as f:
            data = yaml.safe_load(f)

        return cls(data, target_file.parent)


class VariantConfig:
    """Configuration for a package variant (e.g., different SSL backends).
    
    Variants allow building the same source with different dependencies
    and build options. For example, ustream-ssl can be built with mbedtls,
    openssl, or wolfssl backends.
    """

    def __init__(self, name: str, data: Dict[str, Any], parent: 'PackageConfig'):
        self.name = name
        self.parent = parent
        self._data = data

        # Variant-specific package name (e.g., "libustream-mbedtls")
        self.package_name = data.get('package_name', f"{parent.name}-{name}")
        
        # Whether this is the default variant
        self.default: bool = data.get('default', False)
        
        # Variant-specific description
        self.description = data.get('description', f"{parent.name} ({name})")
        
        # Variant-specific dependencies (merged with parent's)
        self._dependencies = data.get('dependencies', {})
        
        # Conflicts with other variants
        self.conflicts: List[str] = data.get('conflicts', [])
        
        # Replaces (for package migration)
        self.replaces: List[str] = data.get('replaces', [])
        
        # Variant-specific build options
        self.configure_args: List[str] = data.get('configure_args', [])
        self.cmake_options: List[str] = data.get('cmake_options', [])
        self.meson_options: List[str] = data.get('meson_options', [])
        self.cflags: List[str] = data.get('cflags', [])
        self.ldflags: List[str] = data.get('ldflags', [])
        
        # Variant-specific install files (if different from parent)
        self.install = data.get('install', parent.install)

    @property
    def version(self) -> str:
        return self.parent.version

    @property
    def release(self) -> int:
        return self.parent.release

    @property
    def license(self) -> str:
        return self.parent.license

    @property
    def pkg_dir(self) -> Path:
        return self.parent.pkg_dir

    @property
    def source(self) -> Dict[str, Any]:
        return self.parent.source

    @property
    def build(self) -> Dict[str, Any]:
        """Get build config merged with variant-specific options."""
        build = dict(self.parent.build)
        
        # Merge configure_args
        if self.configure_args:
            existing = build.get('configure_args', [])
            build['configure_args'] = existing + self.configure_args
        
        # Merge cmake_options
        if self.cmake_options:
            existing = build.get('cmake_options', [])
            build['cmake_options'] = existing + self.cmake_options
            
        # Merge meson_options
        if self.meson_options:
            existing = build.get('meson_options', [])
            build['meson_options'] = existing + self.meson_options
        
        return build

    @property
    def runtime_deps(self) -> List[str]:
        """Get runtime dependencies (parent + variant-specific)."""
        parent_deps = self.parent.dependencies.get('runtime', [])
        variant_deps = self._dependencies.get('runtime', [])
        return parent_deps + variant_deps

    @property
    def build_deps(self) -> List[str]:
        """Get build dependencies (parent + variant-specific)."""
        parent_deps = self.parent.dependencies.get('build', [])
        variant_deps = self._dependencies.get('build', [])
        return parent_deps + variant_deps

    @property
    def build_system(self) -> str:
        return self.parent.build_system

    @property
    def source_name(self) -> str:
        """Get the source package name (for build tracking)."""
        return self.parent.name

    @property
    def metadata(self) -> Dict[str, Any]:
        """Get metadata with variant description."""
        meta = dict(self.parent.metadata)
        meta['description'] = self.description
        return meta

    def add_variable(self, name: str, value: str):
        self.parent.add_variable(name, value)

    def expand(self, s: str) -> str:
        return self.parent.expand(s)


class SubpackageConfig:
    """Configuration for a subpackage within a source package."""

    def __init__(self, name: str, data: Dict[str, Any], parent: 'PackageConfig'):
        self.name = name
        self.parent = parent
        self._data = data

        # Inherit from parent
        self.version = parent.version
        self.release = parent.release
        self.license = parent.license
        self.pkg_dir = parent.pkg_dir
        self.source = parent.source
        self.build = parent.build

        # Subpackage-specific
        self.description = data.get('description', '')
        self.section = data.get('section', 'base')
        self.dependencies = data.get('dependencies', {})
        self.install = {'files': data.get('files', []), 'symlinks': data.get('symlinks', [])}
        self.metadata = {'description': self.description, 'section': self.section}

        # Virtual package support
        self.provides: List[str] = data.get('provides', [])
        self.default_variant: bool = data.get('default_variant', False)

        # Conflicts - packages that cannot be installed alongside this one
        # In APK, conflicts are expressed as !pkgname in the depends field
        self.conflicts: List[str] = data.get('conflicts', [])

        # Replaces - packages whose files this package can take over
        # Used for package renames or splitting packages
        self.replaces: List[str] = data.get('replaces', [])

        # User/group creation (format: "user=uid:group=gid" or ":group=gid")
        self.userid: List[str] = data.get('userid', [])

        # Install scripts (preinst, postinst, prerm, postrm)
        self.scripts: Dict[str, str] = data.get('scripts', {})

        # Alternatives for busybox-style symlinks (format: "PRIORITY:TARGET:SOURCE")
        # e.g., "100:/sbin/rmmod:/sbin/kmodloader"
        self.alternatives: List[str] = data.get('alternatives', [])

    @property
    def runtime_deps(self) -> List[str]:
        """Get runtime dependencies."""
        return self.dependencies.get('runtime', [])

    @property
    def build_deps(self) -> List[str]:
        """Get build dependencies (inherited from parent)."""
        return self.parent.build_deps

    @property
    def build_system(self) -> str:
        """Get build system type (inherited from parent)."""
        return self.parent.build_system

    @property
    def source_name(self) -> str:
        """Get the source package name (for build tracking)."""
        return self.parent.name

    def add_variable(self, name: str, value: str):
        """Add a variable for interpolation (delegated to parent)."""
        self.parent.add_variable(name, value)

    def expand(self, s: str) -> str:
        """Expand variables in a string (delegated to parent)."""
        return self.parent.expand(s)


class PackageConfig:
    """Package configuration loaded from YAML."""

    # Class-level registry of all subpackages for lookup
    _subpackage_registry: Dict[str, 'SubpackageConfig'] = {}

    def __init__(self, data: Dict[str, Any], pkg_dir: Path):
        self._raw_data = data
        self.pkg_dir = pkg_dir

        # Core attributes (not interpolated - used as variables)
        self.name = data['name']
        self.version = str(data['version'])
        self.release = data.get('release', 1)
        self.license = data.get('license', '')

        # Build variable context for interpolation
        self._vars = {
            'name': self.name,
            'version': self.version,
            'release': str(self.release),
            'pkg_dir': str(pkg_dir),
        }

        # Interpolate all other sections
        self.source = self._interpolate(data.get('source', {}))
        self.metadata = self._interpolate(data.get('metadata', {}))
        self.dependencies = self._interpolate(data.get('dependencies', {}))
        self.build = self._interpolate(data.get('build', {}))

        # Install section - merge install: section with top-level files: and symlinks:
        # This allows packages to use either format
        install_data = data.get('install', {})
        if 'files' not in install_data and 'files' in data:
            install_data = install_data.copy()
            install_data['files'] = data['files']
        if 'symlinks' not in install_data and 'symlinks' in data:
            install_data = install_data.copy()
            install_data['symlinks'] = data['symlinks']
        self.install = self._interpolate(install_data)

        self.kernel = self._interpolate(data.get('kernel', {}))

        # Virtual package support
        self.provides: List[str] = data.get('provides', [])
        self.default_variant: bool = data.get('default_variant', False)

        # Conflicts - packages that cannot be installed alongside this one
        # In APK, conflicts are expressed as !pkgname in the depends field
        self.conflicts: List[str] = data.get('conflicts', [])

        # Replaces - packages whose files this package can take over
        # Used for package renames or splitting packages
        self.replaces: List[str] = data.get('replaces', [])

        # User/group creation (format: "user=uid:group=gid" or ":group=gid")
        self.userid: List[str] = data.get('userid', [])

        # Install scripts (preinst, postinst, prerm, postrm)
        self.scripts: Dict[str, str] = data.get('scripts', {})

        # Alternatives for busybox-style symlinks (format: "PRIORITY:TARGET:SOURCE")
        # e.g., "100:/sbin/rmmod:/sbin/kmodloader"
        self.alternatives: List[str] = data.get('alternatives', [])

        # Parse subpackages
        self._subpackages: Dict[str, SubpackageConfig] = {}
        subpkg_data = data.get('subpackages', {})
        if isinstance(subpkg_data, dict):
            for subpkg_name, subpkg_info in subpkg_data.items():
                subpkg = SubpackageConfig(subpkg_name, subpkg_info, self)
                self._subpackages[subpkg_name] = subpkg
                # Register in class-level registry
                PackageConfig._subpackage_registry[subpkg_name] = subpkg

        # Parse variants (different build configurations, e.g., SSL backends)
        self._variants: Dict[str, VariantConfig] = {}
        self._default_variant: Optional[str] = None
        variant_data = data.get('variants', {})
        if isinstance(variant_data, dict):
            for variant_name, variant_info in variant_data.items():
                variant = VariantConfig(variant_name, variant_info, self)
                self._variants[variant_name] = variant
                # Register variant's package_name in subpackage registry for lookup
                PackageConfig._subpackage_registry[variant.package_name] = variant
                if variant.default:
                    self._default_variant = variant_name

    @property
    def variants(self) -> Dict[str, VariantConfig]:
        """Get all variants."""
        return self._variants

    @property
    def has_variants(self) -> bool:
        """Check if this package defines variants."""
        return len(self._variants) > 0

    def get_variant(self, name: str) -> Optional[VariantConfig]:
        """Get a specific variant by name."""
        return self._variants.get(name)

    def get_default_variant(self) -> Optional[VariantConfig]:
        """Get the default variant, if any."""
        if self._default_variant:
            return self._variants.get(self._default_variant)
        return None

    @property
    def subpackages(self) -> Dict[str, SubpackageConfig]:
        """Get all subpackages."""
        return self._subpackages

    @property
    def has_subpackages(self) -> bool:
        """Check if this package defines subpackages."""
        return len(self._subpackages) > 0

    def get_subpackage(self, name: str) -> Optional[SubpackageConfig]:
        """Get a specific subpackage by name."""
        return self._subpackages.get(name)

    def _interpolate(self, value: Any) -> Any:
        """Recursively interpolate variables in a value."""
        if isinstance(value, str):
            return self._interpolate_string(value)
        elif isinstance(value, dict):
            return {k: self._interpolate(v) for k, v in value.items()}
        elif isinstance(value, list):
            return [self._interpolate(item) for item in value]
        else:
            return value

    def _interpolate_string(self, s: str) -> str:
        """Interpolate ${var} patterns in a string."""
        if '${' not in s:
            return s

        result = s
        for var_name, var_value in self._vars.items():
            result = result.replace(f'${{{var_name}}}', var_value)

        # Also support $var syntax (without braces)
        for var_name, var_value in self._vars.items():
            # Use word boundary to avoid partial replacements
            result = re.sub(rf'\$({var_name})(?![a-zA-Z0-9_])', var_value, result)

        return result

    def add_variable(self, name: str, value: str):
        """Add a variable for interpolation (used during build)."""
        self._vars[name] = value

    def expand(self, s: str) -> str:
        """Expand variables in a string (public method for build-time expansion)."""
        return self._interpolate_string(s)

    @property
    def runtime_deps(self) -> List[str]:
        """Get runtime dependencies."""
        return self.dependencies.get('runtime', [])

    @property
    def build_deps(self) -> List[str]:
        """Get build dependencies."""
        return self.dependencies.get('build', [])

    @property
    def build_system(self) -> str:
        """Get build system type."""
        return self.build.get('system', 'autotools')

    @classmethod
    def load(cls, pkg_dir: Path) -> 'PackageConfig':
        """Load package configuration from directory."""
        pkg_file = pkg_dir / 'package.yaml'
        if not pkg_file.exists():
            raise FileNotFoundError(f"Package definition not found: {pkg_file}")

        with open(pkg_file) as f:
            data = yaml.safe_load(f)

        return cls(data, pkg_dir)

    @classmethod
    def find_package(cls, name: str) -> Optional['PackageConfig']:
        """Find and load a package, subpackage, or variant by name.

        If a source package has subpackages or variants, returns the appropriate
        config if one matches the requested name.

        Searches the package/ directory tree for package.yaml files.
        """
        root_dir = Path(__file__).parent.parent

        # Check if already in subpackage/variant registry
        if name in cls._subpackage_registry:
            return cls._subpackage_registry[name]

        # Search for package.yaml files in the package/ directory tree
        package_root = root_dir / 'package'

        # Also check special locations like toolchain/
        search_roots = [package_root]
        toolchain_musl = root_dir / 'toolchain' / 'musl'
        if toolchain_musl.exists():
            search_roots.append(toolchain_musl.parent)

        # Also check owrt/packages for virtual packages (kmod-*)
        owrt_packages = root_dir / 'owrt' / 'packages'
        if owrt_packages.exists():
            search_roots.append(owrt_packages)

        for search_root in search_roots:
            if not search_root.exists():
                continue

            # Recursively find all package.yaml files
            for pkg_yaml in search_root.rglob('package.yaml'):
                pkg_dir = pkg_yaml.parent

                # Skip if the directory name doesn't match and no subpackages
                dir_name = pkg_dir.name

                # Try loading if directory matches or we need to search subpackages
                try:
                    pkg = cls.load(pkg_dir)

                    # Direct match by source name
                    if pkg.name == name:
                        # If this source has subpackages and one matches,
                        # return the subpackage
                        if pkg.has_subpackages and name in pkg.subpackages:
                            return pkg.subpackages[name]
                        return pkg

                    # Check subpackages
                    if name in pkg.subpackages:
                        return pkg.subpackages[name]

                    # Check variants by their package_name
                    if pkg.has_variants:
                        for variant in pkg.variants.values():
                            if variant.package_name == name:
                                return variant
                except Exception:
                    pass

        return None

    @classmethod
    def clear_registry(cls):
        """Clear the subpackage registry (for testing)."""
        cls._subpackage_registry.clear()

    @classmethod
    def find_all_packages(cls) -> List['PackageConfig']:
        """Find and load all source packages.

        Returns a list of all source PackageConfig objects (not subpackages).
        Searches the package/ directory tree for package.yaml files.
        """
        from pathlib import Path

        root_dir = Path(__file__).parent.parent
        packages = []
        seen = set()

        # Search roots
        package_root = root_dir / 'package'
        search_roots = [package_root]

        # Also check toolchain/
        toolchain_dir = root_dir / 'toolchain'
        if toolchain_dir.exists():
            search_roots.append(toolchain_dir)

        for search_root in search_roots:
            if not search_root.exists():
                continue

            # Recursively find all package.yaml files
            for pkg_yaml in search_root.rglob('package.yaml'):
                pkg_dir = pkg_yaml.parent

                # Skip if already seen
                if pkg_dir in seen:
                    continue
                seen.add(pkg_dir)

                try:
                    pkg = cls.load(pkg_dir)
                    packages.append(pkg)
                except Exception:
                    pass

        return packages


def compute_package_content_hash(pkg: PackageConfig, toolchain_info: Optional[Dict[str, str]] = None) -> str:
    """Compute content hash of package inputs for change detection.

    This is a standalone function that can be used by both the ninja generator
    and the package builder to ensure consistent change detection.

    Includes:
    - package.yaml content
    - patches/ directory contents
    - files/ directory contents
    - Version and release info
    - Toolchain version (if provided)

    Args:
        pkg: Package configuration
        toolchain_info: Optional dict with 'gcc_version' and 'libc' keys

    Returns:
        12-character hex hash string
    """
    import hashlib

    h = hashlib.sha256()

    # Get the package directory (for subpackages, use source package dir)
    if isinstance(pkg, SubpackageConfig):
        pkg_dir = pkg.parent.pkg_dir
    else:
        pkg_dir = pkg.pkg_dir

    # Hash the package.yaml file
    yaml_file = pkg_dir / 'package.yaml'
    if yaml_file.exists():
        h.update(yaml_file.read_bytes())

    # Hash patches directory
    patches_dir = pkg_dir / 'patches'
    if patches_dir.exists():
        for patch_file in sorted(patches_dir.glob('*')):
            if patch_file.is_file():
                h.update(patch_file.name.encode())
                h.update(patch_file.read_bytes())

    # Hash files directory
    files_dir = pkg_dir / 'files'
    if files_dir.exists():
        for f in sorted(files_dir.rglob('*')):
            if f.is_file():
                rel_path = f.relative_to(files_dir)
                h.update(str(rel_path).encode())
                h.update(f.read_bytes())

    # Include version and release in hash
    h.update(f"{pkg.version}-{pkg.release}".encode())

    # Include toolchain version for cascading rebuilds on toolchain changes
    if toolchain_info:
        h.update(f"gcc:{toolchain_info.get('gcc_version', '')}".encode())
        h.update(f"libc:{toolchain_info.get('libc', '')}".encode())

    return h.hexdigest()[:12]
