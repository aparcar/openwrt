"""
FIT (Flattened Image Tree) image generation.

Creates U-Boot compatible FIT images containing kernel, DTB, and rootfs.
"""

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Optional, List, Dict, Any

from .utils import run_command


class FITBuilder:
    """Generates FIT images for U-Boot."""

    def __init__(self, arch: str = "arm64", verbose: bool = False):
        self.arch = arch
        self.verbose = verbose

        # Architecture mapping for FIT
        self.fit_arch = {
            'aarch64': 'arm64',
            'arm': 'arm',
            'mips': 'mips',
            'mipsel': 'mips',
            'x86_64': 'x86_64',
            'i386': 'x86',
        }.get(arch, arch)

    def generate_its(
        self,
        output: Path,
        kernel: Path,
        dtb: Path,
        rootfs: Optional[Path] = None,
        initrd: Optional[Path] = None,
        compression: str = "gzip",
        kernel_load_addr: str = "0x44000000",
        kernel_entry_addr: Optional[str] = None,
        dtb_load_addr: Optional[str] = None,
        description: str = "OpenWrt FIT Image",
        kernel_version: str = "6.12",
    ) -> Path:
        """
        Generate a FIT Image Tree Source (.its) file.

        Args:
            output: Output .its file path
            kernel: Path to kernel image
            dtb: Path to device tree blob
            rootfs: Optional path to rootfs image
            initrd: Optional path to initrd
            compression: Kernel compression (none, gzip, lzma, xz)
            kernel_load_addr: Kernel load address (hex string)
            kernel_entry_addr: Kernel entry address (defaults to load addr)
            dtb_load_addr: DTB load address (optional)
            description: FIT image description
            kernel_version: Kernel version string

        Returns:
            Path to generated .its file
        """
        if kernel_entry_addr is None:
            kernel_entry_addr = kernel_load_addr

        its_content = self._generate_its_content(
            kernel=kernel,
            dtb=dtb,
            rootfs=rootfs,
            initrd=initrd,
            compression=compression,
            kernel_load_addr=kernel_load_addr,
            kernel_entry_addr=kernel_entry_addr,
            dtb_load_addr=dtb_load_addr,
            description=description,
            kernel_version=kernel_version,
        )

        output.parent.mkdir(parents=True, exist_ok=True)
        with open(output, 'w') as f:
            f.write(its_content)

        return output

    def _generate_its_content(
        self,
        kernel: Path,
        dtb: Path,
        rootfs: Optional[Path],
        initrd: Optional[Path],
        compression: str,
        kernel_load_addr: str,
        kernel_entry_addr: str,
        dtb_load_addr: Optional[str],
        description: str,
        kernel_version: str,
    ) -> str:
        """Generate the ITS file content."""
        lines = [
            "/dts-v1/;",
            "",
            "/ {",
            f'\tdescription = "{description}";',
            '\t#address-cells = <1>;',
            "",
            "\timages {",
        ]

        # Kernel node
        lines.extend([
            "\t\tkernel-1 {",
            f'\t\t\tdescription = "OpenWrt Linux-{kernel_version}";',
            f'\t\t\tdata = /incbin/("{kernel}");',
            '\t\t\ttype = "kernel";',
            f'\t\t\tarch = "{self.fit_arch}";',
            '\t\t\tos = "linux";',
            f'\t\t\tcompression = "{compression}";',
            f'\t\t\tload = <{kernel_load_addr}>;',
            f'\t\t\tentry = <{kernel_entry_addr}>;',
            '\t\t\thash-1 {',
            '\t\t\t\talgo = "crc32";',
            '\t\t\t};',
            '\t\t\thash-2 {',
            '\t\t\t\talgo = "sha1";',
            '\t\t\t};',
            '\t\t};',
            "",
        ])

        # DTB node
        dtb_load_line = ""
        if dtb_load_addr:
            dtb_load_line = f'\t\t\tload = <{dtb_load_addr}>;'

        lines.extend([
            "\t\tfdt-1 {",
            '\t\t\tdescription = "OpenWrt device tree blob";',
            f'\t\t\tdata = /incbin/("{dtb}");',
            '\t\t\ttype = "flat_dt";',
            f'\t\t\tarch = "{self.fit_arch}";',
            '\t\t\tcompression = "none";',
        ])
        if dtb_load_line:
            lines.append(dtb_load_line)
        lines.extend([
            '\t\t\thash-1 {',
            '\t\t\t\talgo = "crc32";',
            '\t\t\t};',
            '\t\t\thash-2 {',
            '\t\t\t\talgo = "sha1";',
            '\t\t\t};',
            '\t\t};',
            "",
        ])

        # Rootfs node (if provided)
        if rootfs and rootfs.exists():
            lines.extend([
                "\t\trootfs-1 {",
                '\t\t\tdescription = "OpenWrt rootfs";',
                f'\t\t\tdata = /incbin/("{rootfs}");',
                '\t\t\ttype = "filesystem";',
                f'\t\t\tarch = "{self.fit_arch}";',
                '\t\t\tcompression = "none";',
                '\t\t\thash-1 {',
                '\t\t\t\talgo = "crc32";',
                '\t\t\t};',
                '\t\t\thash-2 {',
                '\t\t\t\talgo = "sha1";',
                '\t\t\t};',
                '\t\t};',
                "",
            ])

        # Initrd node (if provided)
        if initrd and initrd.exists():
            lines.extend([
                "\t\tinitrd-1 {",
                '\t\t\tdescription = "OpenWrt initrd";',
                f'\t\t\tdata = /incbin/("{initrd}");',
                '\t\t\ttype = "ramdisk";',
                f'\t\t\tarch = "{self.fit_arch}";',
                '\t\t\tcompression = "none";',
                '\t\t\thash-1 {',
                '\t\t\t\talgo = "crc32";',
                '\t\t\t};',
                '\t\t};',
                "",
            ])

        lines.extend([
            "\t};",  # Close images
            "",
            "\tconfigurations {",
            '\t\tdefault = "config-1";',
            "\t\tconfig-1 {",
            '\t\t\tdescription = "OpenWrt";',
            '\t\t\tkernel = "kernel-1";',
            '\t\t\tfdt = "fdt-1";',
        ])

        # Add loadables for rootfs
        if rootfs and rootfs.exists():
            lines.append('\t\t\tloadables = "rootfs-1";')

        # Add ramdisk for initrd
        if initrd and initrd.exists():
            lines.append('\t\t\tramdisk = "initrd-1";')

        lines.extend([
            '\t\t};',
            '\t};',  # Close configurations
            '};',  # Close root
            "",
        ])

        return '\n'.join(lines)

    def build_fit(
        self,
        its_file: Path,
        output: Path,
        external_data: bool = True,
        dtc_path: Optional[Path] = None,
    ) -> Path:
        """
        Compile ITS file to FIT image using mkimage.

        Args:
            its_file: Input .its file
            output: Output .itb file
            external_data: Use external data layout (-E flag)
            dtc_path: Path to dtc binary (for PATH)

        Returns:
            Path to generated .itb file
        """
        output.parent.mkdir(parents=True, exist_ok=True)

        # Build command
        cmd = ['mkimage']
        if external_data:
            cmd.extend(['-E', '-B', '0x1000'])
        cmd.extend(['-f', str(its_file), str(output)])

        # Set up environment with dtc in PATH
        env = os.environ.copy()
        if dtc_path:
            env['PATH'] = f"{dtc_path}:{env.get('PATH', '')}"

        run_command(cmd, env=env, verbose=self.verbose)

        return output

    def create_fit_image(
        self,
        output: Path,
        kernel: Path,
        dtb: Path,
        rootfs: Optional[Path] = None,
        initrd: Optional[Path] = None,
        compression: str = "gzip",
        kernel_load_addr: str = "0x44000000",
        kernel_entry_addr: Optional[str] = None,
        dtb_load_addr: Optional[str] = None,
        external_data: bool = True,
        dtc_path: Optional[Path] = None,
    ) -> Path:
        """
        Create a complete FIT image.

        This is a convenience method that generates the ITS and compiles it.

        Returns:
            Path to generated .itb file
        """
        # Sync rootfs to page boundary if provided
        if rootfs and rootfs.exists():
            rootfs_sync = rootfs.with_suffix('.pagesync')
            run_command([
                'dd',
                f'if={rootfs}',
                f'of={rootfs_sync}',
                'bs=4096',
                'conv=sync',
            ], verbose=self.verbose)
            rootfs = rootfs_sync

        # Generate ITS
        its_file = output.with_suffix('.its')
        self.generate_its(
            output=its_file,
            kernel=kernel,
            dtb=dtb,
            rootfs=rootfs,
            initrd=initrd,
            compression=compression,
            kernel_load_addr=kernel_load_addr,
            kernel_entry_addr=kernel_entry_addr,
            dtb_load_addr=dtb_load_addr,
        )

        # Compile FIT
        return self.build_fit(its_file, output, external_data, dtc_path)


