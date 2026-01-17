"""
Configuration management for the build system.

Loads and validates target and package definitions from YAML files.
"""

import os
import re
import yaml
from pathlib import Path
from typing import Dict, List, Optional, Any


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
        self.profiles = data.get('profiles', [{'name': 'generic'}])
        self.image = data.get('image', {})

        # Derived paths (needed before kernel loading)
        self._setup_paths()

        # Load and merge kernel configuration
        self.kernel = self._load_kernel_config(data.get('kernel', {}))

    def _setup_paths(self):
        """Set up all build paths."""
        # Environment-based paths (set by Docker or manually)
        self.openwrt_dir = Path(os.environ.get('OPENWRT_DIR', '/openwrt'))
        # root_dir is the repository root - three levels up from target dir (owrt/targets/name/ -> root)
        # _base_dir is owrt/targets/name/, so parent.parent.parent is the repo root
        self.root_dir = Path(os.environ.get('ROOT_DIR', self._base_dir.parent.parent.parent))
        # Backwards compat alias
        self.poc_dir = self.root_dir
        self.build_dir = Path(os.environ.get('BUILD_DIR', self.root_dir / 'build'))
        self.output_dir = Path(os.environ.get('OUTPUT_DIR', self.root_dir / 'build' / self.name))

        # Build subdirectories
        self.toolchain_dir = self.build_dir / 'toolchain' / self.name
        self.staging_dir = self.build_dir / 'staging' / self.name
        self.packages_dir = self.build_dir / 'packages' / self.name
        self.kernel_build_dir = self.build_dir / 'kernel' / self.name
        self.rootfs_dir = self.build_dir / 'rootfs' / self.name

        # Output directories (OpenWrt-style structure)
        # bin/targets/<board>/<subtarget>/ for images
        # bin/packages/<arch>/<feed>/ for packages
        self.bin_dir = self.root_dir / 'output'
        self.images_dir = self.bin_dir / 'targets' / self.board / self.subtarget
        self.packages_output_dir = self.bin_dir / 'packages' / self.arch

        # Download cache (shared across targets)
        self.dl_dir = Path(os.environ.get('DL_DIR', self.build_dir / 'dl'))

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

    @classmethod
    def load_target(cls, target_name: str) -> 'Config':
        """Load a target configuration by name."""
        # Find the owrt module directory (where targets/ is located)
        owrt_dir = Path(__file__).parent
        if not owrt_dir.exists():
            owrt_dir = Path.cwd() / 'owrt'

        # Look for target.yaml in owrt/targets/
        target_file = owrt_dir / 'targets' / target_name / 'target.yaml'
        if not target_file.exists():
            raise FileNotFoundError(f"Target definition not found: {target_file}")

        with open(target_file) as f:
            data = yaml.safe_load(f)

        return cls(data, target_file.parent)


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

        # User/group creation (format: "user=uid:group=gid" or ":group=gid")
        self.userid: List[str] = data.get('userid', [])

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
        self.install = self._interpolate(data.get('install', {}))
        self.kernel = self._interpolate(data.get('kernel', {}))

        # Virtual package support
        self.provides: List[str] = data.get('provides', [])
        self.default_variant: bool = data.get('default_variant', False)

        # User/group creation (format: "user=uid:group=gid" or ":group=gid")
        self.userid: List[str] = data.get('userid', [])

        # Parse subpackages
        self._subpackages: Dict[str, SubpackageConfig] = {}
        subpkg_data = data.get('subpackages', {})
        if isinstance(subpkg_data, dict):
            for subpkg_name, subpkg_info in subpkg_data.items():
                subpkg = SubpackageConfig(subpkg_name, subpkg_info, self)
                self._subpackages[subpkg_name] = subpkg
                # Register in class-level registry
                PackageConfig._subpackage_registry[subpkg_name] = subpkg

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
        """Find and load a package or subpackage by name.

        If a source package has subpackages, returns the subpackage if one
        matches the requested name (even if the source has the same name).

        Searches the package/ directory tree for package.yaml files.
        """
        root_dir = Path(__file__).parent.parent

        # Check if already in subpackage registry
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
                except Exception:
                    pass

        return None

    @classmethod
    def clear_registry(cls):
        """Clear the subpackage registry (for testing)."""
        cls._subpackage_registry.clear()


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
