"""
Kernel module packaging - creates APK packages for kernel modules.

Handles:
- Loading kmod definitions from kmods.yaml
- Discovering built kernel modules from kernel build
- Creating individual APK packages for each module
- Module dependency tracking
- Autoload configuration generation
"""

import os
import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Optional, Set, Any
from dataclasses import dataclass, field

import yaml

from .config import Config
from .apk import APKPackager, APKRepository
# Note: KernelBuilder imported lazily in KernelModulePackager to avoid circular import


@dataclass
class KmodDefinition:
    """Kernel module definition from kmods.yaml."""
    name: str
    title: str = ""
    description: str = ""
    category: str = ""
    kconfig: List[Dict[str, str]] = field(default_factory=list)
    files: List[str] = field(default_factory=list)
    autoload: Dict[str, Any] = field(default_factory=dict)
    depends: List[str] = field(default_factory=list)
    hidden: bool = False
    source_file: str = ""

    @property
    def package_name(self) -> str:
        """APK package name for this module."""
        return f"kmod-{self.name}"

    @property
    def autoload_priority(self) -> int:
        """Get autoload priority."""
        return self.autoload.get('priority', 50)

    @property
    def autoload_modules(self) -> List[str]:
        """Get modules to load."""
        return self.autoload.get('modules', [])

    @property
    def autoload_boot(self) -> bool:
        """Whether to load at boot."""
        return self.autoload.get('boot', False)

    def get_kconfig_options(self) -> List[str]:
        """Get kernel config options needed for this module."""
        options = []
        for cfg in self.kconfig:
            name = cfg.get('name', '')
            value = cfg.get('value', 'm')
            if name:
                options.append(f"{name}={value}")
        return options


class KmodRegistry:
    """Registry of kernel module definitions."""

    def __init__(self, poc_dir: Path):
        self.poc_dir = poc_dir
        self._definitions: Dict[str, KmodDefinition] = {}
        self._by_category: Dict[str, List[str]] = {}
        self._loaded = False

    def load(self):
        """Load kmod definitions from kmods.yaml."""
        if self._loaded:
            return

        yaml_path = self.poc_dir / 'owrt' / 'kmods.yaml'
        if not yaml_path.exists():
            print(f"  Warning: kmods.yaml not found at {yaml_path}")
            return

        with open(yaml_path) as f:
            data = yaml.safe_load(f)

        # New structure: categories is a list with nested modules
        for category in data.get('categories', []):
            cat_name = category.get('name', 'Other')

            if cat_name not in self._by_category:
                self._by_category[cat_name] = []

            for mod_data in category.get('modules', []):
                kmod = KmodDefinition(
                    name=mod_data.get('name', ''),
                    title=mod_data.get('title', ''),
                    description=mod_data.get('description', ''),
                    category=cat_name,
                    kconfig=mod_data.get('kconfig', []),
                    files=mod_data.get('files', []),
                    autoload=mod_data.get('autoload', {}),
                    depends=mod_data.get('depends', []),
                    hidden=mod_data.get('hidden', False),
                )
                self._definitions[kmod.name] = kmod
                self._by_category[cat_name].append(kmod.name)

        self._loaded = True
        print(f"  Loaded {len(self._definitions)} kmod definitions")

    def get(self, name: str) -> Optional[KmodDefinition]:
        """Get a kmod definition by name."""
        self.load()
        return self._definitions.get(name)

    def get_all(self) -> Dict[str, KmodDefinition]:
        """Get all kmod definitions."""
        self.load()
        return self._definitions

    def get_by_category(self, category: str) -> List[str]:
        """Get kmod names in a category."""
        self.load()
        return self._by_category.get(category, [])

    def get_categories(self) -> List[str]:
        """Get all category names."""
        self.load()
        return list(self._by_category.keys())

    def get_kconfig_for_modules(self, module_names: List[str]) -> List[str]:
        """Get all kernel config options needed for a list of modules."""
        self.load()
        options = set()
        for name in module_names:
            kmod = self._definitions.get(name)
            if kmod:
                for opt in kmod.get_kconfig_options():
                    options.add(opt)
        return sorted(options)


