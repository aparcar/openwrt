"""
Toolchain builder - builds the cross-compilation toolchain from OpenWrt sources.

Build order for musl:
1. binutils
2. gcc (initial) - minimal GCC for compiling musl
3. kernel-headers
4. musl
5. gcc (final) - full GCC with libc support
"""

import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from .config import Config
from .utils import run_command, download_file, extract_archive, apply_patches


class ToolchainBuilder:
    """Builds the cross-compilation toolchain."""

    # Component versions (matching OpenWrt defaults)
    BINUTILS_VERSION = "2.44"
    GCC_VERSION = "14.3.0"
    MUSL_VERSION = "1.2.5"
    LINUX_VERSION = "6.12.65"

    # Source URLs
    BINUTILS_URL = f"https://ftp.gnu.org/gnu/binutils/binutils-{BINUTILS_VERSION}.tar.xz"
    GCC_URL = f"https://ftp.gnu.org/gnu/gcc/gcc-{GCC_VERSION}/gcc-{GCC_VERSION}.tar.xz"
    MUSL_URL = f"https://musl.libc.org/releases/musl-{MUSL_VERSION}.tar.gz"
    LINUX_URL = f"https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-{LINUX_VERSION}.tar.xz"

    # Hashes (from OpenWrt)
    BINUTILS_HASH = "ce2017e059d63e67ddb9240e9d4ec49c2893605035cd60e92ad53177f4377237"
    GCC_HASH = "e0dc77297625631ac8e50fa92fffefe899a4eb702592da5c32ef04e2293aca3a"
    MUSL_HASH = "a9a118bbe84d8764da0ea0d28b3ab3fae8477fc7e4085d90102b8596fc7c75e4"
    LINUX_HASH = "54e852667af35c0ed06cfc81311e65fa7f5f798a3bfcf78a559d3b4785a139c1"

    def __init__(self, config: Config, verbose: bool = False, jobs: Optional[int] = None):
        self.config = config
        self.verbose = verbose
        self.jobs = jobs or os.cpu_count()

        # Paths
        self.toolchain_dir = config.toolchain_dir
        self.build_dir = config.build_dir / 'toolchain-build' / config.name
        self.dl_dir = config.dl_dir
        self.stamp_dir = self.toolchain_dir / 'stamp'

        # Target configuration
        self.target = config.target_tuple
        self.arch = config.arch

        # Get versions from config
        tc = config.toolchain
        self.gcc_version = tc.get('gcc_version', self.GCC_VERSION)
        self.binutils_version = tc.get('binutils_version', self.BINUTILS_VERSION)

    def is_built(self) -> bool:
        """Check if toolchain is already built."""
        stamp = self.stamp_dir / 'gcc_final_installed'
        return stamp.exists()

    def clean(self):
        """Clean all toolchain build artifacts."""
        if self.build_dir.exists():
            shutil.rmtree(self.build_dir)
        if self.toolchain_dir.exists():
            shutil.rmtree(self.toolchain_dir)

    def build(self):
        """Build the complete toolchain."""
        print(f"Building toolchain for {self.target}")
        print(f"  GCC: {self.gcc_version}")
        print(f"  Binutils: {self.binutils_version}")
        print(f"  Musl: {self.MUSL_VERSION}")

        # Create directories
        self.toolchain_dir.mkdir(parents=True, exist_ok=True)
        self.build_dir.mkdir(parents=True, exist_ok=True)
        self.dl_dir.mkdir(parents=True, exist_ok=True)
        self.stamp_dir.mkdir(parents=True, exist_ok=True)

        # Create lib symlinks
        (self.toolchain_dir / 'lib').mkdir(exist_ok=True)
        for link in ['lib64', 'lib32']:
            link_path = self.toolchain_dir / link
            if not link_path.exists():
                link_path.symlink_to('lib')

        # Build stages (for musl)
        self._build_binutils()
        self._build_gcc_initial()
        self._build_kernel_headers()
        self._build_musl()
        self._build_gcc_final()

        print(f"\nToolchain built successfully: {self.toolchain_dir}")

    def _download_source(self, url: str, expected_hash: str) -> Path:
        """Download and verify source tarball."""
        filename = url.split('/')[-1]
        dest = self.dl_dir / filename

        if not dest.exists():
            print(f"  Downloading {filename}...")
            download_file(url, dest, expected_hash)
        else:
            print(f"  Using cached {filename}")

        return dest

    def _build_binutils(self):
        """Build binutils."""
        stamp = self.stamp_dir / 'binutils_installed'
        if stamp.exists():
            print("  [1/5] Binutils already built, skipping.")
            return

        print("  [1/5] Building binutils...")

        # Download
        tarball = self._download_source(self.BINUTILS_URL, self.BINUTILS_HASH)

        # Extract
        src_dir = self.build_dir / f'binutils-{self.binutils_version}'
        if not src_dir.exists():
            extract_archive(tarball, self.build_dir)

        # Apply OpenWrt patches (from toolchain/)
        patches_dir = self.config.root_dir / 'toolchain' / 'binutils' / 'patches' / self.binutils_version
        if patches_dir.exists():
            apply_patches(src_dir, patches_dir, verbose=self.verbose)

        # Configure
        build_dir = self.build_dir / 'binutils-build'
        build_dir.mkdir(exist_ok=True)

        configure_args = [
            str(src_dir / 'configure'),
            f'--prefix={self.toolchain_dir}',
            f'--target={self.target}',
            f'--with-sysroot={self.toolchain_dir}',
            '--disable-nls',
            '--disable-werror',
            '--disable-sim',
            '--disable-gdb',
            '--enable-lto',
            '--enable-plugins',
            '--enable-deterministic-archives',
            '--with-system-zlib',
        ]

        run_command(configure_args, cwd=build_dir, verbose=self.verbose)

        # Build
        run_command(['make', f'-j{self.jobs}'], cwd=build_dir, verbose=self.verbose)

        # Install
        run_command(['make', 'install'], cwd=build_dir, verbose=self.verbose)

        stamp.touch()

    def _build_gcc_initial(self):
        """Build initial GCC (minimal, for compiling musl)."""
        stamp = self.stamp_dir / 'gcc_initial_installed'
        if stamp.exists():
            print("  [2/5] GCC (initial) already built, skipping.")
            return

        print("  [2/5] Building GCC (initial)...")

        # Download
        tarball = self._download_source(self.GCC_URL, self.GCC_HASH)

        # Extract
        src_dir = self.build_dir / f'gcc-{self.gcc_version}'
        if not src_dir.exists():
            extract_archive(tarball, self.build_dir)

        # Apply OpenWrt patches (from toolchain/)
        gcc_major = self.gcc_version.split('.')[0]
        patches_dir = self.config.root_dir / 'toolchain' / 'gcc' / f'patches-{gcc_major}.x'
        if patches_dir.exists():
            apply_patches(src_dir, patches_dir, verbose=self.verbose)

        # Configure
        build_dir = self.build_dir / 'gcc-initial-build'
        build_dir.mkdir(exist_ok=True)

        configure_args = [
            str(src_dir / 'configure'),
            f'--prefix={self.toolchain_dir}',
            f'--target={self.target}',
            f'--with-sysroot={self.toolchain_dir}',
            '--with-gnu-ld',
            '--disable-nls',
            '--disable-shared',
            '--disable-multilib',
            '--disable-libssp',
            '--disable-libgomp',
            '--disable-libatomic',
            '--disable-libquadmath',
            '--disable-threads',
            '--without-headers',
            '--with-newlib',
            '--enable-languages=c',
            f'--with-gmp=/usr',
            f'--with-mpfr=/usr',
            f'--with-mpc=/usr',
        ]

        env = os.environ.copy()
        env['CFLAGS'] = '-O2 -pipe'
        env['CXXFLAGS'] = '-O2 -pipe'

        run_command(configure_args, cwd=build_dir, env=env, verbose=self.verbose)

        # Build compiler
        run_command(['make', f'-j{self.jobs}', 'all-gcc'], cwd=build_dir, verbose=self.verbose)

        # Build libgcc (needed by musl for floating-point helpers like __trunctfdf2)
        run_command(['make', f'-j{self.jobs}', 'all-target-libgcc'], cwd=build_dir, verbose=self.verbose)

        # Install compiler and libgcc
        run_command(['make', 'install-gcc'], cwd=build_dir, verbose=self.verbose)
        run_command(['make', 'install-target-libgcc'], cwd=build_dir, verbose=self.verbose)

        stamp.touch()

    def _build_kernel_headers(self):
        """Install Linux kernel headers."""
        stamp = self.stamp_dir / 'kernel_headers_installed'
        if stamp.exists():
            print("  [3/5] Kernel headers already installed, skipping.")
            return

        print("  [3/5] Installing kernel headers...")

        # Download
        tarball = self._download_source(self.LINUX_URL, self.LINUX_HASH)

        # Extract
        src_dir = self.build_dir / f'linux-{self.LINUX_VERSION}'
        if not src_dir.exists():
            extract_archive(tarball, self.build_dir)

        # Map arch names
        kernel_arch = {
            'aarch64': 'arm64',
            'arm': 'arm',
            'mips': 'mips',
            'mipsel': 'mips',
            'x86_64': 'x86',
            'i386': 'x86',
        }.get(self.arch, self.arch)

        # Install headers to /usr/include for proper sysroot layout
        usr_dir = self.toolchain_dir / 'usr'
        usr_dir.mkdir(parents=True, exist_ok=True)

        run_command([
            'make',
            f'ARCH={kernel_arch}',
            f'INSTALL_HDR_PATH={usr_dir}',
            'headers_install',
        ], cwd=src_dir, verbose=self.verbose)

        stamp.touch()

    def _build_musl(self):
        """Build musl libc."""
        stamp = self.stamp_dir / 'musl_installed'
        if stamp.exists():
            print("  [4/5] Musl already built, skipping.")
            return

        print("  [4/5] Building musl...")

        # Download
        tarball = self._download_source(self.MUSL_URL, self.MUSL_HASH)

        # Extract
        src_dir = self.build_dir / f'musl-{self.MUSL_VERSION}'
        if not src_dir.exists():
            extract_archive(tarball, self.build_dir)

        # Apply OpenWrt patches (from toolchain/)
        patches_dir = self.config.root_dir / 'toolchain' / 'musl' / 'patches'
        if patches_dir.exists():
            apply_patches(src_dir, patches_dir, verbose=self.verbose)

        # Configure
        build_dir = self.build_dir / 'musl-build'
        build_dir.mkdir(exist_ok=True)

        # Set up cross-compiler path
        cc = self.toolchain_dir / 'bin' / f'{self.target}-gcc'

        env = os.environ.copy()
        env['CC'] = str(cc)
        env['CROSS_COMPILE'] = f'{self.target}-'
        env['PATH'] = f"{self.toolchain_dir / 'bin'}:{env.get('PATH', '')}"

        configure_args = [
            str(src_dir / 'configure'),
            '--prefix=/usr',  # Install to /usr so sysroot has /usr/include, /usr/lib
            f'--target={self.target}',
            '--disable-gcc-wrapper',
            '--enable-optimize',
        ]

        run_command(configure_args, cwd=build_dir, env=env, verbose=self.verbose)

        # Build
        run_command(['make', f'-j{self.jobs}'], cwd=build_dir, env=env, verbose=self.verbose)

        # Install
        run_command([
            'make',
            f'DESTDIR={self.toolchain_dir}',
            'install',
        ], cwd=build_dir, env=env, verbose=self.verbose)

        stamp.touch()

    def _build_gcc_final(self):
        """Build final GCC with full libc support."""
        stamp = self.stamp_dir / 'gcc_final_installed'
        if stamp.exists():
            print("  [5/5] GCC (final) already built, skipping.")
            return

        print("  [5/5] Building GCC (final)...")

        src_dir = self.build_dir / f'gcc-{self.gcc_version}'

        # Configure
        build_dir = self.build_dir / 'gcc-final-build'
        build_dir.mkdir(exist_ok=True)

        configure_args = [
            str(src_dir / 'configure'),
            f'--prefix={self.toolchain_dir}',
            f'--target={self.target}',
            f'--with-sysroot={self.toolchain_dir}',
            '--with-gnu-ld',
            '--disable-nls',
            '--disable-multilib',
            '--disable-libssp',
            '--disable-libmpx',
            '--enable-target-optspace',
            '--enable-languages=c,c++',
            '--enable-__cxa_atexit',
            '--enable-libstdcxx-dual-abi',
            '--with-default-libstdcxx-abi=new',
            f'--with-gmp=/usr',
            f'--with-mpfr=/usr',
            f'--with-mpc=/usr',
        ]

        env = os.environ.copy()
        env['CFLAGS'] = '-O2 -pipe'
        env['CXXFLAGS'] = '-O2 -pipe'
        env['CFLAGS_FOR_TARGET'] = '-Os -pipe'
        env['CXXFLAGS_FOR_TARGET'] = '-Os -pipe'
        env['PATH'] = f"{self.toolchain_dir / 'bin'}:{env.get('PATH', '')}"

        run_command(configure_args, cwd=build_dir, env=env, verbose=self.verbose)

        # Build
        run_command(['make', f'-j{self.jobs}'], cwd=build_dir, env=env, verbose=self.verbose)

        # Install
        run_command(['make', 'install'], cwd=build_dir, env=env, verbose=self.verbose)

        stamp.touch()

    def get_env(self) -> dict:
        """Get environment variables for using this toolchain."""
        env = os.environ.copy()
        env['PATH'] = f"{self.toolchain_dir / 'bin'}:{env.get('PATH', '')}"
        env['CROSS_COMPILE'] = self.config.cross_compile
        env['CC'] = f'{self.target}-gcc'
        env['CXX'] = f'{self.target}-g++'
        env['AR'] = f'{self.target}-ar'
        env['AS'] = f'{self.target}-as'
        env['LD'] = f'{self.target}-ld'
        env['STRIP'] = f'{self.target}-strip'
        env['OBJCOPY'] = f'{self.target}-objcopy'
        env['OBJDUMP'] = f'{self.target}-objdump'
        env['RANLIB'] = f'{self.target}-ranlib'
        env['STAGING_DIR'] = str(self.config.staging_dir)
        return env
