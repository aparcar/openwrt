"""
Bootloader builder - builds ARM Trusted Firmware and U-Boot for MediaTek SoCs.

Handles:
- Building TF-A (BL2 preloader and BL31 runtime) for specific variants
- Building U-Boot for specific device configurations
- Creating FIP (Firmware Image Package) combining BL31 + U-Boot
"""

import os
import shutil
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional, List

import yaml

from .config import Config
from .utils import run_command, download_file, extract_archive, apply_patches


class BootloaderBuilder:
    """Builds ARM Trusted Firmware and U-Boot for MediaTek SoCs."""

    def __init__(self, config: Config, verbose: bool = False):
        self.config = config
        self.verbose = verbose

        # Paths
        self.build_base = config.build_dir / 'bootloader' / config.name
        self.staging_dir = config.build_dir / 'bootloader-staging'
        self.dl_dir = config.build_dir / 'dl'
        self.host_staging = config.build_dir / 'host-staging'

        # Package definitions directory (bootloader packages are in package/boot/)
        self.boot_packages_dir = config.openwrt_dir / 'package' / 'boot'
        self.tools_dir = config.poc_dir / 'tools'

    def build_tfa(self, variant: str) -> Dict[str, Path]:
        """Build ARM Trusted Firmware for a specific variant.

        Args:
            variant: TFA variant name (e.g., 'mt7981-spim-nand-ubi-ddr4')

        Returns:
            Dict with paths to built binaries: {'bl2': Path, 'bl31': Path}
        """
        print(f"Building TF-A: {variant}")

        # Load package definition
        pkg_yaml = self.boot_packages_dir / 'arm-trusted-firmware-mediatek' / 'package.yaml'
        if not pkg_yaml.exists():
            raise FileNotFoundError(f"TF-A package definition not found: {pkg_yaml}")

        with open(pkg_yaml) as f:
            pkg_config = yaml.safe_load(f)

        # Get variant configuration
        variants = pkg_config.get('variants', {})
        if variant not in variants:
            raise ValueError(f"Unknown TF-A variant: {variant}. Available: {list(variants.keys())}")

        var_config = variants[variant]
        plat = var_config.get('plat', 'mt7981')
        make_flags = var_config.get('make_flags', [])

        # Build directories
        build_dir = self.build_base / 'tfa' / variant
        src_dir = build_dir / 'src'

        # Download and extract source (with patches)
        source = pkg_config['source']
        version = pkg_config.get('version', '')
        patches_dir = self.boot_packages_dir / 'arm-trusted-firmware-mediatek' / 'patches'
        self._fetch_source(source, src_dir, version, patches_dir)

        # Build TF-A
        print(f"  Compiling TF-A for {plat}...")

        # Prepare environment
        env = os.environ.copy()
        # Add toolchain and host tools to PATH
        toolchain_bin = self.config.toolchain_dir / 'bin'
        env['PATH'] = f"{toolchain_bin}:{self.host_staging}/bin:{env.get('PATH', '')}"
        env['CROSS_COMPILE'] = self.config.cross_compile
        env['OPENSSL_DIR'] = str(self.host_staging)

        # Get mkimage path
        mkimage = self.host_staging / 'bin' / 'mkimage'
        if not mkimage.exists():
            print(f"  Warning: mkimage not found at {mkimage}")

        # Build command
        cmd = [
            'make',
            f'-j{os.cpu_count()}',
            f'CROSS_COMPILE={self.config.cross_compile}',
            f'OPENSSL_DIR={self.host_staging}',
            'USE_MKIMAGE=1',
            f'MKIMAGE={mkimage}',
        ]
        cmd.extend(make_flags)

        try:
            run_command(cmd, cwd=src_dir, env=env, verbose=self.verbose)
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"TF-A build failed: {e}")

        # Copy outputs to staging
        self.staging_dir.mkdir(parents=True, exist_ok=True)

        bl2_src = src_dir / 'build' / plat / 'release' / 'bl2.img'
        bl31_src = src_dir / 'build' / plat / 'release' / 'bl31.bin'

        bl2_dst = self.staging_dir / f'{variant}-bl2.img'
        bl31_dst = self.staging_dir / f'{variant}-bl31.bin'

        outputs = {}
        if bl2_src.exists():
            shutil.copy(bl2_src, bl2_dst)
            outputs['bl2'] = bl2_dst
            print(f"  Output: {bl2_dst.name}")
        if bl31_src.exists():
            shutil.copy(bl31_src, bl31_dst)
            outputs['bl31'] = bl31_dst
            print(f"  Output: {bl31_dst.name}")

        return outputs

    def build_uboot(self, variant: str) -> Dict[str, Path]:
        """Build U-Boot for a specific device variant.

        Args:
            variant: U-Boot variant name (e.g., 'mt7981_openwrt_one-snand')

        Returns:
            Dict with paths to built binaries: {'fip': Path, 'bin': Path}
        """
        print(f"Building U-Boot: {variant}")

        # Load package definition
        pkg_yaml = self.boot_packages_dir / 'uboot-mediatek' / 'package.yaml'
        if not pkg_yaml.exists():
            raise FileNotFoundError(f"U-Boot package definition not found: {pkg_yaml}")

        with open(pkg_yaml) as f:
            pkg_config = yaml.safe_load(f)

        # Get variant configuration
        variants = pkg_config.get('variants', {})
        if variant not in variants:
            raise ValueError(f"Unknown U-Boot variant: {variant}. Available: {list(variants.keys())}")

        var_config = variants[variant]
        defconfig = var_config.get('defconfig')
        tfa_variant = var_config.get('tfa_variant')
        fip_config = var_config.get('fip', {})

        if not defconfig:
            raise ValueError(f"No defconfig specified for U-Boot variant: {variant}")

        # Build directories
        build_dir = self.build_base / 'uboot' / variant
        src_dir = build_dir / 'src'

        # Ensure TF-A is built (for BL31)
        bl31_path = self.staging_dir / f'{tfa_variant}-bl31.bin'
        if not bl31_path.exists():
            print(f"  Building required TF-A variant: {tfa_variant}")
            self.build_tfa(tfa_variant)

        if not bl31_path.exists():
            raise FileNotFoundError(f"BL31 not found after TF-A build: {bl31_path}")

        # Download and extract source (with patches)
        source = pkg_config['source']
        version = pkg_config.get('version', '')
        patches_dir = self.boot_packages_dir / 'uboot-mediatek' / 'patches'
        self._fetch_source(source, src_dir, version, patches_dir)

        # Configure U-Boot
        print(f"  Configuring U-Boot with {defconfig}...")

        env = os.environ.copy()
        # Add toolchain and host tools to PATH
        toolchain_bin = self.config.toolchain_dir / 'bin'
        env['PATH'] = f"{toolchain_bin}:{self.host_staging}/bin:{env.get('PATH', '')}"
        env['CROSS_COMPILE'] = self.config.cross_compile

        # Run defconfig
        run_command(
            ['make', defconfig],
            cwd=src_dir,
            env=env,
            verbose=self.verbose,
        )

        # Disable optional features
        config_disable = pkg_config.get('build', {}).get('config_disable', [])
        if config_disable:
            config_file = src_dir / '.config'
            if config_file.exists():
                content = config_file.read_text()
                for opt in config_disable:
                    content = content.replace(
                        f'CONFIG_{opt}=y',
                        f'# CONFIG_{opt} is not set'
                    )
                config_file.write_text(content)

        # Build U-Boot
        print(f"  Compiling U-Boot...")

        run_command(
            ['make', f'-j{os.cpu_count()}', 'u-boot.bin'],
            cwd=src_dir,
            env=env,
            verbose=self.verbose,
        )

        uboot_bin = src_dir / 'u-boot.bin'
        if not uboot_bin.exists():
            raise RuntimeError(f"U-Boot build failed: u-boot.bin not found")

        # Create FIP image
        print(f"  Creating FIP image...")

        fip_compress = fip_config.get('compress', False)

        # Optionally compress binaries
        bl31_input = bl31_path
        uboot_input = uboot_bin

        if fip_compress:
            print(f"    Compressing binaries with xz...")

            # Compress BL31 (use subprocess directly for binary output)
            bl31_xz = build_dir / 'bl31.bin.xz'
            result = subprocess.run(
                ['xz', '-f', '-e', '-k', '-9', '-C', 'crc32', '-c', str(bl31_path)],
                capture_output=True,
            )
            if result.returncode != 0:
                raise subprocess.CalledProcessError(result.returncode, 'xz')
            bl31_xz.write_bytes(result.stdout)
            bl31_input = bl31_xz

            # Compress U-Boot (use subprocess directly for binary output)
            uboot_xz = build_dir / 'u-boot.bin.xz'
            result = subprocess.run(
                ['xz', '-f', '-e', '-k', '-9', '-C', 'crc32', '-c', str(uboot_bin)],
                capture_output=True,
            )
            if result.returncode != 0:
                raise subprocess.CalledProcessError(result.returncode, 'xz')
            uboot_xz.write_bytes(result.stdout)
            uboot_input = uboot_xz

        # Run fiptool
        fiptool = self.host_staging / 'bin' / 'fiptool'
        if not fiptool.exists():
            raise FileNotFoundError(
                f"fiptool not found at {fiptool}. "
                "Build arm-trusted-firmware-tools first."
            )

        fip_output = src_dir / 'u-boot.fip'
        run_command([
            str(fiptool), 'create',
            '--soc-fw', str(bl31_input),
            '--nt-fw', str(uboot_input),
            str(fip_output),
        ], verbose=self.verbose)

        # Copy outputs to staging
        outputs = {}

        fip_dst = self.staging_dir / f'{variant}-u-boot.fip'
        shutil.copy(fip_output, fip_dst)
        outputs['fip'] = fip_dst
        print(f"  Output: {fip_dst.name}")

        uboot_dst = self.staging_dir / f'{variant}-u-boot.bin'
        shutil.copy(uboot_bin, uboot_dst)
        outputs['bin'] = uboot_dst
        print(f"  Output: {uboot_dst.name}")

        return outputs

    def build_for_profile(self, profile: Dict[str, Any]) -> Dict[str, Path]:
        """Build all bootloader components needed for a device profile.

        Args:
            profile: Device profile from target.yaml

        Returns:
            Dict with paths to all built bootloader components
        """
        outputs = {}

        # Get required variants from profile artifacts
        for artifact in profile.get('artifacts', []):
            artifact_type = artifact.get('type')
            variant = artifact.get('variant')

            if artifact_type == 'bl2' and variant:
                # Extract TFA variant from BL2 variant
                # BL2 variants map to TFA variants
                tfa_variant = self._bl2_to_tfa_variant(variant, profile)
                if tfa_variant:
                    try:
                        tfa_outputs = self.build_tfa(tfa_variant)
                        outputs.update({f'tfa-{tfa_variant}': tfa_outputs})
                    except Exception as e:
                        print(f"  Warning: Failed to build TF-A {tfa_variant}: {e}")

            elif artifact_type == 'fip' and variant:
                # FIP variants map to U-Boot variants
                uboot_variant = self._fip_to_uboot_variant(variant)
                if uboot_variant:
                    try:
                        uboot_outputs = self.build_uboot(uboot_variant)
                        outputs.update({f'uboot-{uboot_variant}': uboot_outputs})
                    except Exception as e:
                        print(f"  Warning: Failed to build U-Boot {uboot_variant}: {e}")

        return outputs

    def _bl2_to_tfa_variant(self, bl2_variant: str, profile: Dict[str, Any]) -> Optional[str]:
        """Convert BL2 artifact variant to TFA package variant."""
        # BL2 variants like 'nor-ddr4' need SoC prefix
        # Get SoC from profile hardware section
        hardware = profile.get('hardware', {})
        soc = hardware.get('soc', '').lower()

        if soc.startswith('mt'):
            # e.g., MT7981B -> mt7981
            soc_prefix = soc.rstrip('ab').lower()
            return f'{soc_prefix}-{bl2_variant}'

        return None

    def _fip_to_uboot_variant(self, fip_variant: str) -> Optional[str]:
        """Convert FIP artifact variant to U-Boot package variant."""
        # FIP variants like 'openwrt_one-snand' need SoC prefix
        # Map known variants
        variant_map = {
            'openwrt_one-snand': 'mt7981_openwrt_one-snand',
            'openwrt_one-nor': 'mt7981_openwrt_one-nor',
        }

        return variant_map.get(fip_variant, fip_variant)

    def _fetch_source(self, source: Dict[str, Any], dest_dir: Path, version: str = '',
                      patches_dir: Optional[Path] = None):
        """Download and extract source code, optionally applying patches."""
        source_type = source.get('type')

        if dest_dir.exists():
            # Check if already extracted
            if any(dest_dir.iterdir()):
                return

        dest_dir.mkdir(parents=True, exist_ok=True)
        self.dl_dir.mkdir(parents=True, exist_ok=True)

        if source_type == 'tarball':
            url = source['url'].replace('${version}', version)
            sha256 = source.get('sha256')
            filename = source.get('file', url.split('/')[-1].split('?')[0])

            dl_path = self.dl_dir / filename

            if not dl_path.exists():
                print(f"  Downloading {filename}...")
                download_file(url, dl_path, sha256)

            print(f"  Extracting {filename}...")
            extract_archive(dl_path, dest_dir.parent)

            # Handle strip_components
            strip = source.get('strip_components', 1)
            if strip > 0:
                # Find the extracted directory
                for item in dest_dir.parent.iterdir():
                    if item.is_dir() and item != dest_dir:
                        # Move contents up
                        for sub in item.iterdir():
                            target = dest_dir / sub.name
                            if not target.exists():
                                shutil.move(str(sub), str(dest_dir))
                        # Remove empty extracted dir
                        try:
                            item.rmdir()
                        except OSError:
                            shutil.rmtree(item)
                        break

            # Apply patches after extraction
            if patches_dir and patches_dir.exists():
                print(f"  Applying patches from {patches_dir.name}...")
                apply_patches(dest_dir, patches_dir, verbose=self.verbose)

        elif source_type == 'git':
            url = source['url']
            commit = source.get('commit', 'HEAD')

            print(f"  Cloning {url}...")
            run_command([
                'git', 'clone', '--depth', '1', url, str(dest_dir),
            ], verbose=self.verbose)

            if commit != 'HEAD':
                run_command([
                    'git', 'fetch', '--depth', '1', 'origin', commit,
                ], cwd=dest_dir, verbose=self.verbose)
                run_command([
                    'git', 'checkout', commit,
                ], cwd=dest_dir, verbose=self.verbose)

    def get_bl2_path(self, variant: str) -> Optional[Path]:
        """Get path to built BL2 image."""
        path = self.staging_dir / f'{variant}-bl2.img'
        return path if path.exists() else None

    def get_bl31_path(self, variant: str) -> Optional[Path]:
        """Get path to built BL31 binary."""
        path = self.staging_dir / f'{variant}-bl31.bin'
        return path if path.exists() else None

    def get_fip_path(self, variant: str) -> Optional[Path]:
        """Get path to built FIP image."""
        path = self.staging_dir / f'{variant}-u-boot.fip'
        return path if path.exists() else None
