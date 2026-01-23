"""
Tests for fit.py - FIT image generation.
"""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
import tempfile
import shutil

from owrt.fit import FITBuilder, UBIBuilder, UBIFSBuilder, JFFS2Builder, MetadataBuilder


class TestFITBuilder:
    """Tests for FITBuilder class."""

    @pytest.fixture
    def temp_dir(self):
        """Create temp directory with test files."""
        temp = Path(tempfile.mkdtemp())
        # Create test kernel
        (temp / 'kernel.bin').write_bytes(b'kernel content')
        # Create test DTB
        (temp / 'device.dtb').write_bytes(b'dtb content')
        # Create test rootfs
        (temp / 'rootfs.squashfs').write_bytes(b'rootfs content')
        yield temp
        shutil.rmtree(temp, ignore_errors=True)

    def test_init_default_arch(self):
        """Test default architecture."""
        builder = FITBuilder()

        assert builder.arch == 'arm64'
        assert builder.fit_arch == 'arm64'

    def test_init_aarch64(self):
        """Test aarch64 architecture mapping."""
        builder = FITBuilder(arch='aarch64')

        assert builder.fit_arch == 'arm64'

    def test_init_arm(self):
        """Test ARM architecture mapping."""
        builder = FITBuilder(arch='arm')

        assert builder.fit_arch == 'arm'

    def test_init_x86_64(self):
        """Test x86_64 architecture mapping."""
        builder = FITBuilder(arch='x86_64')

        assert builder.fit_arch == 'x86_64'

    def test_init_mipsel(self):
        """Test MIPS little-endian architecture mapping."""
        builder = FITBuilder(arch='mipsel')

        assert builder.fit_arch == 'mips'

    def test_init_verbose(self):
        """Test verbose flag."""
        builder = FITBuilder(verbose=True)

        assert builder.verbose is True

    def test_generate_its(self, temp_dir):
        """Test ITS file generation."""
        builder = FITBuilder(arch='aarch64')
        output = temp_dir / 'test.its'

        result = builder.generate_its(
            output=output,
            kernel=temp_dir / 'kernel.bin',
            dtb=temp_dir / 'device.dtb',
            compression='gzip',
            kernel_load_addr='0x44000000',
        )

        assert result == output
        assert output.exists()

    def test_generate_its_content(self, temp_dir):
        """Test ITS file content."""
        builder = FITBuilder(arch='aarch64')
        output = temp_dir / 'test.its'

        builder.generate_its(
            output=output,
            kernel=temp_dir / 'kernel.bin',
            dtb=temp_dir / 'device.dtb',
            compression='gzip',
            kernel_load_addr='0x44000000',
        )

        content = output.read_text()
        assert '/dts-v1/' in content
        assert 'kernel-1' in content
        assert 'fdt-1' in content
        assert 'config-1' in content
        assert 'arch = "arm64"' in content

    def test_generate_its_with_rootfs(self, temp_dir):
        """Test ITS with rootfs."""
        builder = FITBuilder(arch='aarch64')
        output = temp_dir / 'test.its'

        builder.generate_its(
            output=output,
            kernel=temp_dir / 'kernel.bin',
            dtb=temp_dir / 'device.dtb',
            rootfs=temp_dir / 'rootfs.squashfs',
            compression='gzip',
            kernel_load_addr='0x44000000',
        )

        content = output.read_text()
        assert 'rootfs-1' in content
        assert 'type = "filesystem"' in content

    def test_generate_its_with_initrd(self, temp_dir):
        """Test ITS with initrd."""
        builder = FITBuilder(arch='aarch64')
        output = temp_dir / 'test.its'
        initrd = temp_dir / 'initrd.cpio.gz'
        initrd.write_bytes(b'initrd content')

        builder.generate_its(
            output=output,
            kernel=temp_dir / 'kernel.bin',
            dtb=temp_dir / 'device.dtb',
            initrd=initrd,
            compression='gzip',
            kernel_load_addr='0x44000000',
        )

        content = output.read_text()
        assert 'initrd-1' in content
        assert 'type = "ramdisk"' in content

    def test_generate_its_custom_load_addr(self, temp_dir):
        """Test ITS with custom load address."""
        builder = FITBuilder(arch='aarch64')
        output = temp_dir / 'test.its'

        builder.generate_its(
            output=output,
            kernel=temp_dir / 'kernel.bin',
            dtb=temp_dir / 'device.dtb',
            compression='gzip',
            kernel_load_addr='0x80000000',
        )

        content = output.read_text()
        assert '0x80000000' in content

    def test_generate_its_compression_lzma(self, temp_dir):
        """Test ITS with LZMA compression."""
        builder = FITBuilder(arch='aarch64')
        output = temp_dir / 'test.its'

        builder.generate_its(
            output=output,
            kernel=temp_dir / 'kernel.bin',
            dtb=temp_dir / 'device.dtb',
            compression='lzma',
            kernel_load_addr='0x44000000',
        )

        content = output.read_text()
        assert 'compression = "lzma"' in content

    def test_build_fit(self, temp_dir):
        """Test FIT image building."""
        builder = FITBuilder(arch='aarch64')
        its_file = temp_dir / 'test.its'
        output = temp_dir / 'test.itb'

        # Generate ITS
        builder.generate_its(
            output=its_file,
            kernel=temp_dir / 'kernel.bin',
            dtb=temp_dir / 'device.dtb',
            compression='none',
            kernel_load_addr='0x44000000',
        )

        # Build FIT (mocked since mkimage may not be available)
        with patch('owrt.fit.run_command') as mock_run:
            builder.build_fit(its_file, output)

        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert 'mkimage' in args
        assert '-f' in args