class JFFS2Builder:
    """Creates JFFS2 (Journaling Flash File System 2) images using mkfs.jffs2."""

    def __init__(
        self,
        erase_block_size: int = 65536,  # 64k default
        page_size: Optional[int] = None,  # For NAND
        big_endian: bool = False,
        no_cleanmarkers: bool = False,  # For NAND/UBI
        staging_dir: Optional[Path] = None,
        verbose: bool = False,
    ):
        """Initialize JFFS2 builder.

        Args:
            erase_block_size: Erase block size in bytes (typically 64k or 128k)
            page_size: NAND page size (set for NAND flash, None for NOR)
            big_endian: Use big endian format
            no_cleanmarkers: Don't add cleanmarkers (for NAND/UBI)
            staging_dir: Path to host-staging directory for tools
            verbose: Enable verbose output
        """
        self.erase_block_size = erase_block_size
        self.page_size = page_size
        self.big_endian = big_endian
        self.no_cleanmarkers = no_cleanmarkers
        self.staging_dir = staging_dir
        self.verbose = verbose

    def _find_mkfs_jffs2(self) -> str:
        """Find mkfs.jffs2 binary."""
        if self.staging_dir:
            for subdir in ['sbin', 'bin']:
                mkfs = self.staging_dir / subdir / 'mkfs.jffs2'
                if mkfs.exists():
                    return str(mkfs)
        return 'mkfs.jffs2'

    def build_jffs2(
        self,
        source_dir: Path,
        output: Path,
        pad: bool = True,
        squash_uids: bool = True,
    ) -> Path:
        """Build JFFS2 image from a source directory.

        Args:
            source_dir: Path to source directory (rootfs or overlay content)
            output: Output JFFS2 image path
            pad: Pad output to erase block boundary
            squash_uids: Squash ownership info to root

        Returns:
            Path to generated JFFS2 image
        """
        output.parent.mkdir(parents=True, exist_ok=True)

        cmd = [self._find_mkfs_jffs2()]

        # Erase block size
        cmd.extend(['-e', str(self.erase_block_size)])

        # Endianness
        if self.big_endian:
            cmd.append('--big-endian')
        else:
            cmd.append('--little-endian')

        # NAND options
        if self.page_size:
            cmd.extend(['--pagesize', str(self.page_size)])
        if self.no_cleanmarkers:
            cmd.append('--no-cleanmarkers')

        # Padding
        if pad:
            cmd.append('--pad')

        # Squash UIDs
        if squash_uids:
            cmd.append('--squash-uids')

        # Source and output
        cmd.extend(['-d', str(source_dir)])
        cmd.extend(['-o', str(output)])

        run_command(cmd, verbose=self.verbose)

        return output

    def build_empty_overlay(
        self,
        output: Path,
        pad: bool = True,
    ) -> Path:
        """Build an empty JFFS2 image for overlay filesystem.

        Creates a minimal JFFS2 image that can be used as an overlay
        partition on top of a read-only squashfs root.

        Args:
            output: Output JFFS2 image path
            pad: Pad output to erase block boundary

        Returns:
            Path to generated JFFS2 overlay image
        """
        import tempfile

        # Create temporary empty directory
        with tempfile.TemporaryDirectory() as tmpdir:
            empty_dir = Path(tmpdir) / 'empty'
            empty_dir.mkdir()
            return self.build_jffs2(empty_dir, output, pad=pad, squash_uids=True)


