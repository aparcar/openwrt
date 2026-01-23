"""
Kernel builder - builds the Linux kernel with OpenWrt patches and DTS support.

Handles:
- Downloading kernel source
- Applying patches (backport, pending, hack, target-specific)
- Merging config fragments
- Compiling kernel and modules
- Building Device Tree Blobs (DTBs)
"""

import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional, List

from .config import Config
from .toolchain import ToolchainBuilder
from .utils import run_command, download_file, extract_archive, apply_patches, merge_kconfig
from .kmod import KmodRegistry


class KernelBuilder:
    """Builds the Linux kernel with OpenWrt customizations.

    The kernel builder integrates with the kmod registry to automatically
    enable kernel CONFIG options required by requested kernel modules.
    """

    KERNEL_URL_BASE = "https://cdn.kernel.org/pub/linux/kernel"

    def __init__(self, config: Config, verbose: bool = False, jobs: Optional[int] = None, use_ccache: bool = False):
        self.config = config
        self.verbose = verbose
        self.jobs = jobs or os.cpu_count()
        self.use_ccache = use_ccache

        # Kernel version info
        self.version = config.kernel['version']
        self.full_version = config.kernel['full_version']
        self.source_hash = config.kernel['source_hash']

        # Paths
        self.build_dir = config.kernel_build_dir
        self.src_dir = self.build_dir / f'linux-{self.full_version}'
        self.output_dir = self.build_dir / 'output'
        self.dl_dir = config.dl_dir
        self.stamp_dir = self.build_dir / 'stamp'

        # Get toolchain
        self.toolchain = ToolchainBuilder(config, verbose=verbose, jobs=jobs)

        # Kmod registry for kernel config generation
        self.kmod_registry = KmodRegistry(config.poc_dir)

        # Architecture mapping
        self.kernel_arch = {
            'aarch64': 'arm64',
            'arm': 'arm',
            'mips': 'mips',
            'mipsel': 'mips',
            'mips64': 'mips',
            'mips64el': 'mips',
            'x86_64': 'x86',
            'i386': 'x86',
        }.get(config.arch, config.arch)

    def is_built(self) -> bool:
        """Check if kernel is already built."""
        stamp = self.stamp_dir / 'kernel_built'
        return stamp.exists()

    def clean(self):
        """Clean kernel build artifacts."""
        if self.build_dir.exists():
            shutil.rmtree(self.build_dir)

    def build(
        self,
        profile_name: Optional[str] = None,
        kernel_config_overrides: Optional[dict] = None,
    ):
        """Build the complete kernel.

        Args:
            profile_name: Profile to include kmod packages from (optional).
                         If provided, kernel CONFIG options for kmod-* packages
                         in the profile will be enabled.
            kernel_config_overrides: Dict of CONFIG_* -> value overrides from
                                    user config.yaml (e.g., {'CONFIG_IPV6': 'y'})
        """
        print(f"Building kernel {self.full_version} for {self.config.arch}")

        # Create directories
        self.build_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.stamp_dir.mkdir(parents=True, exist_ok=True)

        # Build stages
        self._download_and_extract()
        self._apply_patches()
        self._configure(profile_name, kernel_config_overrides)
        self._compile()
        self._build_dtbs()
        self._build_dtb_overlays(profile_name)
        self._install(profile_name)

        # Mark as built
        (self.stamp_dir / 'kernel_built').touch()

        print(f"Kernel built successfully: {self.output_dir}")

    def _download_and_extract(self):
        """Download and extract kernel source."""
        stamp = self.stamp_dir / 'kernel_extracted'
        if stamp.exists():
            print("  [1/6] Kernel source already extracted, skipping.")
            return

        print("  [1/6] Downloading kernel source...")

        # Determine URL
        major = self.full_version.split('.')[0]
        url = f"{self.KERNEL_URL_BASE}/v{major}.x/linux-{self.full_version}.tar.xz"

        # Download
        tarball = self.dl_dir / f'linux-{self.full_version}.tar.xz'
        self.dl_dir.mkdir(parents=True, exist_ok=True)

        if not tarball.exists():
            download_file(url, tarball, self.source_hash)
        else:
            print(f"    Using cached linux-{self.full_version}.tar.xz")

        # Extract
        if not self.src_dir.exists():
            print("    Extracting...")
            extract_archive(tarball, self.build_dir)

        stamp.touch()

    def _apply_patches(self):
        """Apply OpenWrt kernel patches."""
        stamp = self.stamp_dir / 'kernel_patched'
        if stamp.exists():
            print("  [2/6] Patches already applied, skipping.")
            return

        print("  [2/6] Applying kernel patches...")

        patch_dirs = self.config.get_kernel_patch_dirs()
        total_patches = 0

        for patch_dir in patch_dirs:
            patches = sorted(patch_dir.glob('*.patch'))
            if patches:
                print(f"    {patch_dir.name}: {len(patches)} patches")
                apply_patches(self.src_dir, patch_dir, verbose=self.verbose)
                total_patches += len(patches)

        print(f"    Total: {total_patches} patches applied")

        # Copy OpenWrt kernel files (custom drivers, etc.)
        self._copy_kernel_files()

        stamp.touch()

    def _copy_kernel_files(self):
        """Copy OpenWrt kernel files (files/, files-version/)."""
        files_dirs = [
            self.config.openwrt_dir / 'target' / 'linux' / 'generic' / f'files-{self.version}',
            self.config.openwrt_dir / 'target' / 'linux' / 'generic' / 'files',
            self.config.openwrt_dir / 'target' / 'linux' / self.config.board / f'files-{self.version}',
            self.config.openwrt_dir / 'target' / 'linux' / self.config.board / 'files',
        ]

        for files_dir in files_dirs:
            if files_dir.exists():
                print(f"    Copying files from {files_dir.name}/")
                for src_file in files_dir.rglob('*'):
                    if src_file.is_file():
                        rel_path = src_file.relative_to(files_dir)
                        dst_file = self.src_dir / rel_path
                        dst_file.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(src_file, dst_file)

    def _compute_vermagic(self):
        """Compute kernel vermagic from config.

        The vermagic is an MD5 hash of all CONFIG options set to 'y' or 'm'.
        This ensures kernel modules are only compatible with kernels that have
        the exact same configuration (same symbols exported, same structures).

        Matches OpenWrt's calculation:
            grep '=[ym]' .config.set | LC_ALL=C sort | md5sum

        The vermagic is stored in .vermagic in the kernel build directory
        and used in kernel module package dependencies.
        """
        import hashlib

        config_path = self.src_dir / '.config'
        if not config_path.exists():
            print("    Warning: .config not found, cannot compute vermagic")
            return

        # Extract all =y and =m options (only CONFIG_* lines, not comments)
        # This matches: grep '=[ym]' .config
        options = []
        with open(config_path) as f:
            for line in f:
                line = line.rstrip('\n')
                # Only include lines that have =y or =m (not "is not set" comments)
                if '=y' in line or '=m' in line:
                    options.append(line)

        # Sort with LC_ALL=C behavior (byte-by-byte ASCII sort)
        # Python's default sort is already consistent, but we ensure ASCII ordering
        options.sort(key=lambda x: x.encode('ascii', errors='replace'))

        # Compute MD5 hash (matching: | md5sum)
        # The input to md5 is the sorted lines joined by newlines, with trailing newline
        h = hashlib.md5()
        content = '\n'.join(options) + '\n'
        h.update(content.encode('ascii', errors='replace'))
        vermagic = h.hexdigest()

        # Store in build directory
        vermagic_file = self.build_dir / '.vermagic'
        vermagic_file.write_text(vermagic)

        if self.verbose:
            print(f"    Computed vermagic: {vermagic}")

    def get_vermagic(self) -> str:
        """Get the kernel vermagic hash.

        Returns:
            The vermagic hash string, or 'unknown' if not computed.
        """
        vermagic_file = self.build_dir / '.vermagic'
        if vermagic_file.exists():
            return vermagic_file.read_text().strip()
        return 'unknown'

    def _generate_kmod_config(self, profile_name: Optional[str] = None) -> Optional[Path]:
        """Generate kernel config fragment for requested kernel modules.

        Looks up all kmod-* packages in the target's default_packages and
        the specified profile's packages, then generates a config fragment
        with the required CONFIG options.

        Args:
            profile_name: Profile to include packages from (optional)

        Returns:
            Path to generated config fragment, or None if no kmods requested
        """
        # Collect all requested packages
        packages = list(self.config.default_packages)

        # Add profile-specific packages
        if profile_name:
            try:
                profile = self.config.get_profile(profile_name)
                packages.extend(profile.get('packages', []))
            except ValueError:
                pass

        # Filter to kmod-* packages and extract kmod names
        kmod_names = []
        for pkg in packages:
            if pkg.startswith('kmod-'):
                kmod_name = pkg[5:]  # Strip 'kmod-' prefix
                kmod_names.append(kmod_name)

        if not kmod_names:
            return None

        # Load kmod registry and get CONFIG options
        self.kmod_registry.load()
        config_options = self.kmod_registry.get_kconfig_for_modules(kmod_names)

        if not config_options:
            return None

        # Generate config fragment
        kmod_config = self.build_dir / 'kmod.config'
        with open(kmod_config, 'w') as f:
            f.write("# Kernel config options for requested kmod packages\n")
            f.write(f"# Auto-generated for: {', '.join(sorted(kmod_names))}\n\n")
            for opt in config_options:
                f.write(f"{opt}\n")

        if self.verbose:
            print(f"    Generated kmod config with {len(config_options)} options for {len(kmod_names)} modules")

        return kmod_config

    def _generate_user_config(self, overrides: Optional[dict] = None) -> Optional[Path]:
        """Generate kernel config fragment from user overrides.

        Args:
            overrides: Dict of CONFIG_* -> value (e.g., {'CONFIG_IPV6': 'y'})

        Returns:
            Path to generated config fragment, or None if no overrides
        """
        if not overrides:
            return None

        user_config = self.build_dir / 'user.config'
        with open(user_config, 'w') as f:
            f.write("# User kernel config overrides from config.yaml\n\n")
            for key, value in sorted(overrides.items()):
                # Ensure CONFIG_ prefix
                if not key.startswith('CONFIG_'):
                    key = f'CONFIG_{key}'
                # Handle 'n' as "not set"
                if value.lower() == 'n':
                    f.write(f"# {key} is not set\n")
                else:
                    f.write(f"{key}={value}\n")

        if self.verbose:
            print(f"    Generated user config with {len(overrides)} overrides")

        return user_config

    def _compute_config_hash(
        self,
        profile_name: Optional[str],
        kernel_config_overrides: Optional[dict],
    ) -> str:
        """Compute hash of kernel configuration inputs.

        This is used to detect when kernel config changes and trigger rebuild.
        """
        import hashlib
        import json

        h = hashlib.sha256()

        # Include profile name
        h.update(f"profile:{profile_name or 'none'}".encode())

        # Include sorted config overrides
        if kernel_config_overrides:
            # Sort for deterministic hash
            sorted_config = json.dumps(kernel_config_overrides, sort_keys=True)
            h.update(f"overrides:{sorted_config}".encode())

        # Include base config file paths and mtimes
        for config_file in self.config.get_kernel_config_files():
            if config_file.exists():
                h.update(f"file:{config_file}:{config_file.stat().st_mtime}".encode())

        return h.hexdigest()[:16]

    def _configure(
        self,
        profile_name: Optional[str] = None,
        kernel_config_overrides: Optional[dict] = None,
    ):
        """Configure kernel.

        Args:
            profile_name: Profile to include kmod packages from (optional)
            kernel_config_overrides: Dict of CONFIG_* -> value overrides
        """
        stamp = self.stamp_dir / 'kernel_configured'
        config_hash_file = self.stamp_dir / 'kernel_config.hash'

        # Compute hash of current config inputs
        current_hash = self._compute_config_hash(profile_name, kernel_config_overrides)

        # Check if config has changed
        config_changed = False
        if config_hash_file.exists():
            stored_hash = config_hash_file.read_text().strip()
            if stored_hash != current_hash:
                print("  [3/6] Kernel config changed, reconfiguring...")
                config_changed = True
                # Remove old stamps to force reconfigure and recompile
                stamp.unlink(missing_ok=True)
                (self.stamp_dir / 'kernel_compiled').unlink(missing_ok=True)
                (self.stamp_dir / 'kernel_built').unlink(missing_ok=True)

        if stamp.exists() and not config_changed:
            print("  [3/6] Kernel already configured, skipping.")
            # Write hash if missing (upgrading from builds without hash tracking)
            if not config_hash_file.exists():
                config_hash_file.write_text(current_hash)
            return

        print("  [3/6] Configuring kernel...")

        # Get base config files
        config_files = self.config.get_kernel_config_files()

        # Generate and add kmod config fragment
        kmod_config = self._generate_kmod_config(profile_name)
        if kmod_config:
            config_files.append(kmod_config)

        # Generate user config overrides fragment
        user_config = self._generate_user_config(kernel_config_overrides)
        if user_config:
            config_files.append(user_config)

        print(f"    Merging {len(config_files)} config fragments")

        # Merge configs
        merged_config = self.build_dir / '.config.merged'
        merge_kconfig(config_files, merged_config)

        # Copy to kernel source
        shutil.copy(merged_config, self.src_dir / '.config')

        # Run olddefconfig to fill in defaults
        env = self._get_build_env()
        run_command([
            'make',
            f'ARCH={self.kernel_arch}',
            f'CROSS_COMPILE={self.config.cross_compile}',
            'olddefconfig',
        ], cwd=self.src_dir, env=env, verbose=self.verbose)

        # Compute vermagic (MD5 hash of all =y and =m config options)
        # This is used to ensure kernel modules are compatible with this exact kernel config
        self._compute_vermagic()

        # Write config hash for change detection
        config_hash_file.write_text(current_hash)
        stamp.touch()

    def _compile(self):
        """Compile kernel and modules."""
        stamp = self.stamp_dir / 'kernel_compiled'
        if stamp.exists():
            print("  [4/6] Kernel already compiled, skipping.")
            return

        print("  [4/6] Compiling kernel...")

        env = self._get_build_env()

        # Determine kernel image name
        kernel_name = self.config.image.get('kernel_name', 'Image')
        if self.kernel_arch == 'arm64':
            kernel_name = 'Image'
        elif self.kernel_arch == 'arm':
            kernel_name = 'zImage'
        elif self.kernel_arch == 'x86':
            kernel_name = 'bzImage'
        elif self.kernel_arch == 'mips':
            kernel_name = 'vmlinux'

        # Build kernel
        run_command([
            'make',
            f'-j{self.jobs}',
            f'ARCH={self.kernel_arch}',
            f'CROSS_COMPILE={self.config.cross_compile}',
            kernel_name,
            'modules',
        ], cwd=self.src_dir, env=env, verbose=self.verbose)

        stamp.touch()

    def _build_dtbs(self):
        """Build Device Tree Blobs."""
        stamp = self.stamp_dir / 'dtbs_built'
        if stamp.exists():
            print("  [5/6] DTBs already built, skipping.")
            return

        print("  [5/6] Building Device Tree Blobs...")

        # Check if target has DTS files
        dts_dir = self.config.get_dts_dir()
        if not dts_dir or not dts_dir.exists():
            print("    No DTS directory found, skipping DTB build.")
            stamp.touch()
            return

        env = self._get_build_env()

        # Copy DTS files to kernel tree if needed
        # (Some targets have DTS in target/linux/BOARD/dts/ instead of kernel tree)
        kernel_dts_dir = self.src_dir / 'arch' / self.kernel_arch / 'boot' / 'dts'
        if dts_dir != kernel_dts_dir:
            print(f"    Copying DTS files from {dts_dir.name}/")
            for dts_file in dts_dir.glob('*.dts'):
                shutil.copy2(dts_file, kernel_dts_dir)
            for dtsi_file in dts_dir.glob('*.dtsi'):
                shutil.copy2(dtsi_file, kernel_dts_dir)

        # Build all DTBs
        try:
            run_command([
                'make',
                f'-j{self.jobs}',
                f'ARCH={self.kernel_arch}',
                f'CROSS_COMPILE={self.config.cross_compile}',
                'dtbs',
            ], cwd=self.src_dir, env=env, verbose=self.verbose)
        except subprocess.CalledProcessError:
            # DTB build might fail if no DTS files are configured
            print("    Warning: DTB build failed (no DTS files configured?)")

        stamp.touch()

    def _build_dtb_overlays(self, profile_name: Optional[str] = None):
        """Build Device Tree Blob Overlays (.dtbo files).

        DTB overlays allow runtime or build-time modification of the device tree.
        Common uses include different flash configurations (eMMC vs NAND vs NOR)
        or optional hardware accessories.

        Args:
            profile_name: Profile name to get overlay list from
        """
        if not profile_name:
            return

        try:
            profile = self.config.get_profile(profile_name)
        except ValueError:
            return

        # Get list of overlays from profile
        dts_overlay = profile.get('dts_overlay', [])
        if not dts_overlay:
            return

        stamp = self.stamp_dir / f'dtbos_built_{profile_name}'
        if stamp.exists():
            print(f"  DTB overlays already built for {profile_name}, skipping.")
            return

        print(f"  Building DTB overlays for {profile_name}...")

        # Get DTS directory (profile-specific or target default)
        profile_dts_dir = profile.get('dts_dir')
        if profile_dts_dir:
            dts_dir = Path(self.config._expand_vars(profile_dts_dir))
        else:
            dts_dir = self.config.get_dts_dir()

        if not dts_dir or not dts_dir.exists():
            print(f"    Warning: DTS directory not found, skipping overlay build")
            return

        env = self._get_build_env()
        kernel_dts_dir = self.src_dir / 'arch' / self.kernel_arch / 'boot' / 'dts'

        # Output directory for compiled overlays
        dtbo_output = self.output_dir / 'dtbos'
        dtbo_output.mkdir(parents=True, exist_ok=True)

        # DTC compiler location (use kernel's built-in one)
        dtc = self.src_dir / 'scripts' / 'dtc' / 'dtc'
        if not dtc.exists():
            print(f"    Warning: DTC compiler not found at {dtc}, using system dtc")
            dtc = Path('dtc')

        # DTC warning flags (matching OpenWrt)
        dtc_warn_flags = [
            '-Wno-interrupt_provider',
            '-Wno-unique_unit_address',
            '-Wno-unit_address_vs_reg',
            '-Wno-avoid_unnecessary_addr_size',
            '-Wno-alias_paths',
            '-Wno-graph_child_address',
            '-Wno-simple_bus_reg',
        ]

        built_count = 0
        for overlay_name in dts_overlay:
            dtso_file = dts_dir / f'{overlay_name}.dtso'
            if not dtso_file.exists():
                print(f"    Warning: Overlay source {overlay_name}.dtso not found")
                continue

            dtbo_file = dtbo_output / f'{overlay_name}.dtbo'

            # Copy .dtso to kernel DTS dir if it's not already there
            kernel_dtso = kernel_dts_dir / f'{overlay_name}.dtso'
            if dtso_file != kernel_dtso:
                shutil.copy2(dtso_file, kernel_dtso)

            # Preprocess with CPP (handles #include directives)
            preprocessed = self.build_dir / f'{overlay_name}.dts.preprocessed'
            cpp_cmd = [
                f'{self.config.cross_compile}cpp',
                '-nostdinc',
                '-x', 'assembler-with-cpp',
                f'-I{dts_dir}',
                f'-I{dts_dir}/include',
                f'-I{self.src_dir}/include',
                f'-I{kernel_dts_dir}',
                '-undef',
                '-D__DTS__',
                str(dtso_file),
                '-o', str(preprocessed),
            ]

            try:
                run_command(cpp_cmd, env=env, verbose=self.verbose)
            except subprocess.CalledProcessError as e:
                print(f"    Warning: Failed to preprocess {overlay_name}.dtso: {e}")
                continue

            # Compile with DTC
            # -@ flag enables symbols needed for overlays
            dtc_cmd = [
                str(dtc),
                '-O', 'dtb',
                '-@',  # Enable symbols for overlay support
                f'-i{kernel_dts_dir}',
                f'-i{dts_dir}',
            ] + dtc_warn_flags + [
                '-o', str(dtbo_file),
                str(preprocessed),
            ]

            try:
                run_command(dtc_cmd, env=env, verbose=self.verbose)
                built_count += 1
            except subprocess.CalledProcessError as e:
                print(f"    Warning: Failed to compile {overlay_name}.dtbo: {e}")
                continue

            # Clean up preprocessed file
            preprocessed.unlink(missing_ok=True)

        if built_count > 0:
            print(f"    Built {built_count} DTB overlays")

        stamp.touch()

    def build_dtbs(self, profile_name: Optional[str] = None):
        """Public method to build only DTBs and DTB overlays.

        Args:
            profile_name: Profile to include kmod packages from and get
                         overlay list from (optional)
        """
        self._download_and_extract()
        self._apply_patches()
        self._configure(profile_name)
        self._build_dtbs()
        self._build_dtb_overlays(profile_name)

    def _install(self, profile_name: Optional[str] = None):
        """Install kernel and modules.

        Args:
            profile_name: Profile name (used for logging DTB overlay info)
        """
        stamp = self.stamp_dir / 'kernel_installed'
        if stamp.exists():
            print("  [6/6] Kernel already installed, skipping.")
            return

        print("  [6/6] Installing kernel...")

        env = self._get_build_env()

        # Determine kernel image name and location
        if self.kernel_arch == 'arm64':
            kernel_image = self.src_dir / 'arch' / 'arm64' / 'boot' / 'Image'
        elif self.kernel_arch == 'arm':
            kernel_image = self.src_dir / 'arch' / 'arm' / 'boot' / 'zImage'
        elif self.kernel_arch == 'x86':
            kernel_image = self.src_dir / 'arch' / 'x86' / 'boot' / 'bzImage'
        else:
            kernel_image = self.src_dir / 'vmlinux'

        # Copy kernel
        self.output_dir.mkdir(parents=True, exist_ok=True)
        if kernel_image.exists():
            shutil.copy2(kernel_image, self.output_dir / 'kernel.bin')
            print(f"    Kernel: {kernel_image.name} -> kernel.bin")

        # Copy vmlinux for debugging
        vmlinux = self.src_dir / 'vmlinux'
        if vmlinux.exists():
            shutil.copy2(vmlinux, self.output_dir / 'vmlinux')

        # Install modules
        modules_dir = self.output_dir / 'modules'
        modules_dir.mkdir(exist_ok=True)

        run_command([
            'make',
            f'ARCH={self.kernel_arch}',
            f'CROSS_COMPILE={self.config.cross_compile}',
            f'INSTALL_MOD_PATH={modules_dir}',
            'modules_install',
        ], cwd=self.src_dir, env=env, verbose=self.verbose)

        # Copy DTBs
        dtb_dir = self.src_dir / 'arch' / self.kernel_arch / 'boot' / 'dts'
        if dtb_dir.exists():
            dtb_output = self.output_dir / 'dtbs'
            dtb_output.mkdir(exist_ok=True)
            for dtb in dtb_dir.rglob('*.dtb'):
                shutil.copy2(dtb, dtb_output / dtb.name)
            dtb_count = len(list(dtb_output.glob('*.dtb')))
            if dtb_count > 0:
                print(f"    DTBs: {dtb_count} files")

        # Report DTB overlays (already built by _build_dtb_overlays)
        dtbo_output = self.output_dir / 'dtbos'
        if dtbo_output.exists():
            dtbo_count = len(list(dtbo_output.glob('*.dtbo')))
            if dtbo_count > 0:
                print(f"    DTB overlays: {dtbo_count} files")

        stamp.touch()

    def _get_build_env(self) -> dict:
        """Get environment for kernel builds."""
        env = self.toolchain.get_env(use_ccache=self.use_ccache)
        env['KBUILD_BUILD_HOST'] = 'openwrt'
        env['KBUILD_BUILD_USER'] = 'builder'
        # For reproducible builds
        env['KBUILD_BUILD_TIMESTAMP'] = '@0'
        return env

    def menuconfig(self):
        """Run kernel menuconfig."""
        if not self.src_dir.exists():
            self._download_and_extract()
            self._apply_patches()

        env = self._get_build_env()
        run_command([
            'make',
            f'ARCH={self.kernel_arch}',
            f'CROSS_COMPILE={self.config.cross_compile}',
            'menuconfig',
        ], cwd=self.src_dir, env=env, verbose=True)

    def get_kernel_path(self) -> Path:
        """Get path to compiled kernel image."""
        return self.output_dir / 'kernel.bin'

    def get_modules_dir(self) -> Path:
        """Get path to installed modules."""
        return self.output_dir / 'modules'

    def get_dtbs_dir(self) -> Path:
        """Get path to compiled DTBs."""
        return self.output_dir / 'dtbs'

    def get_dtbos_dir(self) -> Path:
        """Get path to compiled DTB overlays."""
        return self.output_dir / 'dtbos'

    def rebuild_with_initramfs(self, rootfs_dir: Path) -> Path:
        """Rebuild kernel with embedded initramfs.

        This follows OpenWrt's approach of embedding the rootfs directly
        into the kernel image via CONFIG_INITRAMFS_SOURCE.

        Args:
            rootfs_dir: Path to the assembled rootfs directory

        Returns:
            Path to the kernel with embedded initramfs
        """
        print(f"  Rebuilding kernel with embedded initramfs from {rootfs_dir.name}...")

        # Save current config
        config_path = self.src_dir / '.config'
        config_backup = self.src_dir / '.config.before_initramfs'
        shutil.copy(config_path, config_backup)

        # Create initramfs-base-files.txt for device nodes and /init
        # This is needed because device nodes can't be in the rootfs dir
        # (requires root to create). The kernel's gen_init_cpio creates them.
        initramfs_base_files = self.build_dir / 'initramfs-base-files.txt'
        initramfs_base_files.write_text("""# Device nodes for initramfs (created by gen_init_cpio)
nod /dev/console 600 0 0 c 5 1
nod /dev/null 666 0 0 c 1 3
nod /dev/zero 666 0 0 c 1 5
nod /dev/tty 666 0 0 c 5 0
nod /dev/tty0 660 0 0 c 4 0
nod /dev/tty1 660 0 0 c 4 1
nod /dev/random 666 0 0 c 1 8
nod /dev/urandom 666 0 0 c 1 9
dir /dev/pts 755 0 0
# /init symlink - required for initramfs boot
slink /init /sbin/init 755 0 0
""")

        # Read and modify kernel config
        config_text = config_path.read_text()

        # Remove existing initramfs settings
        import re
        config_text = re.sub(r'^CONFIG_INITRAMFS_SOURCE=.*$', '', config_text, flags=re.MULTILINE)
        config_text = re.sub(r'^# CONFIG_INITRAMFS_FORCE is not set$', '', config_text, flags=re.MULTILINE)
        config_text = re.sub(r'^CONFIG_INITRAMFS_FORCE=.*$', '', config_text, flags=re.MULTILINE)
        config_text = re.sub(r'^# CONFIG_INITRAMFS_PRESERVE_MTIME is not set$', '', config_text, flags=re.MULTILINE)
        config_text = re.sub(r'^CONFIG_INITRAMFS_ROOT_UID=.*$', '', config_text, flags=re.MULTILINE)
        config_text = re.sub(r'^CONFIG_INITRAMFS_ROOT_GID=.*$', '', config_text, flags=re.MULTILINE)

        # Add new initramfs settings (OpenWrt style)
        # CONFIG_INITRAMFS_SOURCE takes a space-separated list:
        # - rootfs directory path
        # - initramfs-base-files.txt for device nodes
        # Note: We use no compression (CONFIG_INITRAMFS_COMPRESSION_NONE) because
        # the base kernel config doesn't enable CONFIG_RD_GZIP decompression.
        # This results in a larger kernel but avoids needing to modify base configs.
        initramfs_config = f'''
# Initramfs configuration (auto-generated)
CONFIG_BLK_DEV_INITRD=y
CONFIG_INITRAMFS_SOURCE="{rootfs_dir} {initramfs_base_files}"
# CONFIG_INITRAMFS_FORCE is not set
# CONFIG_INITRAMFS_PRESERVE_MTIME is not set
CONFIG_INITRAMFS_ROOT_UID={os.getuid()}
CONFIG_INITRAMFS_ROOT_GID={os.getgid()}
CONFIG_INITRAMFS_COMPRESSION_NONE=y
'''
        config_text += initramfs_config
        config_path.write_text(config_text)

        # Note: We intentionally skip 'olddefconfig' here because it validates
        # that CONFIG_INITRAMFS_SOURCE paths exist and clears the setting if
        # they don't. Since we already ran olddefconfig during the initial
        # kernel build, the config is complete - we just need to add initramfs
        # settings and rebuild. The kernel build system handles this fine.
        env = self._get_build_env()

        # Determine kernel image name
        if self.kernel_arch == 'arm64':
            kernel_name = 'Image'
        elif self.kernel_arch == 'arm':
            kernel_name = 'zImage'
        elif self.kernel_arch == 'x86':
            kernel_name = 'bzImage'
        else:
            kernel_name = 'vmlinux'

        # Force rebuild of initramfs by removing the intermediate cpio
        # The kernel build system caches the initramfs cpio and won't rebuild
        # it unless we either touch the source or remove the cached file
        initramfs_cpio = self.src_dir / 'usr' / 'initramfs_data.cpio'
        if initramfs_cpio.exists():
            initramfs_cpio.unlink()

        # Rebuild kernel with embedded initramfs
        # Pass CONFIG_INITRAMFS_SOURCE on command line to override any cached value
        initramfs_source = f"{rootfs_dir} {initramfs_base_files}"
        run_command([
            'make',
            f'-j{self.jobs}',
            f'ARCH={self.kernel_arch}',
            f'CROSS_COMPILE={self.config.cross_compile}',
            f'CONFIG_INITRAMFS_SOURCE={initramfs_source}',
            kernel_name,
        ], cwd=self.src_dir, env=env, verbose=self.verbose)

        # Get path to new kernel
        if self.kernel_arch == 'arm64':
            kernel_image = self.src_dir / 'arch' / 'arm64' / 'boot' / 'Image'
        elif self.kernel_arch == 'arm':
            kernel_image = self.src_dir / 'arch' / 'arm' / 'boot' / 'zImage'
        elif self.kernel_arch == 'x86':
            kernel_image = self.src_dir / 'arch' / 'x86' / 'boot' / 'bzImage'
        else:
            kernel_image = self.src_dir / 'vmlinux'

        # Copy to output with initramfs suffix
        output_kernel = self.output_dir / 'initramfs-kernel.bin'
        if kernel_image.exists():
            shutil.copy2(kernel_image, output_kernel)
            size_mb = output_kernel.stat().st_size / (1024 * 1024)
            print(f"    Created initramfs-kernel.bin ({size_mb:.1f}MB)")

        # Restore original config (so regular kernel is preserved)
        shutil.copy(config_backup, config_path)

        return output_kernel

    def get_initramfs_kernel_path(self) -> Path:
        """Get path to kernel with embedded initramfs."""
        return self.output_dir / 'initramfs-kernel.bin'