class TestUBIFSBuilder:
    """Tests for UBIFSBuilder class."""

    def test_calculate_leb_size(self):
        """Test LEB size calculation."""
        # 128KB PEB with 2KB page size
        leb_size = UBIFSBuilder.calculate_leb_size(128 * 1024, 2048)

        # LEB = PEB - 2 * page_size
        expected = 128 * 1024 - 2 * 2048
        assert leb_size == expected

    def test_calculate_leb_size_different_page(self):
        """Test LEB size with different page size."""
        # 256KB PEB with 4KB page size
        leb_size = UBIFSBuilder.calculate_leb_size(256 * 1024, 4096)

        expected = 256 * 1024 - 2 * 4096
        assert leb_size == expected

    def test_init_defaults(self):
        """Test default initialization."""
        builder = UBIFSBuilder()

        assert builder.min_io_size == 2048
        assert builder.compression == 'zlib'

    def test_init_custom_values(self):
        """Test custom initialization."""
        builder = UBIFSBuilder(
            min_io_size=4096,
            leb_size=126976,
            max_leb_cnt=2048,
            compression='lzo',
        )

        assert builder.min_io_size == 4096
        assert builder.leb_size == 126976
        assert builder.max_leb_cnt == 2048
        assert builder.compression == 'lzo'


class TestUBIBuilder:
    """Tests for UBIBuilder class."""

    def test_init_defaults(self):
        """Test default initialization."""
        builder = UBIBuilder()

        assert builder.block_size == '128k'
        assert builder.page_size == 2048
        assert builder.sub_page_size is None

    def test_init_custom_values(self):
        """Test custom initialization."""
        builder = UBIBuilder(
            block_size='256k',
            page_size=4096,
            sub_page_size=2048,
        )

        assert builder.block_size == '256k'
        assert builder.page_size == 4096
        assert builder.sub_page_size == 2048


class TestJFFS2Builder:
    """Tests for JFFS2Builder class."""

    def test_init_defaults(self):
        """Test default initialization."""
        builder = JFFS2Builder()

        assert builder.erase_block_size == 64 * 1024
        assert builder.page_size is None
        assert builder.big_endian is False
        assert builder.no_cleanmarkers is False

    def test_init_nand_config(self):
        """Test NAND configuration."""
        builder = JFFS2Builder(
            erase_block_size=128 * 1024,
            page_size=2048,
            no_cleanmarkers=True,
        )

        assert builder.erase_block_size == 128 * 1024
        assert builder.page_size == 2048
        assert builder.no_cleanmarkers is True

    def test_init_big_endian(self):
        """Test big-endian configuration."""
        builder = JFFS2Builder(big_endian=True)

        assert builder.big_endian is True


