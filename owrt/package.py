"""
Package builder - builds userspace packages.

Handles:
- Loading package definitions from YAML
- Resolving dependencies
- Building packages with various build systems (autotools, cmake, make, etc.)
- Installing to staging directory
- Creating APK packages

Build system implementations aligned with OpenWrt's include/cmake.mk,
include/package.mk, and include/package-defaults.mk.

Change detection:
- Computes hash of all inputs (YAML, patches, files)
- Stores hash in stamp file name: .built_<hash>
- If hash changes, old stamp won't match → triggers rebuild

Security:
- All packages are built with fakechroot isolation
- Each package has its own staging directory
- Dependencies are installed via APK into an isolated fake root
- Packages cannot see or modify other packages' build artifacts
"""

import fnmatch
import glob
import hashlib
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional, List, Dict, Set, Any, Union

from .config import Config, PackageConfig, SubpackageConfig
from .toolchain import ToolchainBuilder
from .utils import run_command, download_file, extract_archive, apply_patches
from .apk import APKPackager, APKRepository
from .container import FakechrootIsolation, get_fakechroot_isolation


class PackageBuilder:
    """Builds userspace packages with fakechroot isolation.

    All packages are built with fakechroot isolation for maximum security:
    - Each package has its own staging directory
    - Dependencies are installed via APK into an isolated fake root
    - Packages cannot see or modify other packages' build artifacts
    """

    def __init__(
        self,
        config: Config,
        verbose: bool = False,
        jobs: Optional[int] = None,
    ):
        """
        Initialize the package builder.

        Args:
            config: Target configuration
            verbose: Enable verbose output
            jobs: Number of parallel jobs
        """
        self.config = config
        self.verbose = verbose
        self.jobs = jobs or os.cpu_count()

        # Paths
        self.packages_dir = config.packages_dir
        self.staging_dir = config.staging_dir
        self.dl_dir = config.dl_dir
        self.stamp_dir = self.packages_dir / 'stamp'

        # APK packaging paths
        self.apk_dir = config.build_dir / 'apk-packages' / config.name
        self.repo_dir = config.build_dir / 'apk-repo' / config.name

        # Get toolchain
        self.toolchain = ToolchainBuilder(config, verbose=verbose, jobs=jobs)

        # APK packager and repository
        self._apk_packager: Optional[APKPackager] = None
        self._apk_repo: Optional[APKRepository] = None

        # Package cache
        self._packages: Dict[str, PackageConfig] = {}
        self._built: Set[str] = set()
        self._apk_files: List[Path] = []

        # Fakechroot isolation backend (initialized lazily)
        self._isolation: Optional[FakechrootIsolation] = None

    def _get_isolation(self) -> Optional[FakechrootIsolation]:
        """Get the fakechroot isolation backend."""
        if self._isolation is None:
            apk_binary = self.config.build_dir / 'host-staging' / 'bin' / 'apk'
            self._isolation = get_fakechroot_isolation(
                toolchain_dir=self.toolchain.toolchain_dir,
                repo_dir=self.repo_dir,
                arch=self.config.arch,
                apk_binary=apk_binary,
                verbose=self.verbose,
            )
            if self._isolation is None:
                print("      Warning: fakechroot isolation not available")
                print("      Install fakechroot and fakeroot in Docker image")
        return self._isolation

    def _get_package_staging_dir(self, pkg_name: str) -> Path:
        """Get the staging directory for a package."""
        return self.packages_dir / pkg_name / 'staging'

    def _get_package_deps_dir(self, pkg_name: str) -> Path:
        """Get the assembled dependencies directory for a package."""
        return self.packages_dir / pkg_name / 'deps'

    def _get_source_package_name(self, pkg: Union[PackageConfig, SubpackageConfig]) -> str:
        """Get the source package name (handles subpackages)."""
        if isinstance(pkg, SubpackageConfig):
            return pkg.source_name
        return pkg.name

    def clean(self):
        """Clean all package build artifacts."""
        if self.packages_dir.exists():
            shutil.rmtree(self.packages_dir)
        if self.staging_dir.exists():
            shutil.rmtree(self.staging_dir)
        if self.apk_dir.exists():
            shutil.rmtree(self.apk_dir)
        if self.repo_dir.exists():
            shutil.rmtree(self.repo_dir)

    def build_packages(self, packages: List[str], force: bool = False, create_apk: bool = True,
                       single_package: bool = False):
        """Build a list of packages with dependency resolution.

        Args:
            packages: List of package names to build
            force: Force rebuild even if already built
            create_apk: Create APK packages (default True)
            single_package: If True, skip dependency resolution (ninja handles deps)
        """
        # Create directories
        self.packages_dir.mkdir(parents=True, exist_ok=True)
        self.stamp_dir.mkdir(parents=True, exist_ok=True)
        self.apk_dir.mkdir(parents=True, exist_ok=True)
        self.repo_dir.mkdir(parents=True, exist_ok=True)

        # Initialize APK packager
        if create_apk:
            self._apk_packager = APKPackager(self.config, verbose=self.verbose)
            self._apk_repo = APKRepository(
                self.repo_dir,
                build_dir=self.config.build_dir,
                arch=self.config.arch,
                verbose=self.verbose
            )

        # Resolve build order (skip if single_package mode - ninja handles deps)
        if single_package:
            build_order = packages
        else:
            build_order = self._resolve_dependencies(packages)
        print(f"  Building {len(build_order)} packages: {', '.join(build_order)}")

        # Build each package
        for pkg_name in build_order:
            self._build_package(pkg_name, force=force, create_apk=create_apk)

        # Generate APK repository index
        if create_apk and self._apk_files:
            print(f"  Creating APK repository with {len(self._apk_files)} packages...")
            self._generate_apk_index()

    def _resolve_dependencies(self, packages: List[str]) -> List[str]:
        """Resolve package dependencies and return build order."""
        resolved = []
        seen = set()

        def resolve(name: str):
            if name in seen:
                return
            seen.add(name)

            pkg = self._load_package(name)
            if pkg:
                # Resolve build dependencies first
                for dep in pkg.build_deps:
                    resolve(dep)
                # Then runtime dependencies
                for dep in pkg.runtime_deps:
                    resolve(dep)

            resolved.append(name)

        for pkg in packages:
            resolve(pkg)

        return resolved

    def _load_package(self, name: str) -> Optional[PackageConfig]:
        """Load a package configuration (may return SubpackageConfig)."""
        if name in self._packages:
            return self._packages[name]

        pkg = PackageConfig.find_package(name)
        if pkg:
            self._packages[name] = pkg

        return pkg

    def _compute_package_hash(self, pkg: PackageConfig) -> str:
        """Compute content hash of package inputs for change detection.

        Includes:
        - package.yaml content
        - patches/ directory contents
        - files/ directory contents
        - Version and release info
        - Toolchain version (GCC, libc)
        - Dependency hashes (cascading rebuilds)
        """
        h = hashlib.sha256()

        # Get the package directory (for subpackages, use source package dir)
        if isinstance(pkg, SubpackageConfig):
            pkg_dir = pkg.parent.pkg_dir
            source_pkg = pkg.parent
        else:
            pkg_dir = pkg.pkg_dir
            source_pkg = pkg

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

        # Hash target overlay directories
        for overlay_dir in self._get_target_overlay_dirs(pkg.name):
            h.update(f"overlay:{overlay_dir}".encode())
            for f in sorted(overlay_dir.rglob('*')):
                if f.is_file():
                    rel_path = f.relative_to(overlay_dir)
                    h.update(str(rel_path).encode())
                    h.update(f.read_bytes())

        # Include version and release in hash
        h.update(f"{pkg.version}-{pkg.release}".encode())

        # Include toolchain version for cascading rebuilds on toolchain changes
        h.update(f"gcc:{self.config.toolchain.get('gcc_version', '')}".encode())
        h.update(f"libc:{self.config.toolchain.get('libc', '')}".encode())

        # Include dependency hashes for cascading rebuilds
        # Collect all deps (build + runtime, including from subpackages)
        all_deps = set(source_pkg.build_deps) | set(source_pkg.runtime_deps)
        for subpkg in source_pkg.subpackages.values():
            all_deps.update(subpkg.runtime_deps)

        # Get dependency hashes from their stamp files
        for dep_name in sorted(all_deps):
            dep_hash = self._get_dependency_hash(dep_name)
            if dep_hash:
                h.update(f"dep:{dep_name}:{dep_hash}".encode())

        return h.hexdigest()[:12]

    def _get_dependency_hash(self, name: str) -> Optional[str]:
        """Get the hash of a built dependency from its stamp file.

        For subpackages, looks up the source package's stamp instead.
        """
        # First try direct lookup
        stamp = self._find_stamp(name, 'built')
        if stamp:
            parts = stamp.name.rsplit('_', 1)
            if len(parts) == 2:
                return parts[1]

        # If not found, check if it's a subpackage and use source package
        pkg = self._load_package(name)
        if pkg and isinstance(pkg, SubpackageConfig):
            source_name = pkg.source_name
            stamp = self._find_stamp(source_name, 'built')
            if stamp:
                parts = stamp.name.rsplit('_', 1)
                if len(parts) == 2:
                    return parts[1]

        return None

    def _find_stamp(self, name: str, prefix: str = 'built') -> Optional[Path]:
        """Find existing stamp file for a package (any hash)."""
        pattern = f'{name}.{prefix}_*'
        stamps = list(self.stamp_dir.glob(pattern))
        return stamps[0] if stamps else None

    def _clean_old_stamps(self, name: str, prefix: str = 'built'):
        """Remove old stamp files for a package."""
        pattern = f'{name}.{prefix}_*'
        for stamp in self.stamp_dir.glob(pattern):
            stamp.unlink()

    def _get_target_overlay_dirs(self, pkg_name: str) -> List[Path]:
        """Get target-specific file overlay directories for a package.

        Returns directories in order of priority (lowest to highest):
        1. Target-level base-files (e.g., targets/<target>/base-files/target/)
        2. Subtarget-level base-files (e.g., targets/<target>/base-files/subtarget/)

        Currently only applies to 'base-files' package. Future enhancement could
        support per-package overlays via package.yaml configuration.

        Args:
            pkg_name: Package name to get overlays for

        Returns:
            List of overlay directories that exist, in priority order
        """
        overlay_dirs = []

        # Only apply overlays for base-files package (for now)
        if pkg_name != 'base-files':
            return overlay_dirs

        # Look for target overlay directories
        target_dir = self.config.poc_dir / 'targets' / self.config.name

        # Target-level overlay (from target/linux/<board>/base-files)
        target_overlay = target_dir / 'base-files' / 'target'
        if target_overlay.exists():
            overlay_dirs.append(target_overlay)

        # Subtarget-level overlay (from target/linux/<board>/<subtarget>/base-files)
        subtarget_overlay = target_dir / 'base-files' / 'subtarget'
        if subtarget_overlay.exists():
            overlay_dirs.append(subtarget_overlay)

        return overlay_dirs

    def _build_package(self, name: str, force: bool = False, create_apk: bool = True):
        """Build a single package or create APK for a subpackage.

        For subpackages: ensures the source is compiled first, then creates the APK.
        For regular packages: compiles source and creates APK.

        Uses hash-based stamps for change detection:
        - Computes hash of all inputs (YAML, patches, files, version)
        - Creates stamp file like: name.built_<hash>
        - If inputs change, hash changes, old stamp won't match → triggers rebuild

        In isolated mode:
        - Assembles dependencies into per-package deps directory
        - Uses per-package staging directory
        - Optionally runs in Docker container
        """
        pkg = self._load_package(name)

        if not pkg:
            print(f"    {name}: WARNING - package definition not found, skipping")
            return

        # For subpackages, resolve to source package (unless name == source_name)
        if isinstance(pkg, SubpackageConfig):
            source_name = pkg.source_name
            if name != source_name:
                # Different name, need to build the source package
                print(f"    {name}: resolving to source package {source_name}")
                self._build_package(source_name, force=force, create_apk=create_apk)
                return
            # name == source_name: this is a subpackage with same name as source
            # Load the parent (source) package config instead
            pkg = pkg.parent

        # Compute hash of package inputs for change detection
        pkg_hash = self._compute_package_hash(pkg)
        stamp = self.stamp_dir / f'{name}.built_{pkg_hash}'
        apk_stamp = self.stamp_dir / f'{name}.apk_{pkg_hash}'

        if not force and stamp.exists():
            # Check if APK needs to be created
            if create_apk and not apk_stamp.exists():
                self._create_apk_from_existing(name)
            else:
                print(f"    {name}: already built (hash {pkg_hash}), skipping")
            return

        # Clean old stamps with different hashes before rebuilding
        self._clean_old_stamps(name, 'built')
        self._clean_old_stamps(name, 'apk')

        print(f"    {name}: building (hash {pkg_hash})...")

        # Clean old build artifacts when hash changed
        pkg_build_base = self.packages_dir / name
        if pkg_build_base.exists():
            shutil.rmtree(pkg_build_base)

        # Create build directory and per-package install directory
        build_dir = self.packages_dir / name / 'build'
        pkg_install_dir = self.packages_dir / name / 'ipkg-install'
        build_dir.mkdir(parents=True, exist_ok=True)
        pkg_install_dir.mkdir(parents=True, exist_ok=True)

        # Get per-package staging directory
        pkg_staging_dir = self._get_package_staging_dir(name)
        pkg_staging_dir.mkdir(parents=True, exist_ok=True)

        # Download and extract source
        src_dir = self._prepare_source(pkg, build_dir.parent)

        # Build using appropriate build system
        build_system = pkg.build_system
        env = self._get_build_env(pkg, pkg_staging_dir=pkg_staging_dir)

        # Get fakechroot isolation backend
        isolation = self._get_isolation()

        if isolation and build_system != 'none':
            # Build with fakechroot isolation
            self._build_with_isolation(
                isolation, pkg, src_dir, build_dir, pkg_staging_dir, pkg_install_dir, env
            )
        else:
            # Fallback: run build directly (for 'none' build system or if fakechroot unavailable)
            if build_system == 'autotools':
                self._build_autotools(pkg, src_dir, build_dir, env)
            elif build_system == 'cmake':
                self._build_cmake(pkg, src_dir, build_dir, env)
            elif build_system == 'meson':
                self._build_meson(pkg, src_dir, build_dir, env)
            elif build_system == 'make':
                self._build_make(pkg, src_dir, build_dir, env)
            elif build_system == 'kmod':
                self._build_kmod(pkg, src_dir, build_dir, env)
            elif build_system == 'custom':
                self._build_custom(pkg, src_dir, build_dir, env, pkg_install_dir)
            elif build_system == 'none':
                pass  # No build needed (e.g., base-files)
            else:
                print(f"      Unknown build system: {build_system}")
                return

            # Install to per-package directory and staging
            self._install_package(pkg, src_dir, build_dir, env, pkg_install_dir, pkg_staging_dir)

        stamp.touch()
        self._built.add(name)

        # Create APK packages for all subpackages (or main package if no subpackages)
        if create_apk:
            if hasattr(pkg, 'has_subpackages') and pkg.has_subpackages:
                # Build APKs for all subpackages
                for subpkg_name, subpkg in pkg.subpackages.items():
                    print(f"    {subpkg_name}: creating subpackage APK...")
                    self._build_subpackage(subpkg, create_apk=True)
            else:
                self._create_apk_package(pkg, pkg_install_dir)

            # Auto-generate -dev package from InstallDev content
            self._create_auto_dev_package(pkg, pkg_install_dir)
            apk_stamp.touch()

    def _build_with_isolation(
        self,
        isolation: FakechrootIsolation,
        pkg: PackageConfig,
        src_dir: Path,
        build_dir: Path,
        staging_dir: Path,
        install_dir: Path,
        env: Dict[str, str],
    ):
        """Build a package with fakechroot isolation.

        Dependencies are installed via APK into an isolated fake root,
        ensuring packages only see their explicitly declared dependencies.
        """
        build_system = pkg.build_system

        # Build commands based on build system
        commands = []

        if build_system == 'autotools':
            commands = self._get_autotools_isolation_commands(pkg, env)
        elif build_system == 'cmake':
            commands = self._get_cmake_isolation_commands(pkg, env)
        elif build_system == 'meson':
            commands = self._get_meson_isolation_commands(pkg, env, src_dir, staging_dir)
        elif build_system == 'make':
            commands = self._get_make_isolation_commands(pkg, env)
        elif build_system == 'kmod':
            commands = self._get_kmod_isolation_commands(pkg, env)
        elif build_system == 'custom':
            commands = self._get_custom_isolation_commands(pkg, env)
        elif build_system == 'none':
            return  # Nothing to build

        if not commands:
            print(f"      Warning: No build commands for {pkg.name}")
            return

        # Get build dependencies for APK installation
        build_deps = pkg.build_deps

        isolation.build_package(
            pkg_name=pkg.name,
            source_dir=src_dir,
            build_dir=build_dir,
            staging_dir=staging_dir,
            install_dir=install_dir,
            build_deps=build_deps,
            env=env,
            commands=commands,
        )

        # Copy files from package's files/ directory and target overlays
        # This mirrors what _install_package does for non-isolated builds
        # Order: package files -> target overlay -> subtarget overlay
        files_sources = []

        # Package's own files directory
        pkg_files_dir = pkg.pkg_dir / 'files'
        if pkg_files_dir.exists():
            files_sources.append(pkg_files_dir)

        # Target overlay directories (in priority order)
        files_sources.extend(self._get_target_overlay_dirs(pkg.name))

        # Copy files from all sources (later sources override earlier)
        for files_dir in files_sources:
            for src_file in files_dir.rglob('*'):
                if src_file.is_file():
                    rel_path = src_file.relative_to(files_dir)
                    # Copy to both staging and install directories
                    for dest_dir in [staging_dir, install_dir]:
                        dst_file = dest_dir / rel_path
                        dst_file.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(src_file, dst_file)

        # Process install.staging section - copy headers/libs to shared staging
        # This is needed for packages that provide development files for other packages
        install = pkg.install
        for staging_spec in install.get('staging', []):
            src_pattern = staging_spec['src']
            dst_path = staging_spec['dst']

            # Substitute variables in source path
            src_pattern = src_pattern.replace('${build_dir}', str(build_dir))
            src_pattern = src_pattern.replace('${src_dir}', str(src_dir))
            src_pattern = src_pattern.replace('${pkg_dir}', str(pkg.pkg_dir))

            # Determine the base directory for glob matching
            if src_pattern.startswith('/'):
                base_dir = Path('/')
                pattern = src_pattern[1:]
            else:
                base_dir = src_dir
                pattern = src_pattern

            # Handle glob patterns
            shared_staging = self.staging_dir
            dst_base = shared_staging / dst_path.lstrip('/')
            if '*' in pattern:
                for src_file in base_dir.glob(pattern):
                    if src_file.is_file():
                        dst_file = dst_base / src_file.name
                        dst_file.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(src_file, dst_file)
                        if self.verbose:
                            print(f"      staging: {src_file.name} -> {dst_path}")
                    elif src_file.is_dir():
                        dst_dir = dst_base
                        dst_dir.mkdir(parents=True, exist_ok=True)
                        self._copy_tree(src_file, dst_dir)
                        if self.verbose:
                            print(f"      staging: {src_file.name}/ -> {dst_path}")
            else:
                src_path = base_dir / pattern
                if src_path.is_dir():
                    dst_base.mkdir(parents=True, exist_ok=True)
                    self._copy_tree(src_path, dst_base)
                    if self.verbose:
                        print(f"      staging: {pattern}/ -> {dst_path}")
                elif src_path.is_file():
                    dst_file = dst_base / src_path.name
                    dst_file.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src_path, dst_file)
                    if self.verbose:
                        print(f"      staging: {pattern} -> {dst_path}")

    def _get_autotools_isolation_commands(self, pkg: PackageConfig, env: Dict[str, str]) -> List[List[str]]:
        """Get commands to build an autotools package in container."""
        target = self.config.target_tuple
        commands = []

        # Configure
        configure_args = [
            '/src/configure',
            f'--target={target}',
            f'--host={target}',
            '--prefix=/usr',
            '--disable-dependency-tracking',
            '--disable-nls',
        ] + pkg.build.get('configure_args', [])

        commands.append(configure_args)

        # Make
        make_args = ['make', f'-j{self.jobs}'] + pkg.build.get('make_args', [])
        commands.append(make_args)

        # Install to both staging and ipkg-install
        commands.append(['make', 'DESTDIR=/staging', 'install'])
        commands.append(['make', 'DESTDIR=/ipkg-install', 'install'])

        return commands

    def _get_cmake_isolation_commands(self, pkg: PackageConfig, env: Dict[str, str]) -> List[List[str]]:
        """Get commands to build a cmake package in container.

        Uses in-source builds (cmake . from source dir) to match OpenWrt behavior.
        This is required for packages like netifd that have custom commands
        using relative paths (e.g., ./make_ethtool_modes_h.sh).
        """
        import shlex
        target = self.config.target_tuple
        commands = []

        # Use in-source build - run cmake from source directory
        # This matches OpenWrt's behavior and works with packages that use
        # relative paths in custom cmake commands
        cmake_args = [
            'cmake', '.',  # Configure in source directory
            '-DCMAKE_SYSTEM_NAME=Linux',
            f'-DCMAKE_C_COMPILER=/toolchain/bin/{target}-gcc',
            f'-DCMAKE_CXX_COMPILER=/toolchain/bin/{target}-g++',
            '-DCMAKE_BUILD_TYPE=Release',
            '-DCMAKE_INSTALL_PREFIX=/usr',
            '-DCMAKE_FIND_ROOT_PATH=/staging;/toolchain',  # Semicolon separates paths
            '-DCMAKE_FIND_ROOT_PATH_MODE_LIBRARY=ONLY',
            '-DCMAKE_FIND_ROOT_PATH_MODE_INCLUDE=ONLY',
        ] + pkg.build.get('configure_args', [])

        # Quote arguments properly to handle semicolons and special characters
        cmake_cmd = ' '.join(shlex.quote(arg) for arg in cmake_args)

        # Run cmake and make from source directory (in-source build)
        commands.append(['sh', '-c', f'cd /src && {cmake_cmd}'])
        commands.append(['sh', '-c', f'cd /src && make -j{self.jobs}'])
        commands.append(['sh', '-c', 'cd /src && make DESTDIR=/staging install'])
        commands.append(['sh', '-c', 'cd /src && make DESTDIR=/ipkg-install install'])

        return commands

    def _get_meson_isolation_commands(
        self,
        pkg: PackageConfig,
        env: Dict[str, str],
        src_dir: Path,
        staging_dir: Path,
    ) -> List[List[str]]:
        """Get commands to build a meson package in container.

        Generates a meson cross file with actual paths (since meson reads it directly)
        and runs meson setup/compile/install.
        """
        target = self.config.target_tuple
        arch = self.config.arch
        commands = []

        # Map architecture to meson cpu_family
        cpu_family_map = {
            'aarch64': 'aarch64',
            'arm': 'arm',
            'x86_64': 'x86_64',
            'i386': 'x86',
            'mips': 'mips',
            'mipsel': 'mips',
            'mips64': 'mips64',
            'powerpc': 'ppc',
            'riscv64': 'riscv64',
        }
        cpu_family = cpu_family_map.get(arch, arch)
        endian = self.config.cpu.get('endian', 'little')

        # Get actual paths for the cross file (meson reads file directly)
        toolchain_bin = str(self.toolchain.toolchain_dir / 'bin')
        staging_include = str(staging_dir / 'usr' / 'include')
        staging_lib = str(staging_dir / 'usr' / 'lib')
        staging_pkgconfig = f"{staging_lib}/pkgconfig:{staging_dir}/usr/share/pkgconfig"

        # CFLAGS/LDFLAGS with actual staging paths
        cflags = f"-Os -pipe -I{staging_include} -ffunction-sections -fdata-sections"
        ldflags = f"-L{staging_lib} -Wl,-rpath-link={staging_lib} -Wl,--gc-sections"

        # Create cross file content with real paths
        cross_file = src_dir / 'openwrt-cross.txt'
        with open(cross_file, 'w') as f:
            f.write(f'''[binaries]
c = '{toolchain_bin}/{target}-gcc'
cpp = '{toolchain_bin}/{target}-g++'
ar = '{toolchain_bin}/{target}-gcc-ar'
nm = '{toolchain_bin}/{target}-gcc-nm'
ld = '{toolchain_bin}/{target}-ld'
strip = '{toolchain_bin}/{target}-strip'
ranlib = '{toolchain_bin}/{target}-gcc-ranlib'
objcopy = '{toolchain_bin}/{target}-objcopy'
pkgconfig = 'pkg-config'

[built-in options]
c_args = [{', '.join(f"'{f}'" for f in cflags.split())}]
c_link_args = [{', '.join(f"'{f}'" for f in ldflags.split())}]
cpp_args = [{', '.join(f"'{f}'" for f in cflags.split())}]
cpp_link_args = [{', '.join(f"'{f}'" for f in ldflags.split())}]

[properties]
needs_exe_wrapper = true
sys_root = '{staging_dir}'
pkg_config_libdir = '{staging_pkgconfig}'

[host_machine]
system = 'linux'
cpu_family = '{cpu_family}'
cpu = '{arch}'
endian = '{endian}'
''')

        # Meson setup args - use actual paths (cross file already written)
        meson_args = [
            'meson', 'setup',
            '/src/build',
            '/src',
            f'--cross-file=/src/openwrt-cross.txt',
            '--prefix=/usr',
            '--buildtype=release',
            '--default-library=both',
        ] + pkg.build.get('configure_args', [])
        commands.append(meson_args)

        # Build with ninja
        commands.append(['ninja', '-C', '/src/build', f'-j{self.jobs}'])

        # Install to staging and ipkg-install
        commands.append(['sh', '-c', f'DESTDIR=/staging ninja -C /src/build install'])
        commands.append(['sh', '-c', f'DESTDIR=/ipkg-install ninja -C /src/build install'])

        return commands

    def _get_make_isolation_commands(self, pkg: PackageConfig, env: Dict[str, str]) -> List[List[str]]:
        """Get commands to build a make package in container."""
        import shlex
        target = self.config.target_tuple
        commands = []

        # Build make args string - quote arguments containing spaces
        make_args = pkg.build.get('make_args', [])
        make_extra_args = ' '.join(shlex.quote(arg) if ' ' in arg else arg for arg in make_args)
        make_install_args = pkg.build.get('make_install_args', [])
        make_install_extra = ' '.join(shlex.quote(arg) if ' ' in arg else arg for arg in make_install_args)

        # Get package-level env vars (for custom CFLAGS like kernel includes)
        pkg_env = pkg.build.get('env', {})

        # Get kernel version for include paths
        kernel_version = self.config.kernel.get('full_version', '6.12')
        kernel_linux_dir = f'/kernel/linux-{kernel_version}'

        # Use virtual paths that get substituted to per-package staging by _run_command
        # Base CFLAGS with staging include path for header files
        base_cflags = '-Os -pipe -I/staging/usr/include -ffunction-sections -fdata-sections'
        # Append any package-specific CFLAGS (e.g., kernel headers for fitblk)
        if 'CFLAGS' in pkg_env:
            # Replace ${LINUX_DIR} with the kernel path (virtual path that gets substituted)
            pkg_cflags = pkg_env['CFLAGS'].replace('${LINUX_DIR}', kernel_linux_dir)
            base_cflags = f"{base_cflags} {pkg_cflags}"
        cflags = base_cflags

        # LDFLAGS with staging lib path for libraries
        ldflags = '-L/staging/usr/lib -Wl,-rpath-link=/staging/usr/lib -Wl,--gc-sections'

        # Make needs to run from source directory
        # Wrap in sh -c to handle the cd properly
        make_cmd = (
            f"cd /src && make -j{self.jobs} "
            f"CC=/toolchain/bin/{target}-gcc "
            f"CXX=/toolchain/bin/{target}-g++ "
            f"CROSS_COMPILE={target}- "
            f"CFLAGS={shlex.quote(cflags)} "
            f"LDFLAGS={shlex.quote(ldflags)} "
            f"{make_extra_args}"
        )
        commands.append(['sh', '-c', make_cmd])

        # Install - use custom script if provided, otherwise make install
        install_script = pkg.build.get('install_script', '')
        if install_script:
            script = install_script.strip()
            install_cmd = f"cd /src && export DESTDIR=/ipkg-install && {script}"
        else:
            install_cmd = f"cd /src && make DESTDIR=/ipkg-install install {make_install_extra}"
        commands.append(['sh', '-c', install_cmd])

        return commands

    def _get_custom_isolation_commands(self, pkg: PackageConfig, env: Dict[str, str]) -> List[List[str]]:
        """Get commands to build a custom package in container.

        Provides kernel-related environment variables for wireless drivers etc:
        - LINUX_DIR: Path to kernel source/build directory
        - KERNEL_VERSION: Full kernel version (e.g., 6.12.65)
        - KERNEL_ARCH: Kernel architecture (e.g., arm64)
        - CROSS_COMPILE: Cross-compiler prefix
        - NPROC: Number of processors for parallel builds
        """
        commands = []

        # Get kernel info for wireless packages
        kernel_version = self.config.kernel['full_version']
        kernel_arch = {
            'aarch64': 'arm64',
            'arm': 'arm',
            'x86_64': 'x86',
            'i386': 'x86',
        }.get(self.config.arch, self.config.arch)

        # Environment setup for custom scripts
        env_setup = (
            f'export LINUX_DIR=/kernel/linux-{kernel_version} && '
            f'export KERNEL_VERSION={kernel_version} && '
            f'export KERNEL_ARCH={kernel_arch} && '
            f'export CROSS_COMPILE={self.config.cross_compile} && '
            f'export NPROC={self.jobs} && '
            f'export DESTDIR=/ipkg-install && '
        )

        configure_script = pkg.build.get('configure_script', '')
        if configure_script:
            # Keep newlines for proper shell execution (comments need newlines)
            script = configure_script.strip()
            commands.append(['sh', '-c', f'{env_setup} cd /src && {script}'])

        compile_script = pkg.build.get('compile_script', '')
        if compile_script:
            script = compile_script.strip()
            commands.append(['sh', '-c', f'{env_setup} cd /src && {script}'])

        install_script = pkg.build.get('install_script', '')
        if install_script:
            script = install_script.strip()
            commands.append(['sh', '-c', f'{env_setup} cd /src && {script}'])

        return commands

    def _build_kmod(self, pkg: PackageConfig, src_dir: Path, build_dir: Path, env: Dict[str, str]):
        """Build an out-of-tree kernel module.

        This builds kernel modules against the kernel source tree using:
        - ARCH and CROSS_COMPILE from toolchain
        - Standard out-of-tree build: make -C <kernel_dir> M=<module_dir>
        """
        kernel_dir = self.config.kernel_build_dir / f'linux-{self.config.kernel["full_version"]}'

        # Get kernel architecture
        kernel_arch = {
            'aarch64': 'arm64',
            'arm': 'arm',
            'x86_64': 'x86',
            'i386': 'x86',
        }.get(self.config.arch, self.config.arch)

        # Build kernel module - standard out-of-tree module build
        make_args = pkg.build.get('make_args', [])
        make_cmd = [
            'make',
            '-C', str(kernel_dir),
            f'-j{self.jobs}',
            f'ARCH={kernel_arch}',
            f'CROSS_COMPILE={self.config.cross_compile}',
            f'M={src_dir}',
        ]

        # Add any kmod-specific defines (e.g., CONFIG_MT798X_WMAC)
        kmod_defines = pkg.build.get('kmod_defines', [])
        if kmod_defines:
            make_cmd.append(f'NOSTDINC_FLAGS={" ".join("-D" + d for d in kmod_defines)}')

        make_cmd.extend(make_args)
        make_cmd.append('modules')

        run_command(make_cmd, env=env, verbose=self.verbose)

    def _get_kmod_isolation_commands(self, pkg: PackageConfig, env: Dict[str, str]) -> List[List[str]]:
        """Get commands to build an out-of-tree kernel module in container.

        Uses the kernel build directory mounted at /kernel.
        Uses standard out-of-tree module build: make -C <kernel_dir> M=<module_dir>
        """
        kernel_version = self.config.kernel['full_version']
        kernel_dir = f'/kernel/linux-{kernel_version}'

        # Get kernel architecture
        kernel_arch = {
            'aarch64': 'arm64',
            'arm': 'arm',
            'x86_64': 'x86',
            'i386': 'x86',
        }.get(self.config.arch, self.config.arch)

        commands = []

        # Build kernel module - standard out-of-tree module build
        # Uses make -C <kernel_dir> M=<module_src_dir> modules
        make_args = pkg.build.get('make_args', [])
        make_args_str = ' '.join(make_args)

        # Add kmod defines and CONFIG options for the module Makefile
        kmod_defines = pkg.build.get('kmod_defines', [])
        nostdinc = ''
        if kmod_defines:
            nostdinc = f'NOSTDINC_FLAGS="{" ".join("-D" + d for d in kmod_defines)}"'

        # Standard out-of-tree module build command
        make_cmd = (
            f"make -C {kernel_dir} "
            f"-j{self.jobs} "
            f"ARCH={kernel_arch} "
            f"CROSS_COMPILE={self.config.cross_compile} "
            f"M=/src "
            f"{nostdinc} "
            f"{make_args_str} "
            f"modules"
        )
        commands.append(['sh', '-c', make_cmd])

        # Install modules to ipkg-install
        install_cmd = (
            f"make -C {kernel_dir} "
            f"ARCH={kernel_arch} "
            f"CROSS_COMPILE={self.config.cross_compile} "
            f"M=/src "
            f"INSTALL_MOD_PATH=/ipkg-install "
            f"modules_install"
        )
        commands.append(['sh', '-c', install_cmd])

        return commands

    def _ensure_source_compiled(self, source_name: str, force: bool = False):
        """Ensure a source package is compiled (for subpackage builds).

        This is used when building subpackages - the source must be compiled
        before the subpackage's files can be extracted.

        Uses hash-based stamps for change detection.
        """
        # Load source package directly (not as subpackage)
        poc_dir = Path(__file__).parent.parent
        pkg_dir = poc_dir / 'packages' / source_name
        if not pkg_dir.exists() or not (pkg_dir / 'package.yaml').exists():
            print(f"      Warning: Source package {source_name} not found")
            return

        pkg = PackageConfig.load(pkg_dir)

        # Compute hash for change detection
        pkg_hash = self._compute_package_hash(pkg)
        compiled_stamp = self.stamp_dir / f'{source_name}.compiled_{pkg_hash}'

        if not force and compiled_stamp.exists():
            return  # Source already compiled with current hash

        # Clean old stamps with different hashes
        self._clean_old_stamps(source_name, 'compiled')

        print(f"    {source_name}: compiling source (hash {pkg_hash})...")

        # Clean old build artifacts when hash changed
        pkg_build_base = self.packages_dir / source_name
        if pkg_build_base.exists():
            shutil.rmtree(pkg_build_base)

        # Create build directory and per-package install directory
        build_dir = self.packages_dir / source_name / 'build'
        pkg_install_dir = self.packages_dir / source_name / 'ipkg-install'
        build_dir.mkdir(parents=True, exist_ok=True)
        pkg_install_dir.mkdir(parents=True, exist_ok=True)

        # Download and extract source
        src_dir = self._prepare_source(pkg, build_dir.parent)

        # Build using appropriate build system
        build_system = pkg.build_system
        env = self._get_build_env(pkg)

        if build_system == 'autotools':
            self._build_autotools(pkg, src_dir, build_dir, env)
        elif build_system == 'cmake':
            self._build_cmake(pkg, src_dir, build_dir, env)
        elif build_system == 'meson':
            self._build_meson(pkg, src_dir, build_dir, env)
        elif build_system == 'make':
            self._build_make(pkg, src_dir, build_dir, env)
        elif build_system == 'custom':
            self._build_custom(pkg, src_dir, build_dir, env, pkg_install_dir)
        elif build_system == 'none':
            pass  # No build needed
        else:
            print(f"      Unknown build system: {build_system}")
            return

        # Install to per-package directory and shared staging
        self._install_package(pkg, src_dir, build_dir, env, pkg_install_dir)

        compiled_stamp.touch()
        self._built.add(source_name)

    def _prepare_source(self, pkg: PackageConfig, pkg_dir: Path) -> Path:
        """Download and extract package source."""
        source = pkg.source
        src_type = source.get('type', 'tarball')

        if src_type == 'none':
            # Virtual package or no source needed
            src_dir = pkg_dir / 'src'
            src_dir.mkdir(parents=True, exist_ok=True)
            return src_dir

        elif src_type == 'tarball':
            url = source.get('url', '')
            if not url:
                # No external source, use local files
                return pkg.pkg_dir / 'files'

            # Download
            filename = url.split('/')[-1]
            tarball = self.dl_dir / filename
            self.dl_dir.mkdir(parents=True, exist_ok=True)

            if not tarball.exists():
                expected_hash = source.get('sha256')
                download_file(url, tarball, expected_hash)

            # Extract
            src_dir = pkg_dir / 'src'
            if not src_dir.exists():
                extract_archive(tarball, pkg_dir)
                # Find extracted directory
                dirs = [d for d in pkg_dir.iterdir() if d.is_dir() and d.name != 'build']
                if dirs:
                    dirs[0].rename(src_dir)

                # Apply patches from patches/ directory if it exists
                patches_path = pkg.pkg_dir / 'patches'
                if patches_path.exists():
                    apply_patches(src_dir, patches_path, verbose=self.verbose)

            return src_dir

        elif src_type == 'git':
            url = source.get('url', '')
            version = source.get('version', 'HEAD')
            src_dir = pkg_dir / 'src'

            if not src_dir.exists():
                try:
                    # Clone the repository
                    run_command(['git', 'clone', '--depth=1', url, str(src_dir)],
                               verbose=self.verbose)
                    # Checkout specific version if specified
                    if version and version != 'HEAD':
                        run_command(['git', 'fetch', '--depth=1', 'origin', version],
                                   cwd=src_dir, verbose=self.verbose)
                        run_command(['git', 'checkout', version],
                                   cwd=src_dir, verbose=self.verbose)

                    # Initialize submodules if .gitmodules exists
                    if (src_dir / '.gitmodules').exists():
                        run_command(['git', 'submodule', 'update', '--init', '--recursive', '--depth=1'],
                                   cwd=src_dir, verbose=self.verbose)

                    # Apply patches from patches/ directory if it exists
                    patches_path = pkg.pkg_dir / 'patches'
                    if patches_path.exists():
                        apply_patches(src_dir, patches_path, verbose=self.verbose)
                except Exception as e:
                    # Clean up partial clone on failure
                    if src_dir.exists():
                        shutil.rmtree(src_dir)
                    raise

            return src_dir

        elif src_type == 'local':
            # Local packages have source in src/ subdirectory (OpenWrt convention)
            # files/ contains additional files to install, not source code
            src_dir = pkg.pkg_dir / 'src'
            if src_dir.exists():
                return src_dir
            # Fallback to files/ for backwards compatibility
            return pkg.pkg_dir / 'files'

        else:
            return pkg.pkg_dir / 'files'

    def _get_build_env(
        self,
        pkg: PackageConfig,
        pkg_staging_dir: Optional[Path] = None,
    ) -> dict:
        """Get environment for package builds.

        Aligned with OpenWrt's TARGET_CFLAGS, TARGET_LDFLAGS, TARGET_CPPFLAGS
        from rules.mk and include/package.mk.

        Note: When using fakechroot isolation, additional env vars are set
        in FakechrootIsolation._run_in_fakechroot() to point to the isolated
        filesystem paths.

        Args:
            pkg: Package configuration
            pkg_staging_dir: Per-package staging directory
        """
        env = self.toolchain.get_env()
        toolchain_dir = self.toolchain.toolchain_dir
        target = self.config.target_tuple

        # Use staging directory (fallback paths - fakechroot overrides these)
        include_dir = self.staging_dir / 'usr' / 'include'
        lib_dir = self.staging_dir / 'usr' / 'lib'
        pkgconfig_dir = self.staging_dir / 'usr' / 'lib' / 'pkgconfig'
        sysroot_dir = self.staging_dir

        # Use per-package staging if provided
        staging_dir = pkg_staging_dir if pkg_staging_dir else self.staging_dir

        # Staging directory paths (like OpenWrt's STAGING_DIR)
        env['STAGING_DIR'] = str(staging_dir)
        env['STAGING_PREFIX'] = f"{staging_dir}/usr"
        # Shared staging for development headers from other packages (mac80211-backport, etc)
        env['SHARED_STAGING_DIR'] = str(self.staging_dir)

        # TARGET_CPPFLAGS - preprocessor flags (separate from CFLAGS in OpenWrt)
        target_cppflags = f"-I{include_dir}"
        env['CPPFLAGS'] = target_cppflags

        # TARGET_CFLAGS - compiler flags
        # Base optimization matching OpenWrt defaults
        target_cflags = env.get('CFLAGS', '-Os -pipe')
        target_cflags += f" -I{include_dir}"
        # Add libnl-tiny include path (OpenWrt packages commonly need this)
        target_cflags += f" -I{include_dir}/libnl-tiny"
        # Add gc-sections for size optimization (like PKG_BUILD_FLAGS+=gc-sections)
        target_cflags += " -ffunction-sections -fdata-sections"
        env['CFLAGS'] = target_cflags

        # TARGET_CXXFLAGS
        target_cxxflags = env.get('CXXFLAGS', '-Os -pipe')
        target_cxxflags += f" -I{include_dir}"
        # Add libnl-tiny include path (OpenWrt packages commonly need this)
        target_cxxflags += f" -I{include_dir}/libnl-tiny"
        target_cxxflags += " -ffunction-sections -fdata-sections"
        env['CXXFLAGS'] = target_cxxflags

        # TARGET_LDFLAGS - linker flags
        target_ldflags = env.get('LDFLAGS', '')
        target_ldflags += f" -L{lib_dir}"
        target_ldflags += f" -L{toolchain_dir}/lib"
        # rpath-link helps the linker find shared libraries for transitive dependencies
        target_ldflags += f" -Wl,-rpath-link={lib_dir}"
        # gc-sections linker flag
        target_ldflags += " -Wl,--gc-sections"
        env['LDFLAGS'] = target_ldflags.strip()

        # PKG_CONFIG paths (OpenWrt uses staging/usr/lib/pkgconfig)
        # PKG_CONFIG_SYSROOT_DIR tells pkg-config to prepend this to all paths
        env['PKG_CONFIG_PATH'] = str(pkgconfig_dir)
        env['PKG_CONFIG_LIBDIR'] = str(pkgconfig_dir)
        env['PKG_CONFIG_SYSROOT_DIR'] = str(sysroot_dir)

        # Prevent git from looking outside build directory
        env['GIT_CEILING_DIRECTORIES'] = str(self.config.build_dir)

        # Kernel build directory (for out-of-tree kernel modules)
        env['KERNEL_BUILD_DIR'] = str(self.config.kernel_build_dir)

        # Add package-specific environment
        for key, value in pkg.build.get('env', {}).items():
            env[key] = value

        return env

    def _get_host_tuple(self) -> str:
        """Get the build host's GNU tuple."""
        try:
            result = subprocess.run(['gcc', '-dumpmachine'],
                                    capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception:
            pass
        return 'x86_64-linux-gnu'

    def _get_configure_opts(self, env: dict) -> dict:
        """Get TARGET_CONFIGURE_OPTS as environment variables.

        These match OpenWrt's TARGET_CONFIGURE_OPTS from rules.mk.
        """
        env = env.copy()
        target = self.config.target_tuple
        toolchain_bin = self.toolchain.toolchain_dir / 'bin'

        # Full tool paths matching OpenWrt's TARGET_CONFIGURE_OPTS
        env['AR'] = f'{toolchain_bin}/{target}-gcc-ar'
        env['AS'] = f'{toolchain_bin}/{target}-gcc'
        env['LD'] = f'{toolchain_bin}/{target}-ld'
        env['NM'] = f'{toolchain_bin}/{target}-gcc-nm'
        env['CC'] = f'{toolchain_bin}/{target}-gcc'
        env['GCC'] = f'{toolchain_bin}/{target}-gcc'
        env['CXX'] = f'{toolchain_bin}/{target}-g++'
        env['RANLIB'] = f'{toolchain_bin}/{target}-gcc-ranlib'
        env['STRIP'] = f'{toolchain_bin}/{target}-strip'
        env['OBJCOPY'] = f'{toolchain_bin}/{target}-objcopy'
        env['OBJDUMP'] = f'{toolchain_bin}/{target}-objdump'
        env['SIZE'] = f'{toolchain_bin}/{target}-size'

        return env

    def _build_autotools(self, pkg: PackageConfig, src_dir: Path, build_dir: Path, env: dict):
        """Build with autotools (configure && make).

        Aligned with OpenWrt's CONFIGURE_ARGS from include/package-defaults.mk.
        """
        configure = src_dir / 'configure'
        if not configure.exists():
            # Try running autoreconf
            if (src_dir / 'configure.ac').exists():
                run_command(['autoreconf', '-fi'], cwd=src_dir, env=env, verbose=self.verbose)

        if not configure.exists():
            print(f"      No configure script found")
            return

        target = self.config.target_tuple
        host_tuple = self._get_host_tuple()

        # CONFIGURE_ARGS matching OpenWrt's include/package-defaults.mk
        configure_args = [
            str(configure),
            f'--target={target}',
            f'--host={target}',
            f'--build={host_tuple}',
            '--disable-dependency-tracking',
            '--program-prefix=',
            '--program-suffix=',
            '--prefix=/usr',
            '--exec-prefix=/usr',
            '--bindir=/usr/bin',
            '--sbindir=/usr/sbin',
            '--libexecdir=/usr/lib',
            '--sysconfdir=/etc',
            '--datadir=/usr/share',
            '--localstatedir=/var',
            '--mandir=/usr/man',
            '--infodir=/usr/info',
            f'--with-sysroot={self.staging_dir}',
            '--disable-nls',
        ] + pkg.build.get('configure_args', [])

        # Add TARGET_CONFIGURE_OPTS to environment
        configure_env = self._get_configure_opts(env)

        run_command(configure_args, cwd=build_dir, env=configure_env, verbose=self.verbose)

        # Build with MAKE_FLAGS
        make_args = ['make', f'-j{self.jobs}'] + pkg.build.get('make_args', [])
        run_command(make_args, cwd=build_dir, env=configure_env, verbose=self.verbose)

    def _build_cmake(self, pkg: PackageConfig, src_dir: Path, build_dir: Path, env: dict):
        """Build with CMake.

        Aligned with OpenWrt's include/cmake.mk Build/Configure/Default.
        Uses in-source builds to match OpenWrt behavior (CMAKE_BINARY_DIR = CMAKE_SOURCE_DIR).
        """
        toolchain_dir = self.toolchain.toolchain_dir
        toolchain_bin = toolchain_dir / 'bin'
        target = self.config.target_tuple

        # CMAKE_FIND_ROOT_PATH matching OpenWrt
        cmake_find_root = f"{self.staging_dir}/usr;{toolchain_dir}"

        # Linker flags with symbolic functions (OpenWrt default)
        ldflags = env.get('LDFLAGS', '')
        shared_ldflags = f"{ldflags} -Wl,-Bsymbolic-functions"

        # CFLAGS for cmake (includes staging includes like libnl-tiny)
        cflags = env.get('CFLAGS', '')
        cxxflags = env.get('CXXFLAGS', '')

        # Use in-source builds like OpenWrt (CMAKE_BINARY_DIR = CMAKE_SOURCE_DIR)
        cmake_args = [
            'cmake',
            '.',  # Build in source directory
            '--no-warn-unused-cli',
            # Cross-compilation core settings
            '-DCMAKE_SYSTEM_NAME=Linux',
            '-DCMAKE_SYSTEM_VERSION=1',
            f'-DCMAKE_SYSTEM_PROCESSOR={self.config.arch}',
            '-DCMAKE_BUILD_TYPE=Release',
            f'-DCMAKE_C_FLAGS:STRING={cflags}',
            f'-DCMAKE_CXX_FLAGS:STRING={cxxflags}',
            '-DCMAKE_C_FLAGS_RELEASE=-DNDEBUG',
            '-DCMAKE_CXX_FLAGS_RELEASE=-DNDEBUG',
            # Compilers with full paths
            f'-DCMAKE_C_COMPILER={toolchain_bin}/{target}-gcc',
            f'-DCMAKE_CXX_COMPILER={toolchain_bin}/{target}-g++',
            f'-DCMAKE_ASM_COMPILER={toolchain_bin}/{target}-gcc',
            # Tools for LTO support (gcc-ar, gcc-nm, gcc-ranlib)
            f'-DCMAKE_AR={toolchain_bin}/{target}-gcc-ar',
            f'-DCMAKE_NM={toolchain_bin}/{target}-gcc-nm',
            f'-DCMAKE_RANLIB={toolchain_bin}/{target}-gcc-ranlib',
            # Linker flags
            f'-DCMAKE_EXE_LINKER_FLAGS:STRING={ldflags}',
            f'-DCMAKE_MODULE_LINKER_FLAGS:STRING={shared_ldflags}',
            f'-DCMAKE_SHARED_LINKER_FLAGS:STRING={shared_ldflags}',
            # Cross-compilation search paths
            f'-DCMAKE_FIND_ROOT_PATH={cmake_find_root}',
            '-DCMAKE_FIND_ROOT_PATH_MODE_PROGRAM=NEVER',
            '-DCMAKE_FIND_ROOT_PATH_MODE_LIBRARY=ONLY',
            '-DCMAKE_FIND_ROOT_PATH_MODE_INCLUDE=ONLY',
            # Installation settings
            '-DCMAKE_STRIP=:',  # Disable cmake stripping
            '-DCMAKE_INSTALL_PREFIX=/usr',
            f'-DCMAKE_PREFIX_PATH={self.staging_dir}',
            '-DCMAKE_SKIP_RPATH=TRUE',
            # Package registry (disabled for reproducibility)
            '-DCMAKE_EXPORT_PACKAGE_REGISTRY=FALSE',
            '-DCMAKE_EXPORT_NO_PACKAGE_REGISTRY=TRUE',
            '-DCMAKE_FIND_USE_PACKAGE_REGISTRY=FALSE',
            '-DCMAKE_FIND_PACKAGE_NO_PACKAGE_REGISTRY=TRUE',
            '-DCMAKE_FIND_USE_SYSTEM_PACKAGE_REGISTRY=FALSE',
            '-DCMAKE_FIND_PACKAGE_NO_SYSTEM_PACKAGE_REGISTRY=TRUE',
        ] + pkg.build.get('configure_args', [])

        # Run cmake from source directory (in-source build)
        run_command(cmake_args, cwd=src_dir, env=env, verbose=self.verbose)

        # Build (use make; ninja would require checking if available)
        # Support compile_targets to build only specific targets
        compile_targets = pkg.build.get('compile_targets', [])
        if compile_targets:
            # Build specific targets only
            for target in compile_targets:
                run_command(['make', f'-j{self.jobs}', target], cwd=src_dir, env=env, verbose=self.verbose)
        else:
            # Build all targets
            run_command(['make', f'-j{self.jobs}'], cwd=src_dir, env=env, verbose=self.verbose)

    def _build_meson(self, pkg: PackageConfig, src_dir: Path, build_dir: Path, env: dict):
        """Build with Meson.

        Cross-compilation file aligned with OpenWrt's meson template.
        """
        target = self.config.target_tuple
        toolchain_bin = self.toolchain.toolchain_dir / 'bin'
        arch = self.config.arch

        # Map architecture to meson cpu_family
        cpu_family_map = {
            'aarch64': 'aarch64',
            'arm': 'arm',
            'x86_64': 'x86_64',
            'i386': 'x86',
            'mips': 'mips',
            'mipsel': 'mips',
            'mips64': 'mips64',
            'powerpc': 'ppc',
            'riscv64': 'riscv64',
        }
        cpu_family = cpu_family_map.get(arch, arch)

        # Get CFLAGS/LDFLAGS from environment
        cflags = env.get('CFLAGS', '').split()
        cppflags = env.get('CPPFLAGS', '').split()
        cxxflags = env.get('CXXFLAGS', '').split()
        ldflags = env.get('LDFLAGS', '').split()

        # Format as meson array: ['flag1', 'flag2', ...]
        def meson_array(flags):
            return '[' + ', '.join(f"'{f}'" for f in flags if f) + ']'

        # Create cross file with full tool paths and flags
        cross_file = build_dir / 'cross.txt'
        with open(cross_file, 'w') as f:
            f.write(f"""[binaries]
c = '{toolchain_bin}/{target}-gcc'
cpp = '{toolchain_bin}/{target}-g++'
ar = '{toolchain_bin}/{target}-gcc-ar'
nm = '{toolchain_bin}/{target}-gcc-nm'
ld = '{toolchain_bin}/{target}-ld'
strip = '{toolchain_bin}/{target}-strip'
ranlib = '{toolchain_bin}/{target}-gcc-ranlib'
objcopy = '{toolchain_bin}/{target}-objcopy'
pkgconfig = 'pkg-config'

[built-in options]
c_args = {meson_array(cflags + cppflags)}
c_link_args = {meson_array(ldflags)}
cpp_args = {meson_array(cxxflags + cppflags)}
cpp_link_args = {meson_array(ldflags)}

[properties]
needs_exe_wrapper = true
sys_root = '{self.staging_dir}'
pkg_config_libdir = '{self.staging_dir}/usr/lib/pkgconfig:{self.staging_dir}/usr/share/pkgconfig'

[host_machine]
system = 'linux'
cpu_family = '{cpu_family}'
cpu = '{arch}'
endian = '{self.config.cpu.get("endian", "little")}'
""")

        meson_args = [
            'meson', 'setup',
            str(build_dir),
            str(src_dir),
            f'--cross-file={cross_file}',
            '--prefix=/usr',
            '--buildtype=release',
            '--default-library=both',
        ] + pkg.build.get('configure_args', [])

        run_command(meson_args, env=env, verbose=self.verbose)

        # Build
        run_command(['ninja', '-C', str(build_dir), f'-j{self.jobs}'],
                    env=env, verbose=self.verbose)

    def _build_make(self, pkg: PackageConfig, src_dir: Path, build_dir: Path, env: dict):
        """Build with plain make.

        Aligned with OpenWrt's MAKE_FLAGS and TARGET_CONFIGURE_OPTS.
        Passes CFLAGS and LDFLAGS on the command line like OpenWrt does.
        """
        target = self.config.target_tuple
        toolchain_bin = self.toolchain.toolchain_dir / 'bin'

        # Merge CPPFLAGS into CFLAGS for make (OpenWrt convention)
        cppflags = env.get('CPPFLAGS', '')
        cflags = f"{env.get('CFLAGS', '')} {cppflags}".strip()
        cxxflags = f"{env.get('CXXFLAGS', '')} {cppflags}".strip()
        ldflags = env.get('LDFLAGS', '')

        # MAKE_FLAGS on command line (like OpenWrt's $(TARGET_CONFIGURE_OPTS))
        make_args = [
            'make',
            f'-j{self.jobs}',
            # Full tool paths with gcc-ar/gcc-nm/gcc-ranlib for LTO
            f'CC={toolchain_bin}/{target}-gcc',
            f'CXX={toolchain_bin}/{target}-g++',
            f'AR={toolchain_bin}/{target}-gcc-ar',
            f'AS={toolchain_bin}/{target}-gcc',
            f'LD={toolchain_bin}/{target}-ld',
            f'NM={toolchain_bin}/{target}-gcc-nm',
            f'RANLIB={toolchain_bin}/{target}-gcc-ranlib',
            f'STRIP={toolchain_bin}/{target}-strip',
            f'OBJCOPY={toolchain_bin}/{target}-objcopy',
            f'OBJDUMP={toolchain_bin}/{target}-objdump',
            # CROSS and CROSS_COMPILE for packages that use them
            f'CROSS={target}-',
            f'CROSS_COMPILE={target}-',
            # Architecture (some Makefiles need this)
            f'ARCH={self.config.arch}',
            # Pass CFLAGS/LDFLAGS on command line (like OpenWrt MAKE_FLAGS)
            f'CFLAGS={cflags}',
            f'CXXFLAGS={cxxflags}',
            f'LDFLAGS={ldflags}',
        ] + pkg.build.get('make_args', [])

        run_command(make_args, cwd=src_dir, env=env, verbose=self.verbose)

    def _build_custom(self, pkg: PackageConfig, src_dir: Path, build_dir: Path, env: dict, pkg_install_dir: Optional[Path] = None):
        """Build with custom script."""
        # Set up additional environment variables for scripts
        env = env.copy()
        env['JOBS'] = str(self.jobs)
        env['CROSS_COMPILE'] = self.config.cross_compile
        env['DESTDIR'] = str(self.staging_dir)
        env['OPENWRT_DIR'] = str(self.config.openwrt_dir)
        # Add per-package install directory for APK packaging
        if pkg_install_dir:
            env['PKG_INSTALL_DIR'] = str(pkg_install_dir)

        # Run configure script if present
        configure_script = pkg.build.get('configure_script', '')
        if configure_script:
            run_command(['sh', '-c', configure_script], cwd=src_dir, env=env, verbose=self.verbose)

        # Run compile script
        compile_script = pkg.build.get('compile_script', '')
        if compile_script:
            run_command(['sh', '-c', compile_script], cwd=src_dir, env=env, verbose=self.verbose)

        # Run install script if present
        install_script = pkg.build.get('install_script', '')
        if install_script:
            run_command(['sh', '-c', install_script], cwd=src_dir, env=env, verbose=self.verbose)

    def _install_package(
        self,
        pkg: PackageConfig,
        src_dir: Path,
        build_dir: Path,
        env: dict,
        pkg_install_dir: Optional[Path] = None,
        pkg_staging_dir: Optional[Path] = None,
    ):
        """Install package to staging directory and optionally to per-package directory.

        Args:
            pkg: Package configuration
            src_dir: Source directory
            build_dir: Build directory
            env: Build environment
            pkg_install_dir: Per-package install directory for APK creation
            pkg_staging_dir: Per-package staging directory (isolated mode)
        """
        install = pkg.install

        # Determine staging directory (per-package in isolated mode, shared otherwise)
        staging_dir = pkg_staging_dir if pkg_staging_dir else self.staging_dir

        # Directories to install to (staging + optional per-package install)
        install_dirs = [staging_dir]
        if pkg_install_dir:
            install_dirs.append(pkg_install_dir)

        # Create staging subdirectories
        for install_dir in install_dirs:
            (install_dir / 'usr' / 'bin').mkdir(parents=True, exist_ok=True)
            (install_dir / 'usr' / 'lib').mkdir(parents=True, exist_ok=True)
            (install_dir / 'usr' / 'include').mkdir(parents=True, exist_ok=True)

        # Run make install if applicable
        build_system = pkg.build_system
        if build_system in ('autotools', 'cmake', 'make'):
            # For cmake with in-source builds, run from src_dir; otherwise use build_dir
            make_cwd = src_dir if build_system == 'cmake' else build_dir

            # Install to staging (for development files)
            make_install_args = ['make', f'DESTDIR={staging_dir}', 'install'] + pkg.build.get('make_install_args', [])
            try:
                run_command(make_install_args, cwd=make_cwd, env=env, verbose=self.verbose)
            except Exception as e:
                if self.verbose:
                    print(f"      make install failed: {e}")

            # Fix libtool .la files in staging to use absolute paths
            # (libtool generates sysroot-relative paths that cause linking issues)
            self._fix_libtool_files(staging_dir)

            # Also install to per-package dir for APK
            if pkg_install_dir:
                make_install_args = ['make', f'DESTDIR={pkg_install_dir}', 'install'] + pkg.build.get('make_install_args', [])
                try:
                    run_command(make_install_args, cwd=make_cwd, env=env, verbose=self.verbose)
                except Exception:
                    pass  # Ignore errors for per-package install

        # Install development files to staging (mirrors OpenWrt's Build/InstallDev)
        # This ensures headers, libraries, and pkg-config files are available for dependent packages
        if pkg_install_dir:
            self._install_dev(pkg, pkg_install_dir, staging_dir)

        # Install explicit files from package.yaml
        for file_spec in install.get('files', []):
            src_path = file_spec['src']
            # Replace variables
            src_path = src_path.replace('${build_dir}', str(build_dir))
            src_path = src_path.replace('${src_dir}', str(src_dir))
            src_path = src_path.replace('${pkg_dir}', str(pkg.pkg_dir))
            src_path = src_path.replace('${toolchain_dir}', str(self.toolchain.toolchain_dir))
            src = Path(src_path)

            for install_dir in install_dirs:
                dst = install_dir / file_spec['dst'].lstrip('/')
                dst.parent.mkdir(parents=True, exist_ok=True)
                if src.exists():
                    shutil.copy2(src, dst)
                    mode = file_spec.get('mode')
                    if mode:
                        dst.chmod(int(mode, 8))

        # Install directories from install.dirs
        for dir_spec in install.get('dirs', []):
            for install_dir in install_dirs:
                (install_dir / dir_spec.lstrip('/')).mkdir(parents=True, exist_ok=True)

        # Install headers from install.headers (to shared staging only, not in APK)
        for header_spec in install.get('headers', []):
            src_path = header_spec['src']
            src_path = src_path.replace('${build_dir}', str(build_dir))
            src_path = src_path.replace('${src_dir}', str(src_dir))
            src = Path(src_path)
            dst = self.staging_dir / header_spec['dst'].lstrip('/')
            dst.parent.mkdir(parents=True, exist_ok=True)
            if src.exists():
                shutil.copy2(src, dst)

        # Create symlinks from install.symlinks
        for link_spec in install.get('symlinks', []):
            for install_dir in install_dirs:
                link_target = link_spec['src']  # What the link points to
                link_path = install_dir / link_spec['dst'].lstrip('/')  # Where the link is created
                link_path.parent.mkdir(parents=True, exist_ok=True)
                if link_path.is_symlink():
                    link_path.unlink()
                elif link_path.is_dir():
                    shutil.rmtree(link_path)
                elif link_path.exists():
                    link_path.unlink()
                try:
                    link_path.symlink_to(link_target)
                except Exception:
                    pass

        # Install from package files directory and target overlays
        # Order: package files -> target overlay -> subtarget overlay
        # Later directories override earlier ones
        files_sources = []

        # Package's own files directory
        pkg_files_dir = pkg.pkg_dir / 'files'
        if pkg_files_dir.exists():
            files_sources.append(pkg_files_dir)

        # Target overlay directories (in priority order)
        files_sources.extend(self._get_target_overlay_dirs(pkg.name))

        # Copy files from all sources (later sources override earlier)
        for files_dir in files_sources:
            for src_file in files_dir.rglob('*'):
                if src_file.is_file():
                    rel_path = src_file.relative_to(files_dir)
                    for install_dir in install_dirs:
                        dst_file = install_dir / rel_path
                        dst_file.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(src_file, dst_file)

        # Install to shared staging from install.staging (for development headers/libs)
        # This is for packages that install headers for other packages to build against
        for staging_spec in install.get('staging', []):
            src_pattern = staging_spec['src']
            dst_path = staging_spec['dst']

            # Substitute variables in source path
            src_pattern = src_pattern.replace('${build_dir}', str(build_dir))
            src_pattern = src_pattern.replace('${src_dir}', str(src_dir))
            src_pattern = src_pattern.replace('${pkg_dir}', str(pkg.pkg_dir))

            # Determine the base directory for glob matching
            if src_pattern.startswith('/'):
                # Absolute path - use as is
                base_dir = Path('/')
                pattern = src_pattern[1:]
            else:
                # Relative to source directory
                base_dir = src_dir
                pattern = src_pattern

            # Handle glob patterns
            dst_base = self.staging_dir / dst_path.lstrip('/')
            if '*' in pattern:
                # Glob pattern - find all matching files
                for src_file in base_dir.glob(pattern):
                    if src_file.is_file():
                        dst_file = dst_base / src_file.name
                        dst_file.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(src_file, dst_file)
                        if self.verbose:
                            print(f"      staging: {src_file.name} -> {dst_path}")
                    elif src_file.is_dir():
                        # Copy entire directory
                        dst_dir = dst_base
                        dst_dir.mkdir(parents=True, exist_ok=True)
                        self._copy_tree(src_file, dst_dir)
                        if self.verbose:
                            print(f"      staging: {src_file.name}/ -> {dst_path}")
            else:
                # Direct path (may be file or directory)
                src_path = base_dir / pattern
                if src_path.is_dir():
                    # Copy entire directory contents
                    dst_base.mkdir(parents=True, exist_ok=True)
                    self._copy_tree(src_path, dst_base)
                    if self.verbose:
                        print(f"      staging: {pattern}/ -> {dst_path}")
                elif src_path.is_file():
                    dst_file = dst_base / src_path.name
                    dst_file.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src_path, dst_file)
                    if self.verbose:
                        print(f"      staging: {pattern} -> {dst_path}")

    def _create_apk_package(self, pkg: Union[PackageConfig, SubpackageConfig], pkg_install_dir: Path):
        """Create APK package from installed files."""
        if not self._apk_packager:
            return

        apk_file = self._apk_packager.create_package(pkg, self.packages_dir / pkg.name, pkg_install_dir)
        if apk_file:
            self._apk_files.append(apk_file)
            # Add to repository
            if self._apk_repo:
                self._apk_repo.add_package(apk_file)
            print(f"      Created: {apk_file.name}")

    def _build_subpackage(self, pkg: SubpackageConfig, create_apk: bool = True):
        """Build a subpackage by filtering files from the parent's install directory.

        Subpackages share the source build with their parent package. This method:
        1. Locates the parent package's ipkg-install directory
        2. Filters files according to the subpackage's 'files' specification
        3. Creates an APK containing only those files
        """
        source_name = pkg.source_name
        parent_install_dir = self.packages_dir / source_name / 'ipkg-install'

        if not parent_install_dir.exists():
            print(f"      Warning: Parent install dir not found: {parent_install_dir}")
            return

        if not create_apk:
            return

        # Get paths for variable substitution
        pkg_dir = pkg.parent.pkg_dir
        src_dir = self.packages_dir / source_name / 'src'
        build_dir = self.packages_dir / source_name / 'build'

        # Create a temporary directory with only the subpackage's files
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_install = Path(tmpdir)

            # Copy files matching the subpackage's file patterns
            files_spec = pkg.install.get('files', [])
            for file_entry in files_spec:
                src_pattern = file_entry.get('src', '')
                dst_path = file_entry.get('dst', '')
                file_mode = file_entry.get('mode')

                if not src_pattern:
                    continue

                # Handle variable substitution (e.g., ${pkg_dir}/files/...)
                if '${' in src_pattern:
                    src_pattern = src_pattern.replace('${pkg_dir}', str(pkg_dir))
                    src_pattern = src_pattern.replace('${src_dir}', str(src_dir))
                    src_pattern = src_pattern.replace('${build_dir}', str(build_dir))
                    src_path = Path(src_pattern)
                    if src_path.exists():
                        matched_files = [src_path]
                    else:
                        if self.verbose:
                            print(f"        Warning: {src_pattern} not found")
                        continue
                else:
                    # Handle glob patterns in src (relative to install dir)
                    # The src pattern is relative to the install dir (e.g., "usr/lib/libubox.so*")
                    matched_files = self._match_files(parent_install_dir, src_pattern)

                for src_file in matched_files:
                    # Determine destination path
                    if dst_path.endswith('/'):
                        # dst is a directory
                        # Check if this is a recursive glob pattern with **
                        if '**' in src_pattern:
                            # Extract the base path before ** and preserve relative structure
                            base_pattern = src_pattern.split('**')[0].rstrip('/')
                            base_dir = parent_install_dir / base_pattern
                            if base_dir.exists() and str(src_file).startswith(str(base_dir)):
                                # Preserve relative path from base_dir
                                rel_path = src_file.relative_to(base_dir)
                                dst_file = tmp_install / dst_path.lstrip('/') / rel_path
                            else:
                                # Fallback to just filename
                                dst_file = tmp_install / dst_path.lstrip('/') / src_file.name
                        else:
                            # Non-recursive glob, use just the filename
                            rel_name = src_file.name
                            dst_file = tmp_install / dst_path.lstrip('/') / rel_name
                    else:
                        # dst is a specific file path
                        dst_file = tmp_install / dst_path.lstrip('/')

                    dst_file.parent.mkdir(parents=True, exist_ok=True)

                    if src_file.is_symlink():
                        # Preserve symlinks
                        link_target = os.readlink(src_file)
                        if dst_file.exists() or dst_file.is_symlink():
                            dst_file.unlink()
                        dst_file.symlink_to(link_target)
                    elif src_file.is_file():
                        shutil.copy2(src_file, dst_file)
                        # Apply file mode if specified
                        if file_mode:
                            try:
                                dst_file.chmod(int(file_mode, 8))
                            except (ValueError, OSError):
                                pass

            # Handle symlinks from subpackage definition
            symlinks_spec = pkg.install.get('symlinks', [])
            for link_entry in symlinks_spec:
                src = link_entry.get('src', '')
                dst = link_entry.get('dst', '')
                if src and dst:
                    link_path = tmp_install / dst.lstrip('/')
                    link_path.parent.mkdir(parents=True, exist_ok=True)
                    if link_path.exists() or link_path.is_symlink():
                        link_path.unlink()
                    link_path.symlink_to(src)

            # Create APK from the filtered directory
            self._create_apk_package(pkg, tmp_install)

    def _create_auto_dev_package(self, pkg: PackageConfig, pkg_install_dir: Path):
        """Auto-generate a -dev package from InstallDev content.

        Creates a development package containing:
        - Headers from usr/include/
        - Static libraries (.a) from usr/lib/
        - pkg-config files (.pc) from usr/lib/pkgconfig/
        - CMake modules from usr/lib/cmake/ or usr/share/cmake/

        The package name is derived from the main library subpackage or
        the source package name with -dev suffix.

        If a -dev subpackage is already explicitly defined, this function
        skips auto-generation to avoid overwriting it.
        """
        if not self._apk_packager:
            return

        # Check if a -dev subpackage is already defined - skip auto-generation
        if hasattr(pkg, 'subpackages') and pkg.subpackages:
            for subpkg_name in pkg.subpackages:
                if subpkg_name.endswith('-dev'):
                    # A dev subpackage is already defined, don't auto-generate
                    return

        # Find dev files in pkg_install_dir
        dev_files = []

        # Headers
        include_dir = pkg_install_dir / 'usr' / 'include'
        if include_dir.exists():
            for f in include_dir.rglob('*'):
                if f.is_file() or f.is_symlink():
                    rel_path = f.relative_to(pkg_install_dir)
                    dev_files.append(('include', f, rel_path))

        # Static libraries
        lib_dir = pkg_install_dir / 'usr' / 'lib'
        if lib_dir.exists():
            for f in lib_dir.glob('*.a'):
                if f.is_file():
                    rel_path = f.relative_to(pkg_install_dir)
                    dev_files.append(('static_lib', f, rel_path))

        # pkg-config files
        pc_dir = pkg_install_dir / 'usr' / 'lib' / 'pkgconfig'
        if pc_dir.exists():
            for f in pc_dir.glob('*.pc'):
                if f.is_file():
                    rel_path = f.relative_to(pkg_install_dir)
                    dev_files.append(('pkgconfig', f, rel_path))

        # CMake modules
        for cmake_dir in [pkg_install_dir / 'usr' / 'lib' / 'cmake',
                          pkg_install_dir / 'usr' / 'share' / 'cmake']:
            if cmake_dir.exists():
                for f in cmake_dir.rglob('*'):
                    if f.is_file():
                        rel_path = f.relative_to(pkg_install_dir)
                        dev_files.append(('cmake', f, rel_path))

        if not dev_files:
            return  # No dev files to package

        # Determine dev package name
        # Use lib<name>-dev naming if there's a lib* subpackage, otherwise <name>-dev
        dev_pkg_name = None
        main_lib_subpkg = None

        if hasattr(pkg, 'subpackages') and pkg.subpackages:
            for subpkg_name in pkg.subpackages:
                if subpkg_name.startswith('lib'):
                    dev_pkg_name = f"{subpkg_name}-dev"
                    main_lib_subpkg = subpkg_name
                    break

        if not dev_pkg_name:
            # Fallback: use source package name
            dev_pkg_name = f"{pkg.name}-dev"

        print(f"    {dev_pkg_name}: auto-generating dev package...")

        # Create temporary directory with dev files
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_install = Path(tmpdir)

            for file_type, src_file, rel_path in dev_files:
                dst_file = tmp_install / rel_path
                dst_file.parent.mkdir(parents=True, exist_ok=True)
                if src_file.is_symlink():
                    link_target = os.readlink(src_file)
                    dst_file.symlink_to(link_target)
                else:
                    shutil.copy2(src_file, dst_file)

            # Create a synthetic dev package config
            dev_pkg_config = {
                'name': dev_pkg_name,
                'version': pkg.version,
                'release': pkg.release,
                'description': f"{pkg.name} development files",
                'section': 'devel',
                'license': pkg.license,
                'dependencies': {
                    'runtime': [main_lib_subpkg] if main_lib_subpkg else []
                }
            }

            # Create APK using the packager
            apk_file = self._apk_packager.create_dev_package(
                dev_pkg_name,
                dev_pkg_config,
                tmp_install,
                self.config.arch
            )
            if apk_file:
                self._apk_files.append(apk_file)
                if self._apk_repo:
                    self._apk_repo.add_package(apk_file)
                print(f"      Created: {apk_file.name}")

    def _match_files(self, base_dir: Path, pattern: str) -> List[Path]:
        """Match files in base_dir using a glob-like pattern.

        Handles patterns like:
        - "usr/lib/libubox.so*" -> matches libubox.so, libubox.so.1, etc.
        - "usr/include/libubox/*.h" -> matches all .h files in that dir
        """
        # Convert the pattern to work with Path.glob
        # Pattern may contain * for wildcards
        parts = pattern.split('/')
        result = []

        # Use rglob if ** in pattern, otherwise iterative matching
        if '**' in pattern:
            for match in base_dir.glob(pattern):
                result.append(match)
        else:
            # Handle patterns like "usr/lib/libubox.so*"
            # Walk through directory parts, handling wildcards
            current_paths = [base_dir]

            for i, part in enumerate(parts):
                next_paths = []
                is_last = (i == len(parts) - 1)

                for current in current_paths:
                    if not current.exists():
                        continue

                    if '*' in part or '?' in part:
                        # Wildcard in this part
                        for item in current.iterdir():
                            if fnmatch.fnmatch(item.name, part):
                                if is_last:
                                    if item.is_file() or item.is_symlink():
                                        next_paths.append(item)
                                else:
                                    if item.is_dir():
                                        next_paths.append(item)
                    else:
                        # Exact match
                        next_item = current / part
                        if next_item.exists():
                            if is_last:
                                if next_item.is_file() or next_item.is_symlink():
                                    next_paths.append(next_item)
                            else:
                                next_paths.append(next_item)

                current_paths = next_paths

            result = current_paths

        return result

    def _install_dev(
        self,
        pkg: PackageConfig,
        pkg_install_dir: Path,
        staging_dir: Optional[Path] = None,
    ):
        """Install development files to staging directory.

        Mirrors OpenWrt's Build/InstallDev mechanism. For cmake packages,
        this copies everything from pkg_install_dir to staging (like OpenWrt's
        Build/InstallDev/cmake default). For other packages, it copies
        headers, libraries, and pkg-config files.

        Can be customized via install_dev section in package.yaml:
          install_dev:
            mode: cmake|standard|none|custom
            files:
              - src: "usr/include/*.h"
                dst: "/usr/include/"

        Args:
            pkg: Package configuration
            pkg_install_dir: Source install directory
            staging_dir: Target staging directory (defaults to shared staging)
        """
        if staging_dir is None:
            staging_dir = self.staging_dir

        install_dev = pkg.build.get('install_dev', {})
        mode = install_dev.get('mode', '')

        # Determine mode from build system if not explicitly set
        if not mode:
            if pkg.build_system == 'cmake':
                mode = 'cmake'
            else:
                mode = 'standard'

        if mode == 'none':
            return

        if mode == 'cmake':
            # OpenWrt's Build/InstallDev/cmake: copy everything
            # $(CP) $(PKG_INSTALL_DIR)/* $(1)/
            self._copy_tree(pkg_install_dir, staging_dir)
            if self.verbose:
                print(f"      InstallDev (cmake): copied all to staging")

        elif mode == 'standard':
            # Standard InstallDev: copy headers, libs, and pkgconfig
            # Headers: usr/include/**
            include_src = pkg_install_dir / 'usr' / 'include'
            if include_src.exists():
                include_dst = staging_dir / 'usr' / 'include'
                self._copy_tree(include_src, include_dst)
                if self.verbose:
                    print(f"      InstallDev: copied headers")

            # Libraries: usr/lib/*.so*, usr/lib/*.a
            lib_src = pkg_install_dir / 'usr' / 'lib'
            if lib_src.exists():
                lib_dst = staging_dir / 'usr' / 'lib'
                lib_dst.mkdir(parents=True, exist_ok=True)
                # Copy .so files and symlinks
                for f in lib_src.glob('*.so*'):
                    dst = lib_dst / f.name
                    if f.is_symlink():
                        link_target = os.readlink(f)
                        if dst.exists() or dst.is_symlink():
                            dst.unlink()
                        dst.symlink_to(link_target)
                    elif f.is_file():
                        shutil.copy2(f, dst)
                # Copy .a files
                for f in lib_src.glob('*.a'):
                    if f.is_file():
                        shutil.copy2(f, lib_dst / f.name)
                if self.verbose:
                    print(f"      InstallDev: copied libraries")

            # pkg-config files: usr/lib/pkgconfig/*.pc
            pc_src = pkg_install_dir / 'usr' / 'lib' / 'pkgconfig'
            if pc_src.exists():
                pc_dst = staging_dir / 'usr' / 'lib' / 'pkgconfig'
                pc_dst.mkdir(parents=True, exist_ok=True)
                for f in pc_src.glob('*.pc'):
                    if f.is_file():
                        shutil.copy2(f, pc_dst / f.name)
                if self.verbose:
                    print(f"      InstallDev: copied pkgconfig files")

        elif mode == 'custom':
            # Custom file patterns from install_dev.files
            files_spec = install_dev.get('files', [])
            for file_entry in files_spec:
                src_pattern = file_entry.get('src', '')
                dst_path = file_entry.get('dst', '')
                if not src_pattern:
                    continue
                matched_files = self._match_files(pkg_install_dir, src_pattern)
                for src_file in matched_files:
                    if dst_path.endswith('/'):
                        dst_file = staging_dir / dst_path.lstrip('/') / src_file.name
                    else:
                        dst_file = staging_dir / dst_path.lstrip('/')
                    dst_file.parent.mkdir(parents=True, exist_ok=True)
                    if src_file.is_symlink():
                        link_target = os.readlink(src_file)
                        if dst_file.exists() or dst_file.is_symlink():
                            dst_file.unlink()
                        dst_file.symlink_to(link_target)
                    elif src_file.is_file():
                        shutil.copy2(src_file, dst_file)
            if self.verbose:
                print(f"      InstallDev: copied custom files")

    def _copy_tree(self, src: Path, dst: Path):
        """Copy directory tree preserving symlinks."""
        if not src.exists():
            return
        for item in src.rglob('*'):
            rel_path = item.relative_to(src)
            dst_path = dst / rel_path
            if item.is_symlink():
                dst_path.parent.mkdir(parents=True, exist_ok=True)
                link_target = os.readlink(item)
                if dst_path.exists() or dst_path.is_symlink():
                    dst_path.unlink()
                dst_path.symlink_to(link_target)
            elif item.is_dir():
                dst_path.mkdir(parents=True, exist_ok=True)
            elif item.is_file():
                dst_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, dst_path)

    def _fix_libtool_files(self, staging_dir: Optional[Path] = None):
        """Remove libtool .la files from staging.

        Libtool .la files cause linking issues in cross-compilation environments
        because they contain paths that don't resolve correctly. Since we already
        set up proper LDFLAGS and pkg-config paths, the .la files are not needed.

        This is the same approach used by many distributions and cross-compilation
        systems including Alpine Linux and Yocto.

        Args:
            staging_dir: Staging directory to clean (defaults to shared staging)
        """
        if staging_dir is None:
            staging_dir = self.staging_dir

        lib_dir = staging_dir / 'usr' / 'lib'
        if not lib_dir.exists():
            return

        for la_file in lib_dir.glob('*.la'):
            try:
                la_file.unlink()
                if self.verbose:
                    print(f"      Removed libtool file: {la_file.name}")
            except Exception as e:
                if self.verbose:
                    print(f"      Warning: Failed to remove {la_file.name}: {e}")

    def _create_apk_from_existing(self, name: str):
        """Create APK from existing built package."""
        pkg = self._load_package(name)
        if not pkg:
            return

        pkg_install_dir = self.packages_dir / name / 'ipkg-install'
        if pkg_install_dir.exists():
            self._create_apk_package(pkg, pkg_install_dir)
            # Use hash-based APK stamp
            pkg_hash = self._compute_package_hash(pkg)
            self._clean_old_stamps(name, 'apk')
            apk_stamp = self.stamp_dir / f'{name}.apk_{pkg_hash}'
            apk_stamp.touch()

    def _generate_apk_index(self):
        """Generate APK repository index."""
        if self._apk_repo:
            self._apk_repo.generate_index(f"OpenWrt {self.config.name} packages")
            print(f"    Repository: {self.repo_dir}")

    def get_staging_dir(self) -> Path:
        """Get staging directory path."""
        return self.staging_dir

    def get_apk_dir(self) -> Path:
        """Get APK packages directory path."""
        return self.apk_dir

    def get_repo_dir(self) -> Path:
        """Get APK repository directory path."""
        return self.repo_dir