class UBIFSBuilder:
    """Creates UBIFS filesystem images using mkfs.ubifs."""

    def __init__(
        self,
        min_io_size: int = 2048,
        leb_size: int = 126976,  # 128k - 2*2048 = 124KiB
        max_leb_cnt: int = 4096,
        compression: str = "zlib",
        staging_dir: Optional[Path] = None,
        verbose: bool = False,
    ):
        """Initialize UBIFS builder.

        Args:
            min_io_size: Minimum I/O unit size (typically NAND page size)
            leb_size: Logical erase block size (PEB size - 2 * min_io_size)
            max_leb_cnt: Maximum number of logical erase blocks
            compression: Compression type (none, lzo, zlib)
            staging_dir: Path to host-staging directory for tools
            verbose: Enable verbose output
        """
        self.min_io_size = min_io_size
        self.leb_size = leb_size
        self.max_leb_cnt = max_leb_cnt
        self.compression = compression
        self.staging_dir = staging_dir
        self.verbose = verbose

    def _find_mkfs_ubifs(self) -> str:
        """Find mkfs.ubifs binary."""
        if self.staging_dir:
            # Check host-staging locations
            for subdir in ['sbin', 'bin']:
                mkfs = self.staging_dir / subdir / 'mkfs.ubifs'
                if mkfs.exists():
                    return str(mkfs)
        # Fall back to system PATH
        return 'mkfs.ubifs'

    @staticmethod
    def calculate_leb_size(block_size: int, page_size: int) -> int:
        """Calculate LEB size from physical erase block and page sizes.

        UBIFS overhead is 2 pages per physical erase block (PEB):
        - One page for the erase counter header (EC)
        - One page for the volume identifier header (VID)

        Args:
            block_size: Physical erase block size in bytes
            page_size: NAND page size in bytes

        Returns:
            Logical erase block (LEB) size in bytes
        """
        return block_size - (2 * page_size)

    def build_ubifs(
        self,
        rootfs_dir: Path,
        output: Path,
        space_fixup: bool = True,
        squash_uids: bool = True,
    ) -> Path:
        """Build UBIFS image from a root filesystem directory.

        Args:
            rootfs_dir: Path to root filesystem directory
            output: Output UBIFS image path
            space_fixup: Enable free space fixup for first mount
            squash_uids: Squash ownership info to root

        Returns:
            Path to generated UBIFS image
        """
        output.parent.mkdir(parents=True, exist_ok=True)

        cmd = [
            self._find_mkfs_ubifs(),
            '-m', str(self.min_io_size),
            '-e', str(self.leb_size),
            '-c', str(self.max_leb_cnt),
        ]

        if self.compression != 'none':
            cmd.extend(['--compr', self.compression])

        if space_fixup:
            cmd.append('--space-fixup')

        if squash_uids:
            cmd.append('--squash-uids')

        cmd.extend([
            '-r', str(rootfs_dir),
            '-o', str(output),
        ])

        run_command(cmd, verbose=self.verbose)

        return output


