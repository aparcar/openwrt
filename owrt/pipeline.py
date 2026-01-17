"""
Image pipeline system - composable image transformation steps.

Implements a pipeline-based approach similar to OpenWrt's Build/* macros,
allowing complex image generation through composable steps.

Example pipeline (from target.yaml):
    images:
      - name: sysupgrade.itb
        pipeline:
          - compress: gzip
          - fit:
              compression: gzip
              rootfs: external
          - metadata: append

Each step takes the output of the previous step as input.
"""

import gzip
import hashlib
import lzma
import os
import shutil
import struct
import subprocess
import tempfile
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Any, Type

from .utils import run_command


@dataclass
class PipelineContext:
    """Context passed through the pipeline."""
    # Input files
    kernel: Optional[Path] = None
    dtb: Optional[Path] = None
    rootfs: Optional[Path] = None
    initrd: Optional[Path] = None

    # Configuration
    kernel_load_addr: str = "0x44000000"
    kernel_entry_addr: str = "0x44000000"
    dtb_load_addr: Optional[str] = None
    arch: str = "arm64"
    kernel_version: str = "6.12"
    target: str = ""
    board: str = ""
    profile: str = ""

    # Working directories
    work_dir: Path = field(default_factory=lambda: Path("/tmp"))
    output_dir: Path = field(default_factory=lambda: Path("/tmp"))

    # Current working file (passed through pipeline)
    current: Optional[Path] = None

    # Build artifacts
    artifacts: Dict[str, Path] = field(default_factory=dict)

    # Verbose output
    verbose: bool = False


class PipelineStep(ABC):
    """Base class for pipeline steps."""

    name: str = "base"

    @abstractmethod
    def execute(self, ctx: PipelineContext, config: Dict[str, Any]) -> Path:
        """Execute this step.

        Args:
            ctx: Pipeline context with inputs and working directory
            config: Step-specific configuration

        Returns:
            Path to output file (becomes input for next step)
        """
        pass


class CompressStep(PipelineStep):
    """Compress file with gzip, lzma, or xz."""

    name = "compress"

    def execute(self, ctx: PipelineContext, config: Dict[str, Any]) -> Path:
        compression = config.get("compression", config.get("type", "gzip"))
        input_file = ctx.current or ctx.kernel

        if not input_file or not input_file.exists():
            raise ValueError(f"CompressStep: No input file")

        output_file = ctx.work_dir / f"{input_file.name}.{compression}"

        if ctx.verbose:
            print(f"    Compressing with {compression}...")

        if compression == "gzip":
            with open(input_file, 'rb') as f_in:
                with gzip.open(output_file, 'wb', compresslevel=9) as f_out:
                    shutil.copyfileobj(f_in, f_out)
        elif compression == "lzma":
            with open(input_file, 'rb') as f_in:
                with lzma.open(output_file, 'wb', preset=9) as f_out:
                    shutil.copyfileobj(f_in, f_out)
        elif compression == "xz":
            with open(input_file, 'rb') as f_in:
                with lzma.open(output_file, 'wb', format=lzma.FORMAT_XZ, preset=9) as f_out:
                    shutil.copyfileobj(f_in, f_out)
        elif compression == "none":
            shutil.copy2(input_file, output_file)
        else:
            raise ValueError(f"Unknown compression: {compression}")

        ctx.current = output_file
        return output_file