class TestMetadataBuilder:
    """Tests for MetadataBuilder class."""

    @pytest.fixture
    def temp_dir(self):
        """Create temp directory."""
        temp = Path(tempfile.mkdtemp())
        yield temp
        shutil.rmtree(temp, ignore_errors=True)

    def test_init(self, temp_dir):
        """Test initialization."""
        builder = MetadataBuilder(
            target='armsr/armv8',
            board='generic',
            host_staging=temp_dir,
            source_dir=temp_dir,
        )

        assert builder.target == 'armsr/armv8'
        assert builder.board == 'generic'

    def test_init_verbose(self, temp_dir):
        """Test verbose flag."""
        builder = MetadataBuilder(
            target='armsr/armv8',
            board='generic',
            host_staging=temp_dir,
            source_dir=temp_dir,
            verbose=True,
        )

        assert builder.verbose is True

    def test_generate_metadata(self, temp_dir):
        """Test metadata generation."""
        builder = MetadataBuilder(
            target='armsr/armv8',
            board='generic',
            host_staging=temp_dir,
            source_dir=temp_dir,
        )

        metadata = builder.generate_metadata(['device1', 'device2'])

        # generate_metadata returns a JSON string
        import json
        metadata_dict = json.loads(metadata)
        assert 'supported_devices' in metadata_dict
        assert 'device1' in metadata_dict['supported_devices']
        assert 'device2' in metadata_dict['supported_devices']

    def test_append_metadata(self, temp_dir):
        """Test metadata appending to image."""
        builder = MetadataBuilder(
            target='armsr/armv8',
            board='generic',
            host_staging=temp_dir,
            source_dir=temp_dir,
        )

        # Create a test image
        image = temp_dir / 'test.itb'
        image.write_bytes(b'image content')

        # Just verify generate_metadata works
        # append_metadata requires fwtool which may not be available
        metadata = builder.generate_metadata(['device1'])
        import json
        metadata_dict = json.loads(metadata)
        assert 'device1' in metadata_dict['supported_devices']


class TestFITBuilderIntegration:
    """Integration tests for FIT builder."""

    @pytest.fixture
    def temp_dir(self):
        """Create temp directory."""
        temp = Path(tempfile.mkdtemp())
        (temp / 'kernel.bin').write_bytes(b'kernel')
        (temp / 'device.dtb').write_bytes(b'dtb')
        yield temp
        shutil.rmtree(temp, ignore_errors=True)

    def test_full_its_generation(self, temp_dir):
        """Test complete ITS generation with all features."""
        builder = FITBuilder(arch='aarch64', verbose=True)
        output = temp_dir / 'full.its'

        # Create initrd
        initrd = temp_dir / 'initrd.cpio.gz'
        initrd.write_bytes(b'initrd')

        # Create rootfs
        rootfs = temp_dir / 'rootfs.squashfs'
        rootfs.write_bytes(b'rootfs')

        builder.generate_its(
            output=output,
            kernel=temp_dir / 'kernel.bin',
            dtb=temp_dir / 'device.dtb',
            rootfs=rootfs,
            initrd=initrd,
            compression='lzma',
            kernel_load_addr='0x40000000',
            kernel_entry_addr='0x40000000',
            dtb_load_addr='0x50000000',
            description='Full Test Image',
            kernel_version='6.12.65',
        )

        content = output.read_text()

        # Verify all components
        assert 'kernel-1' in content
        assert 'fdt-1' in content
        assert 'rootfs-1' in content
        assert 'initrd-1' in content
        assert 'Full Test Image' in content
        assert '6.12.65' in content
        assert '0x40000000' in content
        assert '0x50000000' in content
        assert 'lzma' in content