class UBIBuilder:
    """Creates UBI (Unsorted Block Image) volumes."""

    def __init__(
        self,
        block_size: str = "128k",
        page_size: int = 2048,
        sub_page_size: Optional[int] = None,
        vid_hdr_offset: Optional[int] = None,
        staging_dir: Optional[Path] = None,
        verbose: bool = False,
    ):
        self.block_size = block_size
        self.page_size = page_size
        self.sub_page_size = sub_page_size
        self.vid_hdr_offset = vid_hdr_offset
        self.staging_dir = staging_dir
        self.verbose = verbose

    def _find_ubinize(self) -> str:
        """Find ubinize binary."""
        if self.staging_dir:
            # Check host-staging/sbin first
            ubinize = self.staging_dir / 'sbin' / 'ubinize'
            if ubinize.exists():
                return str(ubinize)
            # Check host-staging/bin as fallback
            ubinize = self.staging_dir / 'bin' / 'ubinize'
            if ubinize.exists():
                return str(ubinize)
        # Fall back to system PATH
        return 'ubinize'

    def _parse_size(self, size: str) -> int:
        """Parse size string (e.g., '128k', '1M') to bytes."""
        size = size.upper()
        if size.endswith('K'):
            return int(size[:-1]) * 1024
        elif size.endswith('M'):
            return int(size[:-1]) * 1024 * 1024
        elif size.endswith('G'):
            return int(size[:-1]) * 1024 * 1024 * 1024
        return int(size)

    def generate_config(
        self,
        output: Path,
        volumes: List[Dict[str, Any]],
    ) -> Path:
        """
        Generate ubinize configuration file.

        Args:
            output: Output config file path
            volumes: List of volume definitions, each with:
                - name: Volume name
                - image: Path to volume image (optional)
                - type: 'static' or 'dynamic' (default: dynamic)
                - size: Volume size (optional, for empty volumes)
                - autoresize: Enable autoresize flag (default: False for last volume)

        Returns:
            Path to generated config file
        """
        output.parent.mkdir(parents=True, exist_ok=True)

        lines = []
        for i, vol in enumerate(volumes):
            name = vol['name']
            vol_type = vol.get('type', 'dynamic')
            image = vol.get('image')
            size = vol.get('size')
            autoresize = vol.get('autoresize', False)

            lines.append(f"[{name}]")
            lines.append("mode=ubi")
            lines.append(f"vol_id={i}")
            lines.append(f"vol_type={vol_type}")
            lines.append(f"vol_name={name}")

            if image:
                lines.append(f"image={image}")
            elif size:
                lines.append(f"vol_size={size}")
            else:
                lines.append("vol_size=1MiB")

            if autoresize:
                lines.append("vol_flags=autoresize")

            lines.append("")

        with open(output, 'w') as f:
            f.write('\n'.join(lines))

        return output

    def build_ubi(
        self,
        config: Path,
        output: Path,
        extra_opts: Optional[List[str]] = None,
    ) -> Path:
        """
        Build UBI image using ubinize.

        Args:
            config: Path to ubinize config file
            output: Output UBI image path
            extra_opts: Additional ubinize options

        Returns:
            Path to generated UBI image
        """
        output.parent.mkdir(parents=True, exist_ok=True)

        # Parse block size
        block_size_bytes = self._parse_size(self.block_size)
        block_size_kib = block_size_bytes // 1024

        cmd = [
            self._find_ubinize(),
            '-o', str(output),
            '-p', f'{block_size_kib}KiB',
            '-m', str(self.page_size),
        ]

        if self.sub_page_size:
            cmd.extend(['-s', str(self.sub_page_size)])

        if self.vid_hdr_offset:
            cmd.extend(['-O', str(self.vid_hdr_offset)])

        if extra_opts:
            cmd.extend(extra_opts)

        cmd.append(str(config))

        run_command(cmd, verbose=self.verbose)

        return output

    def create_ubi_image(
        self,
        output: Path,
        volumes: List[Dict[str, Any]],
        extra_opts: Optional[List[str]] = None,
    ) -> Path:
        """
        Create a complete UBI image.

        This is a convenience method that generates config and runs ubinize.

        Returns:
            Path to generated UBI image
        """
        config = output.with_suffix('.cfg')
        self.generate_config(config, volumes)
        return self.build_ubi(config, output, extra_opts)