class PadStep(PipelineStep):
    """Pad file to a specific size or alignment."""

    name = "pad"

    def execute(self, ctx: PipelineContext, config: Dict[str, Any]) -> Path:
        input_file = ctx.current
        if not input_file or not input_file.exists():
            raise ValueError("PadStep: No input file")

        # Parse size (supports K, M, G suffixes)
        size_str = config.get("size", config.get("to", "64k"))
        size = self._parse_size(size_str)

        output_file = ctx.work_dir / f"{input_file.name}.padded"

        if ctx.verbose:
            print(f"    Padding to {size_str}...")

        current_size = input_file.stat().st_size

        with open(input_file, 'rb') as f_in:
            with open(output_file, 'wb') as f_out:
                f_out.write(f_in.read())
                if current_size < size:
                    f_out.write(b'\x00' * (size - current_size))
                elif config.get("align", False):
                    # Align to size boundary
                    aligned = ((current_size + size - 1) // size) * size
                    if aligned > current_size:
                        f_out.write(b'\x00' * (aligned - current_size))

        ctx.current = output_file
        return output_file

    def _parse_size(self, size_str: str) -> int:
        """Parse size string like '64k', '1M', '512' to bytes."""
        size_str = str(size_str).strip().upper()
        multipliers = {'K': 1024, 'M': 1024**2, 'G': 1024**3}

        if size_str[-1] in multipliers:
            return int(size_str[:-1]) * multipliers[size_str[-1]]
        return int(size_str)


class FITStep(PipelineStep):
    """Create FIT (Flattened Image Tree) image."""

    name = "fit"

    def execute(self, ctx: PipelineContext, config: Dict[str, Any]) -> Path:
        from .fit import FITBuilder

        # Get kernel (compressed or original)
        kernel = ctx.current or ctx.kernel
        if not kernel or not kernel.exists():
            raise ValueError("FITStep: No kernel file")

        # Get DTB
        dtb = ctx.dtb
        if not dtb or not dtb.exists():
            raise ValueError(f"FITStep: No DTB file (expected {dtb})")

        # Determine compression from config or filename
        compression = config.get("compression", "none")
        if ".gz" in kernel.name or ".gzip" in kernel.name:
            compression = "gzip"
        elif ".lzma" in kernel.name:
            compression = "lzma"
        elif ".xz" in kernel.name:
            compression = "xz"

        # Rootfs handling
        rootfs = None
        rootfs_mode = config.get("rootfs", "none")
        if rootfs_mode in ("external", "external-static-with-rootfs"):
            rootfs = ctx.rootfs

        # Initrd handling
        initrd = None
        if config.get("initrd") == "embedded" or config.get("with_initrd"):
            initrd = ctx.initrd

        output_file = ctx.work_dir / "fit-image.itb"

        if ctx.verbose:
            print(f"    Creating FIT image (compression={compression})...")

        fit = FITBuilder(arch=ctx.arch, verbose=ctx.verbose)

        # Generate ITS
        its_file = ctx.work_dir / "image.its"
        fit.generate_its(
            output=its_file,
            kernel=kernel,
            dtb=dtb,
            rootfs=rootfs,
            initrd=initrd,
            compression=compression,
            kernel_load_addr=ctx.kernel_load_addr,
            kernel_entry_addr=ctx.kernel_entry_addr,
            dtb_load_addr=ctx.dtb_load_addr,
            kernel_version=ctx.kernel_version,
        )

        # Build FIT
        fit.build(its_file, output_file)

        ctx.current = output_file
        return output_file


class MetadataStep(PipelineStep):
    """Append sysupgrade metadata."""

    name = "metadata"

    def execute(self, ctx: PipelineContext, config: Dict[str, Any]) -> Path:
        from .fit import MetadataBuilder

        input_file = ctx.current
        if not input_file or not input_file.exists():
            raise ValueError("MetadataStep: No input file")

        output_file = ctx.work_dir / f"{input_file.name}.metadata"

        if ctx.verbose:
            print("    Appending sysupgrade metadata...")

        metadata = MetadataBuilder(
            target=ctx.target,
            board=ctx.board,
            verbose=ctx.verbose,
        )

        metadata.append_metadata(input_file, output_file)

        ctx.current = output_file
        return output_file


class UBIStep(PipelineStep):
    """Create UBI image."""

    name = "ubi"

    def execute(self, ctx: PipelineContext, config: Dict[str, Any]) -> Path:
        input_file = ctx.current
        if not input_file:
            raise ValueError("UBIStep: No input file")

        # UBI parameters
        blocksize = self._parse_size(config.get("blocksize", "128k"))
        pagesize = config.get("pagesize", 2048)
        options = config.get("options", "-E 5")

        # Volumes configuration
        volumes = config.get("volumes", [])

        output_file = ctx.work_dir / "ubi.img"

        if ctx.verbose:
            print(f"    Creating UBI image (blocksize={blocksize}, pagesize={pagesize})...")

        # Generate ubinize config
        ubi_cfg = ctx.work_dir / "ubinize.cfg"
        self._generate_ubinize_cfg(ubi_cfg, volumes, ctx)

        # Run ubinize
        cmd = [
            'ubinize',
            '-o', str(output_file),
            '-p', str(blocksize),
            '-m', str(pagesize),
        ]
        if options:
            cmd.extend(options.split())
        cmd.append(str(ubi_cfg))

        run_command(cmd, verbose=ctx.verbose)

        ctx.current = output_file
        return output_file

    def _parse_size(self, size_str: str) -> int:
        size_str = str(size_str).strip().upper()
        multipliers = {'K': 1024, 'M': 1024**2, 'G': 1024**3}
        if size_str[-1] in multipliers:
            return int(size_str[:-1]) * multipliers[size_str[-1]]
        return int(size_str)

    def _generate_ubinize_cfg(self, cfg_path: Path, volumes: List[Dict], ctx: PipelineContext):
        """Generate ubinize configuration file."""
        lines = []
        for i, vol in enumerate(volumes):
            vol_name = vol.get("name", f"vol{i}")
            vol_type = vol.get("type", "static")
            vol_image = vol.get("image", "")

            # Resolve image path
            if vol_image.startswith("${"):
                # Variable reference
                var = vol_image.strip("${}")
                vol_image = str(ctx.artifacts.get(var, ctx.current or ""))
            elif vol_image and not Path(vol_image).is_absolute():
                vol_image = str(ctx.work_dir / vol_image)

            lines.append(f"[{vol_name}]")
            lines.append(f"mode=ubi")
            lines.append(f"vol_id={i}")
            lines.append(f"vol_type={vol_type}")
            lines.append(f"vol_name={vol_name}")

            if vol_image and Path(vol_image).exists():
                lines.append(f"image={vol_image}")

            if "size" in vol:
                lines.append(f"vol_size={vol['size']}")

            lines.append("")

        cfg_path.write_text('\n'.join(lines))


class ConcatStep(PipelineStep):
    """Concatenate multiple files/parts."""

    name = "concat"

    def execute(self, ctx: PipelineContext, config: Dict[str, Any]) -> Path:
        parts = config.get("parts", [])
        if not parts:
            raise ValueError("ConcatStep: No parts specified")

        output_file = ctx.work_dir / "combined.bin"

        if ctx.verbose:
            print(f"    Concatenating {len(parts)} parts...")

        with open(output_file, 'wb') as out:
            for part in parts:
                part_type = part.get("type", "file")
                pad_to = part.get("pad_to")

                # Get the data for this part
                data = self._get_part_data(part, ctx)
                out.write(data)

                # Pad if needed
                if pad_to:
                    pad_size = self._parse_size(pad_to)
                    current_pos = out.tell()
                    if current_pos < pad_size:
                        out.write(b'\x00' * (pad_size - current_pos))
                    elif current_pos % pad_size != 0:
                        # Align to boundary
                        aligned = ((current_pos + pad_size - 1) // pad_size) * pad_size
                        out.write(b'\x00' * (aligned - current_pos))

        ctx.current = output_file
        return output_file

    def _get_part_data(self, part: Dict, ctx: PipelineContext) -> bytes:
        """Get binary data for a part."""
        part_type = part.get("type", "file")

        if part_type == "file":
            path = part.get("path", "")
            if path.startswith("${"):
                var = path.strip("${}")
                path = str(ctx.artifacts.get(var, ""))
            if path and Path(path).exists():
                return Path(path).read_bytes()

        elif part_type == "bl2":
            # BL2 preloader from artifacts
            variant = part.get("variant", "")
            artifact_name = f"bl2-{variant}"
            path = ctx.artifacts.get(artifact_name)
            if path and path.exists():
                return path.read_bytes()

        elif part_type == "fip":
            # FIP from artifacts
            variant = part.get("variant", "")
            artifact_name = f"fip-{variant}"
            path = ctx.artifacts.get(artifact_name)
            if path and path.exists():
                return path.read_bytes()

        elif part_type == "eeprom":
            # EEPROM calibration data
            name = part.get("name", "")
            path = ctx.artifacts.get(f"eeprom-{name}")
            if path and path.exists():
                return path.read_bytes()

        elif part_type == "image":
            # Reference to another generated image
            source = part.get("source", "")
            path = ctx.artifacts.get(source)
            if path and path.exists():
                return path.read_bytes()

        elif part_type == "ubi":
            # Use current UBI image
            if ctx.current and ctx.current.exists():
                return ctx.current.read_bytes()

        return b''

    def _parse_size(self, size_str: str) -> int:
        size_str = str(size_str).strip().upper()
        multipliers = {'K': 1024, 'M': 1024**2, 'G': 1024**3}
        if size_str[-1] in multipliers:
            return int(size_str[:-1]) * multipliers[size_str[-1]]
        return int(size_str)


class CopyKernelStep(PipelineStep):
    """Copy kernel as starting point."""

    name = "kernel"

    def execute(self, ctx: PipelineContext, config: Dict[str, Any]) -> Path:
        if not ctx.kernel or not ctx.kernel.exists():
            raise ValueError("CopyKernelStep: No kernel file")

        # Copy kernel to work dir
        output_file = ctx.work_dir / "kernel.bin"
        shutil.copy2(ctx.kernel, output_file)

        ctx.current = output_file
        return output_file


class CopyRootfsStep(PipelineStep):
    """Copy rootfs as starting point."""

    name = "rootfs"

    def execute(self, ctx: PipelineContext, config: Dict[str, Any]) -> Path:
        if not ctx.rootfs or not ctx.rootfs.exists():
            raise ValueError("CopyRootfsStep: No rootfs file")

        output_file = ctx.work_dir / "rootfs.bin"
        shutil.copy2(ctx.rootfs, output_file)

        ctx.current = output_file
        return output_file


class AppendDTBStep(PipelineStep):
    """Append device tree blob to kernel (Build/append-dtb)."""

    name = "append-dtb"

    def execute(self, ctx: PipelineContext, config: Dict[str, Any]) -> Path:
        input_file = ctx.current or ctx.kernel
        if not input_file or not input_file.exists():
            raise ValueError("AppendDTBStep: No input file")

        if not ctx.dtb or not ctx.dtb.exists():
            raise ValueError(f"AppendDTBStep: No DTB file (expected {ctx.dtb})")

        output_file = ctx.work_dir / f"{input_file.name}.dtb"

        if ctx.verbose:
            print(f"    Appending DTB {ctx.dtb.name}...")

        # Concatenate kernel + DTB
        with open(output_file, 'wb') as out:
            out.write(input_file.read_bytes())
            out.write(ctx.dtb.read_bytes())

        ctx.current = output_file
        return output_file


class UImageStep(PipelineStep):
    """Create U-Boot legacy image (Build/uImage).

    Uses mkimage to create a legacy U-Boot image with header.
    """

    name = "uimage"

    # Architecture names for U-Boot
    UBOOT_ARCH = {
        'aarch64': 'arm64',
        'arm64': 'arm64',
        'arm': 'arm',
        'mips': 'mips',
        'mipsel': 'mips',
        'mips64': 'mips64',
        'x86_64': 'x86_64',
        'i386': 'x86',
    }

    def execute(self, ctx: PipelineContext, config: Dict[str, Any]) -> Path:
        input_file = ctx.current or ctx.kernel
        if not input_file or not input_file.exists():
            raise ValueError("UImageStep: No input file")

        # Get configuration
        compression = config.get("compression", "none")
        name = config.get("name", f"Linux-{ctx.kernel_version}")
        image_type = config.get("type", "kernel")  # kernel, ramdisk, multi
        os_type = config.get("os", "linux")
        load_addr = config.get("load_address", ctx.kernel_load_addr)
        entry_addr = config.get("entry_address", ctx.kernel_entry_addr)

        output_file = ctx.work_dir / "uImage"

        if ctx.verbose:
            print(f"    Creating uImage (compression={compression})...")

        arch = self.UBOOT_ARCH.get(ctx.arch, ctx.arch)

        cmd = [
            'mkimage',
            '-A', arch,
            '-O', os_type,
            '-T', image_type,
            '-C', compression,
            '-a', load_addr,
            '-e', entry_addr,
            '-n', name,
            '-d', str(input_file),
            str(output_file),
        ]

        run_command(cmd, verbose=ctx.verbose)

        ctx.current = output_file
        return output_file


class CheckSizeStep(PipelineStep):
    """Verify image doesn't exceed maximum size (Build/check-size)."""

    name = "check-size"

    def execute(self, ctx: PipelineContext, config: Dict[str, Any]) -> Path:
        input_file = ctx.current
        if not input_file or not input_file.exists():
            raise ValueError("CheckSizeStep: No input file")

        max_size_str = config.get("max", config.get("size", "0"))
        max_size = self._parse_size(max_size_str)

        if max_size == 0:
            # No size limit
            return input_file

        actual_size = input_file.stat().st_size

        if ctx.verbose:
            print(f"    Checking size: {actual_size} <= {max_size}")

        if actual_size > max_size:
            overflow = actual_size - max_size
            raise ValueError(
                f"Image {input_file.name} too large: {actual_size} bytes "
                f"(max {max_size}, overflow {overflow})"
            )

        return input_file

    def _parse_size(self, size_str: str) -> int:
        size_str = str(size_str).strip().upper()
        multipliers = {'K': 1024, 'M': 1024**2, 'G': 1024**3}
        if size_str and size_str[-1] in multipliers:
            return int(size_str[:-1]) * multipliers[size_str[-1]]
        return int(size_str) if size_str else 0


class PadExtraStep(PipelineStep):
    """Add extra padding to end of file (Build/pad-extra).

    Unlike PadStep which pads to a target size, this adds a fixed
    amount of padding to the end.
    """

    name = "pad-extra"

    def execute(self, ctx: PipelineContext, config: Dict[str, Any]) -> Path:
        input_file = ctx.current
        if not input_file or not input_file.exists():
            raise ValueError("PadExtraStep: No input file")

        extra_size = self._parse_size(config.get("size", "0"))
        if extra_size == 0:
            return input_file

        output_file = ctx.work_dir / f"{input_file.name}.padded"

        if ctx.verbose:
            print(f"    Adding {extra_size} bytes padding...")

        with open(input_file, 'rb') as f_in:
            with open(output_file, 'wb') as f_out:
                f_out.write(f_in.read())
                f_out.write(b'\x00' * extra_size)

        ctx.current = output_file
        return output_file

    def _parse_size(self, size_str: str) -> int:
        size_str = str(size_str).strip().upper()
        multipliers = {'K': 1024, 'M': 1024**2, 'G': 1024**3}
        if size_str and size_str[-1] in multipliers:
            return int(size_str[:-1]) * multipliers[size_str[-1]]
        return int(size_str) if size_str else 0


class PadOffsetStep(PipelineStep):
    """Pad to alignment with offset (Build/pad-offset).

    Pads to make (file_size + offset) aligned to pad boundary.
    """

    name = "pad-offset"

    def execute(self, ctx: PipelineContext, config: Dict[str, Any]) -> Path:
        input_file = ctx.current
        if not input_file or not input_file.exists():
            raise ValueError("PadOffsetStep: No input file")

        pad_boundary = self._parse_size(config.get("pad", "4k"))
        offset = self._parse_size(config.get("offset", "0"))

        current_size = input_file.stat().st_size
        # Calculate padding: (pad - ((size + offset) % pad)) % pad
        pad_needed = (pad_boundary - ((current_size + offset) % pad_boundary)) % pad_boundary

        if pad_needed == 0:
            return input_file

        output_file = ctx.work_dir / f"{input_file.name}.aligned"

        if ctx.verbose:
            print(f"    Aligning to {pad_boundary} with offset {offset}...")

        with open(input_file, 'rb') as f_in:
            with open(output_file, 'wb') as f_out:
                f_out.write(f_in.read())
                f_out.write(b'\x00' * pad_needed)

        ctx.current = output_file
        return output_file

    def _parse_size(self, size_str: str) -> int:
        size_str = str(size_str).strip().upper()
        multipliers = {'K': 1024, 'M': 1024**2, 'G': 1024**3}
        if size_str and size_str[-1] in multipliers:
            return int(size_str[:-1]) * multipliers[size_str[-1]]
        return int(size_str) if size_str else 0


class SysupgradeTarStep(PipelineStep):
    """Create sysupgrade tar archive (Build/sysupgrade-tar).

    Creates a tarball containing kernel, rootfs, and sysupgrade.conf
    for devices that use tar-based sysupgrade.
    """

    name = "sysupgrade-tar"

    def execute(self, ctx: PipelineContext, config: Dict[str, Any]) -> Path:
        import tarfile
        import io

        output_file = ctx.work_dir / "sysupgrade.tar"

        if ctx.verbose:
            print("    Creating sysupgrade tarball...")

        # Get components
        kernel_file = ctx.current or ctx.kernel
        rootfs_file = ctx.rootfs

        with tarfile.open(output_file, 'w') as tar:
            # Add sysupgrade.conf with board info
            conf_content = f"BOARD={ctx.board}\nTARGET={ctx.target}\n"
            conf_info = tarfile.TarInfo(name="sysupgrade.conf")
            conf_data = conf_content.encode('utf-8')
            conf_info.size = len(conf_data)
            tar.addfile(conf_info, io.BytesIO(conf_data))

            # Add kernel
            if kernel_file and kernel_file.exists():
                tar.add(str(kernel_file), arcname="kernel")

            # Add rootfs
            if rootfs_file and rootfs_file.exists():
                tar.add(str(rootfs_file), arcname="rootfs")

        ctx.current = output_file
        return output_file


class AppendRootfsStep(PipelineStep):
    """Append rootfs to current image (Build/append-rootfs)."""

    name = "append-rootfs"

    def execute(self, ctx: PipelineContext, config: Dict[str, Any]) -> Path:
        input_file = ctx.current
        if not input_file or not input_file.exists():
            raise ValueError("AppendRootfsStep: No input file")

        if not ctx.rootfs or not ctx.rootfs.exists():
            raise ValueError("AppendRootfsStep: No rootfs file")

        output_file = ctx.work_dir / f"{input_file.name}.combined"

        if ctx.verbose:
            print(f"    Appending rootfs ({ctx.rootfs.stat().st_size // 1024}KB)...")

        with open(output_file, 'wb') as out:
            out.write(input_file.read_bytes())
            out.write(ctx.rootfs.read_bytes())

        ctx.current = output_file
        return output_file


class AppendKernelStep(PipelineStep):
    """Append kernel to current image (Build/append-kernel)."""

    name = "append-kernel"

    def execute(self, ctx: PipelineContext, config: Dict[str, Any]) -> Path:
        input_file = ctx.current
        if not input_file:
            # Start fresh with kernel
            input_file = ctx.work_dir / "empty.bin"
            input_file.write_bytes(b'')

        if not ctx.kernel or not ctx.kernel.exists():
            raise ValueError("AppendKernelStep: No kernel file")

        output_file = ctx.work_dir / f"{input_file.name}.withkernel"

        if ctx.verbose:
            print(f"    Appending kernel ({ctx.kernel.stat().st_size // 1024}KB)...")

        with open(output_file, 'wb') as out:
            if input_file.exists():
                out.write(input_file.read_bytes())
            out.write(ctx.kernel.read_bytes())

        ctx.current = output_file
        return output_file


class PadRootfsStep(PipelineStep):
    """Pad rootfs for JFFS2 end markers (Build/pad-rootfs).

    Pads rootfs to block boundary with JFFS2 end-of-filesystem markers.
    """

    name = "pad-rootfs"

    # JFFS2 magic markers
    JFFS2_EOF_MARK = b'\xde\xad\xc0\xde'  # JFFS2 end of filesystem

    def execute(self, ctx: PipelineContext, config: Dict[str, Any]) -> Path:
        input_file = ctx.current
        if not input_file or not input_file.exists():
            raise ValueError("PadRootfsStep: No input file")

        blocksize = self._parse_size(config.get("blocksize", "64k"))

        output_file = ctx.work_dir / f"{input_file.name}.padded"

        current_size = input_file.stat().st_size
        # Align to block boundary
        aligned = ((current_size + blocksize - 1) // blocksize) * blocksize
        pad_needed = aligned - current_size

        if ctx.verbose:
            print(f"    Padding rootfs to {blocksize} boundary...")

        with open(input_file, 'rb') as f_in:
            with open(output_file, 'wb') as f_out:
                f_out.write(f_in.read())
                if pad_needed > 0:
                    # Pad with 0xff (erased flash)
                    f_out.write(b'\xff' * pad_needed)

        ctx.current = output_file
        return output_file

    def _parse_size(self, size_str: str) -> int:
        size_str = str(size_str).strip().upper()
        multipliers = {'K': 1024, 'M': 1024**2, 'G': 1024**3}
        if size_str and size_str[-1] in multipliers:
            return int(size_str[:-1]) * multipliers[size_str[-1]]
        return int(size_str) if size_str else 0


class SaveArtifactStep(PipelineStep):
    """Save current file as named artifact for later use."""

    name = "save-artifact"

    def execute(self, ctx: PipelineContext, config: Dict[str, Any]) -> Path:
        input_file = ctx.current
        if not input_file or not input_file.exists():
            raise ValueError("SaveArtifactStep: No input file")

        artifact_name = config.get("name", "artifact")
        ctx.artifacts[artifact_name] = input_file

        if ctx.verbose:
            print(f"    Saved artifact: {artifact_name}")

        return input_file


class LoadArtifactStep(PipelineStep):
    """Load a previously saved artifact as current file."""

    name = "load-artifact"

    def execute(self, ctx: PipelineContext, config: Dict[str, Any]) -> Path:
        artifact_name = config.get("name", "")
        if not artifact_name:
            raise ValueError("LoadArtifactStep: No artifact name specified")

        artifact_path = ctx.artifacts.get(artifact_name)
        if not artifact_path or not artifact_path.exists():
            raise ValueError(f"LoadArtifactStep: Artifact '{artifact_name}' not found")

        ctx.current = artifact_path

        if ctx.verbose:
            print(f"    Loaded artifact: {artifact_name}")

        return artifact_path


# Registry of available steps
PIPELINE_STEPS: Dict[str, Type[PipelineStep]] = {
    # Core steps
    'compress': CompressStep,
    'pad': PadStep,
    'pad-extra': PadExtraStep,
    'pad-offset': PadOffsetStep,
    'pad-rootfs': PadRootfsStep,

    # Image format steps
    'fit': FITStep,
    'uimage': UImageStep,
    'ubi': UBIStep,
    'sysupgrade-tar': SysupgradeTarStep,

    # Metadata and validation
    'metadata': MetadataStep,
    'check-size': CheckSizeStep,

    # File manipulation
    'concat': ConcatStep,
    'append-dtb': AppendDTBStep,
    'append-rootfs': AppendRootfsStep,
    'append-kernel': AppendKernelStep,

    # Input selection
    'kernel': CopyKernelStep,
    'rootfs': CopyRootfsStep,

    # Artifact management
    'save-artifact': SaveArtifactStep,
    'load-artifact': LoadArtifactStep,
}


class ImagePipeline:
    """Executes image generation pipelines."""

    def __init__(self, verbose: bool = False):
        self.verbose = verbose
        self.steps = PIPELINE_STEPS.copy()

    def register_step(self, name: str, step_class: Type[PipelineStep]):
        """Register a custom pipeline step."""
        self.steps[name] = step_class

    def execute(self, pipeline: List[Dict[str, Any]], ctx: PipelineContext) -> Path:
        """Execute a pipeline definition.

        Args:
            pipeline: List of step definitions
            ctx: Pipeline context

        Returns:
            Path to final output file
        """
        ctx.work_dir.mkdir(parents=True, exist_ok=True)
        ctx.verbose = self.verbose

        for i, step_def in enumerate(pipeline):
            # Parse step definition
            # Can be: {"compress": {"type": "gzip"}} or {"compress": "gzip"}
            if isinstance(step_def, str):
                step_name = step_def
                step_config = {}
            elif isinstance(step_def, dict):
                step_name = list(step_def.keys())[0]
                step_config = step_def[step_name]
                if isinstance(step_config, str):
                    # Shorthand: {"compress": "gzip"} -> {"compress": {"type": "gzip"}}
                    step_config = {"type": step_config}
                elif step_config is None:
                    step_config = {}
            else:
                raise ValueError(f"Invalid step definition: {step_def}")

            # Get step class
            if step_name not in self.steps:
                raise ValueError(f"Unknown pipeline step: {step_name}")

            step_class = self.steps[step_name]
            step = step_class()

            if self.verbose:
                print(f"  Step {i+1}: {step_name}")

            # Execute step
            ctx.current = step.execute(ctx, step_config)

        return ctx.current


def run_pipeline(
    pipeline_def: List[Dict[str, Any]],
    kernel: Path,
    dtb: Optional[Path] = None,
    rootfs: Optional[Path] = None,
    initrd: Optional[Path] = None,
    output_dir: Path = None,
    work_dir: Path = None,
    arch: str = "arm64",
    kernel_load_addr: str = "0x44000000",
    target: str = "",
    board: str = "",
    profile: str = "",
    artifacts: Dict[str, Path] = None,
    verbose: bool = False,
) -> Path:
    """Convenience function to run a pipeline.

    Args:
        pipeline_def: Pipeline definition from YAML
        kernel: Path to kernel image
        dtb: Path to device tree blob
        rootfs: Path to rootfs image
        initrd: Path to initrd
        output_dir: Output directory
        work_dir: Working directory for intermediate files
        arch: Target architecture
        kernel_load_addr: Kernel load address
        target: Target name (e.g., "mediatek/filogic")
        board: Board name
        profile: Profile name
        artifacts: Dict of named artifacts (bl2, fip, etc.)
        verbose: Enable verbose output

    Returns:
        Path to final output file
    """
    if work_dir is None:
        work_dir = Path(tempfile.mkdtemp(prefix="image-pipeline-"))
    if output_dir is None:
        output_dir = work_dir

    ctx = PipelineContext(
        kernel=kernel,
        dtb=dtb,
        rootfs=rootfs,
        initrd=initrd,
        kernel_load_addr=kernel_load_addr,
        arch=arch,
        target=target,
        board=board,
        profile=profile,
        work_dir=work_dir,
        output_dir=output_dir,
        artifacts=artifacts or {},
        verbose=verbose,
    )

    pipeline = ImagePipeline(verbose=verbose)
    return pipeline.execute(pipeline_def, ctx)