class KernelModulePackager:
    """Creates APK packages for kernel modules."""

    def __init__(self, config: Config, verbose: bool = False):
        self.config = config
        self.verbose = verbose

        # Get kernel builder for paths (lazy import to avoid circular dependency)
        from .kernel import KernelBuilder
        self.kernel_builder = KernelBuilder(config, verbose=verbose)

        # APK packager for creating packages
        self.apk_packager = APKPackager(config, verbose=verbose)

        # Kmod registry
        self.registry = KmodRegistry(config.poc_dir)

        # Paths
        self.modules_dir = self.kernel_builder.get_modules_dir()
        self.kmod_staging = config.build_dir / 'kmod-staging' / config.name
        self.kmod_packages = config.build_dir / 'apk-packages' / config.name

        # APK repository for adding packages
        self.apk_repo = APKRepository(
            repo_dir=config.build_dir / 'apk-repo' / config.name,
            build_dir=config.build_dir,
            arch=config.arch,
            verbose=verbose,
        )

        # Kernel version and vermagic for package compatibility
        self.kernel_version = self.kernel_builder.full_version
        self.kernel_vermagic = self.kernel_builder.get_vermagic()
        self.kernel_release = '1'  # TODO: make configurable

        # Full kernel package version: VERSION~VERMAGIC-rRELEASE
        # This ensures kmod packages only work with the exact kernel they were built for
        self.kernel_pkg_version = f"{self.kernel_version}~{self.kernel_vermagic}-r{self.kernel_release}"

        # Cache of built module paths (ko_name -> Path)
        self._built_modules: Dict[str, Path] = {}

    def discover_built_modules(self) -> Dict[str, Path]:
        """Discover all built kernel modules (.ko files)."""
        if self._built_modules:
            return self._built_modules

        # Find all .ko files
        modules_base = self.modules_dir / 'lib' / 'modules'
        if not modules_base.exists():
            if self.verbose:
                print(f"  Warning: No modules directory at {modules_base}")
            return {}

        # Find kernel version directory
        kernel_dirs = list(modules_base.iterdir())
        if not kernel_dirs:
            return {}

        kernel_dir = kernel_dirs[0]  # e.g., 6.12.65

        for ko_file in kernel_dir.rglob('*.ko'):
            # Module name from filename (strip .ko)
            name = ko_file.stem
            # Normalize: replace underscores with hyphens to match kmod names
            normalized_name = name.replace('_', '-')
            self._built_modules[normalized_name] = ko_file
            # Also store with original name
            if name != normalized_name:
                self._built_modules[name] = ko_file

        if self.verbose:
            print(f"  Discovered {len(self._built_modules)} built kernel modules")

        return self._built_modules

    def build_module_packages(self, module_names: Optional[List[str]] = None) -> List[Path]:
        """Build APK packages for kernel modules.

        Args:
            module_names: List of kmod names to build, or None for all built modules

        Returns:
            List of paths to created APK packages
        """
        # Load definitions and discover built modules
        self.registry.load()
        built_modules = self.discover_built_modules()

        if not built_modules:
            print("  No kernel modules to package")
            return []

        # Determine which modules to build
        if module_names:
            # Build specific modules
            to_build = module_names
        else:
            # Build all modules that have definitions and are built
            # Note: hidden modules are still built (needed as dependencies),
            # just not user-selectable in menus
            to_build = []
            for name, kmod in self.registry.get_all().items():
                # Check if the module files exist
                for ko_file in kmod.files:
                    ko_name = Path(ko_file).stem.replace('_', '-')
                    if ko_name in built_modules:
                        to_build.append(name)
                        break

        # First, create the kernel virtual package that kmods depend on
        kernel_pkg = self._build_kernel_package()
        if kernel_pkg:
            self.apk_repo.add_package(kernel_pkg)

        print(f"  Packaging {len(to_build)} kernel modules...")

        packages = []

        # Use parallel packaging for speed (I/O bound task)
        max_workers = min(os.cpu_count() or 4, len(to_build), 16)

        if max_workers > 1 and len(to_build) > 1:
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {
                    executor.submit(self._build_module_package, name): name
                    for name in sorted(to_build)
                }
                for future in as_completed(futures):
                    name = futures[future]
                    try:
                        pkg_path = future.result()
                        if pkg_path:
                            packages.append(pkg_path)
                    except Exception as e:
                        if self.verbose:
                            print(f"    Warning: Failed to package kmod-{name}: {e}")
        else:
            # Sequential fallback for single module
            for name in sorted(to_build):
                pkg_path = self._build_module_package(name)
                if pkg_path:
                    packages.append(pkg_path)

        # Add packages to the APK repository
        if packages:
            print(f"  Adding {len(packages)} kmod packages to repository...")
            for pkg_path in packages:
                self.apk_repo.add_package(pkg_path)

            # Regenerate repository index
            print(f"  Regenerating repository index...")
            self.apk_repo.generate_index(f"OpenWrt {self.config.name} packages")

        return packages

    def _build_kernel_package(self) -> Optional[Path]:
        """Create the kernel virtual package that kmod packages depend on.

        This package contains:
        - modules.builtin (list of built-in modules)
        - modules.builtin.modinfo (info about built-in modules)

        The version includes the vermagic to ensure exact kernel compatibility.
        """
        print(f"  Creating kernel package (version {self.kernel_pkg_version})...")

        # Create staging directory
        staging_dir = self.kmod_staging / 'kernel'
        if staging_dir.exists():
            shutil.rmtree(staging_dir)
        staging_dir.mkdir(parents=True)

        # Install modules.builtin files
        modules_dir = staging_dir / 'lib' / 'modules' / self.kernel_version
        modules_dir.mkdir(parents=True)

        # Find kernel source directory
        kernel_src = self.kernel_builder.src_dir

        # Copy modules.builtin (list of built-in modules)
        builtin_src = kernel_src / 'modules.builtin'
        if builtin_src.exists():
            # Strip path prefixes (convert "kernel/foo/bar.ko" to "bar.ko")
            with open(builtin_src) as f:
                modules = [line.strip().split('/')[-1] for line in f if line.strip()]
            (modules_dir / 'modules.builtin').write_text('\n'.join(modules) + '\n')

        # Copy modules.builtin.modinfo
        modinfo_src = kernel_src / 'modules.builtin.modinfo'
        if modinfo_src.exists():
            shutil.copy2(modinfo_src, modules_dir / 'modules.builtin.modinfo')

        # Create APK package
        output_dir = self.kmod_packages
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"kernel-{self.kernel_pkg_version}.apk"

        try:
            if not self.apk_packager.have_apk():
                print(f"    Warning: apk binary not found, skipping kernel package")
                return None

            apk_bin = self.apk_packager.apk_binary

            cmd = [
                str(apk_bin),
                'mkpkg',
                '--info', f'name:kernel',
                '--info', f'version:{self.kernel_pkg_version}',
                '--info', f'description:Virtual kernel package',
                '--info', f'url:https://www.kernel.org/',
                '--info', f'arch:{self.config.arch}',
                '--info', f'license:GPL-2.0',
                '--info', f'origin:kernel',
                '--files', str(staging_dir),
                '--output', str(output_path),
            ]

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                cwd=staging_dir,
            )

            if result.returncode == 0:
                if self.verbose:
                    print(f"    Created: kernel package")
                return output_path
            else:
                print(f"    Warning: Failed to create kernel package: {result.stderr}")
                return None

        except Exception as e:
            print(f"    Warning: Failed to create kernel package: {e}")
            return None

    def _build_module_package(self, name: str) -> Optional[Path]:
        """Build APK package for a single kernel module."""
        # Get definition
        kmod = self.registry.get(name)
        if not kmod:
            if self.verbose:
                print(f"    Warning: No definition for kmod-{name}")
            return None

        # Get built module paths
        built_modules = self.discover_built_modules()

        # Find the .ko files
        ko_paths = []
        for ko_file in kmod.files:
            ko_name = Path(ko_file).stem.replace('_', '-')
            if ko_name in built_modules:
                ko_paths.append((ko_file, built_modules[ko_name]))
            else:
                # Try with original name
                ko_name_orig = Path(ko_file).stem
                if ko_name_orig in built_modules:
                    ko_paths.append((ko_file, built_modules[ko_name_orig]))

        if not ko_paths:
            if self.verbose:
                print(f"    Warning: No built modules found for kmod-{name}")
            return None

        pkg_name = kmod.package_name

        # Create staging directory for this module
        staging_dir = self.kmod_staging / pkg_name
        if staging_dir.exists():
            shutil.rmtree(staging_dir)
        staging_dir.mkdir(parents=True)

        # Install module files
        modules_install_dir = staging_dir / 'lib' / 'modules' / self.kernel_version
        modules_install_dir.mkdir(parents=True)

        for ko_rel_path, ko_src_path in ko_paths:
            # Preserve path structure
            dest_path = modules_install_dir / ko_rel_path
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ko_src_path, dest_path)

        # Create autoload config with priority
        # /etc/modules.d/<priority>-<module_name>
        modules_d = staging_dir / 'etc' / 'modules.d'
        modules_d.mkdir(parents=True)

        priority = kmod.autoload_priority
        autoload_modules = kmod.autoload_modules or [name.replace('-', '_')]
        autoload_file = modules_d / f"{priority:02d}-{name}"
        autoload_file.write_text('\n'.join(autoload_modules) + '\n')

        # Build package dependencies
        # All kmod packages depend on the exact kernel version (with vermagic)
        # This ensures modules only work with the kernel they were compiled for
        kernel_dep = f"kernel={self.kernel_pkg_version}"
        pkg_depends = [kernel_dep]
        target_prefix = self.config.name.split('-')[0]  # armsr from armsr-armv8

        for dep in kmod.depends:
            if dep.startswith('@'):
                # Feature dependency - skip for now
                continue

            # Handle negated target conditionals: !TARGET_x:pkg or !TARGET_x (alone)
            # Format: !TARGET_bcm47xx:kmod-ssb (include if NOT bcm47xx)
            if dep.startswith('!TARGET_'):
                if ':' in dep:
                    condition, pkg_name_raw = dep.split(':', 1)
                    neg_target = condition.replace('!TARGET_', '')
                    # Include if we're NOT the negated target
                    if target_prefix != neg_target:
                        if pkg_name_raw.startswith('kmod-'):
                            pkg_depends.append(pkg_name_raw)
                        else:
                            pkg_depends.append(f"kmod-{pkg_name_raw}")
                # else: just a feature flag like !TARGET_x, skip
                continue

            # Handle conditional dependencies: (CONDITION):package-name
            # Format: (TARGET_armsr||TARGET_bcm27xx):kmod-of-mdio
            # Or: (KERNEL_FEATURE):kmod-something
            if dep.startswith('(') and ':' in dep:
                # Extract the condition and package name
                condition, pkg_name_raw = dep.rsplit(':', 1)
                condition = condition.strip('()')

                # Evaluate TARGET_ conditions
                if 'TARGET_' in condition:
                    # Parse the condition - e.g., TARGET_armsr||TARGET_bcm27xx
                    condition_targets = [
                        t.replace('TARGET_', '')
                        for t in re.findall(r'TARGET_(\w+)', condition)
                    ]

                    if target_prefix in condition_targets:
                        # Condition matches, add the dependency
                        if pkg_name_raw.startswith('kmod-'):
                            pkg_depends.append(pkg_name_raw)
                        else:
                            pkg_depends.append(f"kmod-{pkg_name_raw}")
                # Skip other conditional deps (KERNEL_* etc) for now
                continue

            # Handle simple Kconfig feature conditionals: FEATURE:package-name
            # Format: USB_SUPPORT:kmod-usb-common (if USB_SUPPORT enabled, depend on kmod-usb-common)
            # Only add if the dependent module actually exists (feature may be disabled)
            if ':' in dep and not dep.startswith('kmod-'):
                feature, pkg_name_raw = dep.split(':', 1)
                # Skip if this looks like a feature flag without a package
                if not pkg_name_raw:
                    continue
                # Determine the actual dependency package name
                if pkg_name_raw.startswith('kmod-'):
                    dep_pkg_name = pkg_name_raw
                    dep_kmod_name = pkg_name_raw.replace('kmod-', '')
                else:
                    dep_pkg_name = f"kmod-{pkg_name_raw}"
                    dep_kmod_name = pkg_name_raw
                # Only add if the dependent module can be built (has .ko files)
                dep_kmod = self.registry.get(dep_kmod_name)
                if dep_kmod:
                    # Check if any of the module files exist
                    has_files = False
                    for ko_file in dep_kmod.files:
                        ko_name = Path(ko_file).stem.replace('_', '-')
                        if ko_name in built_modules:
                            has_files = True
                            break
                    if has_files:
                        pkg_depends.append(dep_pkg_name)
                continue

            if dep.startswith('kmod-'):
                pkg_depends.append(dep)
            else:
                pkg_depends.append(f"kmod-{dep}")

        # Create APK package
        # Version includes vermagic to indicate kernel compatibility
        description = kmod.description or kmod.title or f"Kernel module: {name}"
        pkg_path = self._create_apk(
            name=pkg_name,
            version=self.kernel_pkg_version,
            description=description,
            depends=pkg_depends,
            staging_dir=staging_dir,
        )

        if pkg_path:
            if self.verbose:
                print(f"    Created: {pkg_name}")
        else:
            print(f"    Warning: Failed to create {pkg_name}")

        return pkg_path

    def _create_apk(
        self,
        name: str,
        version: str,
        description: str,
        depends: List[str],
        staging_dir: Path,
    ) -> Optional[Path]:
        """Create an APK package from staged files using apk v3 mkpkg."""

        # Output path
        output_dir = self.kmod_packages
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{name}-{version}.apk"

        # Use apk mkpkg with --info KEY:VALUE pairs (apk v3 format)
        try:
            if not self.apk_packager.have_apk():
                print(f"    Warning: apk binary not found, skipping {name}")
                return None

            apk_bin = self.apk_packager.apk_binary

            # Build command with --info KEY:VALUE pairs
            cmd = [
                str(apk_bin),
                'mkpkg',
                '--info', f'name:{name}',
                '--info', f'version:{version}',
                '--info', f'description:{description}',
                '--info', f'url:https://openwrt.org',
                '--info', f'arch:{self.config.arch}',
                '--info', f'license:GPL-2.0',
                '--info', f'origin:{name}',
            ]

            # Add dependencies as space-separated list (APK v3 format)
            if depends:
                cmd.extend(['--info', f'depends:{" ".join(depends)}'])

            # Add files directory
            if staging_dir.exists() and any(staging_dir.iterdir()):
                cmd.extend(['--files', str(staging_dir)])

            cmd.extend(['--output', str(output_path)])

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                cwd=staging_dir,
            )

            if result.returncode == 0:
                return output_path
            else:
                if self.verbose:
                    print(f"    Warning: apk mkpkg failed for {name}: {result.stderr}")
                return None

        except Exception as e:
            if self.verbose:
                print(f"    Warning: Failed to create APK for {name}: {e}")
            return None

    def get_module_packages(self) -> List[str]:
        """Get list of kmod package names that can be built."""
        self.registry.load()
        return [f"kmod-{name}" for name in self.registry.get_all().keys()]

    def get_kconfig_for_target(self, module_names: List[str]) -> List[str]:
        """Get kernel config options needed for specified modules."""
        return self.registry.get_kconfig_for_modules(module_names)