class MetadataBuilder:
    """Appends sysupgrade metadata to firmware images."""

    def __init__(
        self,
        version_dist: str = "OpenWrt",
        version_number: str = "SNAPSHOT",
        revision: str = "",
        target: str = "",
        board: str = "",
        host_staging: Optional[Path] = None,
        source_dir: Optional[Path] = None,
        verbose: bool = False,
    ):
        self.version_dist = version_dist
        self.version_number = version_number
        self.revision = revision or self._get_git_revision(source_dir)
        self.target = target
        self.board = board
        self.host_staging = host_staging
        self.verbose = verbose

    def _get_git_revision(self, source_dir: Optional[Path] = None) -> str:
        """Get git revision in OpenWrt format: r<count>-<short_hash>."""
        try:
            cwd = str(source_dir) if source_dir else None

            # Get commit count
            result = subprocess.run(
                ['git', 'rev-list', '--count', 'HEAD'],
                capture_output=True,
                text=True,
                cwd=cwd,
            )
            if result.returncode != 0:
                return ""
            count = result.stdout.strip()

            # Get short hash
            result = subprocess.run(
                ['git', 'rev-parse', '--short', 'HEAD'],
                capture_output=True,
                text=True,
                cwd=cwd,
            )
            if result.returncode != 0:
                return ""
            short_hash = result.stdout.strip()

            return f"r{count}-{short_hash}"
        except Exception:
            return ""

    def generate_metadata(
        self,
        supported_devices: Optional[List[str]] = None,
        compat_version: str = "1.0",
    ) -> str:
        """Generate sysupgrade metadata JSON."""
        metadata = {
            "metadata_version": "1.1",
            "compat_version": compat_version,
            "supported_devices": supported_devices or [self.board],
            "version": {
                "dist": self.version_dist,
                "version": self.version_number,
                "revision": self.revision,
                "target": self.target,
                "board": self.board,
            }
        }

        import json
        return json.dumps(metadata, separators=(',', ':'))

    def append_metadata(
        self,
        image: Path,
        supported_devices: Optional[List[str]] = None,
    ) -> Path:
        """
        Append sysupgrade metadata to an image.

        Uses fwtool to insert metadata JSON into the firmware image.
        This metadata is used by sysupgrade to verify device compatibility.

        Returns:
            Path to modified image
        """
        import shutil
        import tempfile

        if not image.exists():
            print(f"    Warning: Image not found for metadata: {image}")
            return image

        metadata = self.generate_metadata(supported_devices)
        print(f"    Appending sysupgrade metadata...")

        # Find fwtool binary
        fwtool_bin = None
        if self.host_staging:
            fwtool_path = self.host_staging / 'bin' / 'fwtool'
            if fwtool_path.exists():
                fwtool_bin = str(fwtool_path)

        if not fwtool_bin:
            # Try to find in PATH
            fwtool_bin = shutil.which('fwtool')

        if not fwtool_bin:
            print(f"    Warning: fwtool not found, skipping metadata insertion")
            print(f"      Searched: {self.host_staging / 'bin' / 'fwtool' if self.host_staging else 'PATH'}")
            return image

        # Write metadata to temp file to avoid shell escaping issues
        try:
            with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
                f.write(metadata)
                metadata_file = f.name

            # Use fwtool to insert metadata
            run_command(
                [fwtool_bin, '-I', metadata_file, str(image)],
                verbose=self.verbose,
            )
            print(f"      Metadata: {metadata[:60]}...")

            # Clean up temp file
            Path(metadata_file).unlink(missing_ok=True)

        except subprocess.CalledProcessError as e:
            print(f"    Warning: fwtool failed: {e}")
            # Clean up temp file on error
            if 'metadata_file' in locals():
                Path(metadata_file).unlink(missing_ok=True)
        except Exception as e:
            print(f"    Warning: Metadata insertion failed: {e}")
            if 'metadata_file' in locals():
                Path(metadata_file).unlink(missing_ok=True)

        # Generate SHA256 hash
        hash_file = image.with_suffix(image.suffix + '.sha256sum')
        run_command(
            ['sh', '-c', f'sha256sum "{image}" | cut -d" " -f1 > "{hash_file}"'],
            verbose=self.verbose,
        )

        return image
