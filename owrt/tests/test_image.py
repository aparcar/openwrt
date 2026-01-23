"""
Tests for image.py - Firmware image builder.
"""

import os
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock, PropertyMock
import tempfile
import shutil

from owrt.image import ImageBuilder
from owrt.config import Config


class MockConfig:
    """Mock Config for testing."""

    def __init__(self, name='armsr-armv8', arch='aarch64'):
        self.name = name
        self.arch = arch
        self.target_tuple = f'{arch}-openwrt-linux-musl'
        self.cross_compile = f'{arch}-openwrt-linux-musl-'
        self.board = 'armsr'
        self.subtarget = 'armv8'
        self.kernel = {
            'version': '6.12',
            'full_version': '6.12.65',
            'load_address': '0x44000000',
        }
        self.image = {'kernel_name': 'Image', 'cmdline': 'console=ttyAMA0'}
        self.default_packages = ['base-files', 'busybox']
        self.openwrt_dir = Path('/home/aparcar/openwrt-ng')

        # Create temp directories
        self._temp = Path(tempfile.mkdtemp())
        self.rootfs_dir = self._temp / 'rootfs' / name
        self.images_dir = self._temp / 'images' / name
        self.build_dir = self._temp / 'build'
        self.staging_dir = self._temp / 'staging' / name

    def get_profile(self, name):
        return {
            'name': name,
            'packages': [],
            'images': [],
            'artifacts': [],
        }

    def get_profile_packages(self, name):
        return self.default_packages

    def cleanup(self):
        shutil.rmtree(self._temp, ignore_errors=True)


class TestImageBuilderInit:
    """Tests for ImageBuilder initialization."""

    @pytest.fixture
    def config(self):
        cfg = MockConfig()
        yield cfg
        cfg.cleanup()

    def test_init_basic(self, config):
        """Test basic initialization."""
        with patch('owrt.image.KernelBuilder'), \
             patch('owrt.image.PackageBuilder'), \
             patch('owrt.image.FITBuilder'), \
             patch('owrt.image.MetadataBuilder'), \
             patch('owrt.image.BootloaderBuilder'):
            builder = ImageBuilder(config)

        assert builder.config is config
        assert builder.rootfs_dir == config.rootfs_dir
        assert builder.images_dir == config.images_dir

    def test_init_verbose(self, config):
        """Test verbose flag."""
        with patch('owrt.image.KernelBuilder'), \
             patch('owrt.image.PackageBuilder'), \
             patch('owrt.image.FITBuilder'), \
             patch('owrt.image.MetadataBuilder'), \
             patch('owrt.image.BootloaderBuilder'):
            builder = ImageBuilder(config, verbose=True)

        assert builder.verbose is True


class TestImageBuilderNaming:
    """Tests for image naming methods."""

    @pytest.fixture
    def config(self):
        cfg = MockConfig()
        yield cfg
        cfg.cleanup()

    def test_get_image_prefix(self, config):
        """Test image prefix generation."""
        with patch('owrt.image.KernelBuilder'), \
             patch('owrt.image.PackageBuilder'), \
             patch('owrt.image.FITBuilder'), \
             patch('owrt.image.MetadataBuilder'), \
             patch('owrt.image.BootloaderBuilder'):
            builder = ImageBuilder(config)

        prefix = builder._get_image_prefix()

        assert 'openwrt' in prefix
        assert 'armsr' in prefix
        assert 'armv8' in prefix

    def test_get_device_image_prefix(self, config):
        """Test device image prefix generation."""
        with patch('owrt.image.KernelBuilder'), \
             patch('owrt.image.PackageBuilder'), \
             patch('owrt.image.FITBuilder'), \
             patch('owrt.image.MetadataBuilder'), \
             patch('owrt.image.BootloaderBuilder'):
            builder = ImageBuilder(config)

        prefix = builder._get_device_image_prefix('generic')

        assert 'generic' in prefix
        assert 'armsr' in prefix

    def test_get_image_name(self, config):
        """Test full image name generation."""
        with patch('owrt.image.KernelBuilder'), \
             patch('owrt.image.PackageBuilder'), \
             patch('owrt.image.FITBuilder'), \
             patch('owrt.image.MetadataBuilder'), \
             patch('owrt.image.BootloaderBuilder'):
            builder = ImageBuilder(config)

        name = builder._get_image_name('generic', 'sysupgrade', 'itb')

        assert 'generic' in name
        assert 'sysupgrade' in name
        assert name.endswith('.itb')


class TestImageBuilderSizeParser:
    """Tests for size parsing."""

    @pytest.fixture
    def config(self):
        cfg = MockConfig()
        yield cfg
        cfg.cleanup()

    def test_parse_size_mb_megabytes(self, config):
        """Test parsing megabyte size."""
        with patch('owrt.image.KernelBuilder'), \
             patch('owrt.image.PackageBuilder'), \
             patch('owrt.image.FITBuilder'), \
             patch('owrt.image.MetadataBuilder'), \
             patch('owrt.image.BootloaderBuilder'):
            builder = ImageBuilder(config)

        assert builder._parse_size_mb('512M') == 512
        assert builder._parse_size_mb('256m') == 256

    def test_parse_size_mb_gigabytes(self, config):
        """Test parsing gigabyte size."""
        with patch('owrt.image.KernelBuilder'), \
             patch('owrt.image.PackageBuilder'), \
             patch('owrt.image.FITBuilder'), \
             patch('owrt.image.MetadataBuilder'), \
             patch('owrt.image.BootloaderBuilder'):
            builder = ImageBuilder(config)

        assert builder._parse_size_mb('1G') == 1024
        assert builder._parse_size_mb('2g') == 2048

    def test_parse_size_mb_kilobytes(self, config):
        """Test parsing kilobyte size."""
        with patch('owrt.image.KernelBuilder'), \
             patch('owrt.image.PackageBuilder'), \
             patch('owrt.image.FITBuilder'), \
             patch('owrt.image.MetadataBuilder'), \
             patch('owrt.image.BootloaderBuilder'):
            builder = ImageBuilder(config)

        assert builder._parse_size_mb('1024K') == 1
        assert builder._parse_size_mb('2048k') == 2


class TestImageBuilderSymlinks:
    """Tests for symlink creation."""

    @pytest.fixture
    def config(self):
        cfg = MockConfig()
        yield cfg
        cfg.cleanup()

    def test_create_symlinks(self, config):
        """Test symlink creation."""
        with patch('owrt.image.KernelBuilder'), \
             patch('owrt.image.PackageBuilder'), \
             patch('owrt.image.FITBuilder'), \
             patch('owrt.image.MetadataBuilder'), \
             patch('owrt.image.BootloaderBuilder'):
            builder = ImageBuilder(config)
            builder.rootfs_dir.mkdir(parents=True)
            (builder.rootfs_dir / 'bin').mkdir()
            (builder.rootfs_dir / 'bin' / 'busybox').touch()
            (builder.rootfs_dir / 'sbin').mkdir()

            builder._create_symlinks()

        # Check /init symlink exists
        init_link = builder.rootfs_dir / 'init'
        assert init_link.is_symlink()


class TestImageBuilderPermissions:
    """Tests for permission setting."""

    @pytest.fixture
    def config(self):
        cfg = MockConfig()
        yield cfg
        cfg.cleanup()

    def test_set_permissions(self, config):
        """Test permission setting."""
        with patch('owrt.image.KernelBuilder'), \
             patch('owrt.image.PackageBuilder'), \
             patch('owrt.image.FITBuilder'), \
             patch('owrt.image.MetadataBuilder'), \
             patch('owrt.image.BootloaderBuilder'):
            builder = ImageBuilder(config)
            builder.rootfs_dir.mkdir(parents=True)
            (builder.rootfs_dir / 'root').mkdir()
            (builder.rootfs_dir / 'tmp').mkdir()

            builder._set_permissions()

        # Check /root is 700
        root_mode = (builder.rootfs_dir / 'root').stat().st_mode & 0o777
        assert root_mode == 0o700


class TestImageBuilderReleaseFiles:
    """Tests for release file generation."""

    @pytest.fixture
    def config(self):
        cfg = MockConfig()
        yield cfg
        cfg.cleanup()

    def test_generate_release_files(self, config):
        """Test release file generation."""
        with patch('owrt.image.KernelBuilder'), \
             patch('owrt.image.PackageBuilder'), \
             patch('owrt.image.FITBuilder'), \
             patch('owrt.image.MetadataBuilder'), \
             patch('owrt.image.BootloaderBuilder'):
            builder = ImageBuilder(config)
            builder.rootfs_dir.mkdir(parents=True)
            (builder.rootfs_dir / 'etc').mkdir()
            (builder.rootfs_dir / 'usr' / 'lib').mkdir(parents=True)

            builder._generate_release_files()

        # Check files exist
        assert (builder.rootfs_dir / 'etc' / 'openwrt_release').exists()
        assert (builder.rootfs_dir / 'etc' / 'openwrt_version').exists()
        assert (builder.rootfs_dir / 'usr' / 'lib' / 'os-release').exists()

    def test_release_file_content(self, config):
        """Test release file content."""
        with patch('owrt.image.KernelBuilder'), \
             patch('owrt.image.PackageBuilder'), \
             patch('owrt.image.FITBuilder'), \
             patch('owrt.image.MetadataBuilder'), \
             patch('owrt.image.BootloaderBuilder'):
            builder = ImageBuilder(config)
            builder.rootfs_dir.mkdir(parents=True)
            (builder.rootfs_dir / 'etc').mkdir()
            (builder.rootfs_dir / 'usr' / 'lib').mkdir(parents=True)

            builder._generate_release_files()

        content = (builder.rootfs_dir / 'etc' / 'openwrt_release').read_text()
        assert 'DISTRIB_ID' in content
        assert 'DISTRIB_TARGET' in content


class TestImageBuilderUsersGroups:
    """Tests for user/group creation."""

    @pytest.fixture
    def config(self):
        cfg = MockConfig()
        yield cfg
        cfg.cleanup()

    def test_create_users_and_groups_empty(self, config):
        """Test with no packages having userid."""
        with patch('owrt.image.KernelBuilder'), \
             patch('owrt.image.PackageBuilder'), \
             patch('owrt.image.FITBuilder'), \
             patch('owrt.image.MetadataBuilder'), \
             patch('owrt.image.BootloaderBuilder'), \
             patch('owrt.config.PackageConfig.find_package', return_value=None):
            builder = ImageBuilder(config)
            builder.rootfs_dir.mkdir(parents=True)
            (builder.rootfs_dir / 'etc').mkdir()
            (builder.rootfs_dir / 'etc' / 'passwd').write_text('')
            (builder.rootfs_dir / 'etc' / 'group').write_text('')
            (builder.rootfs_dir / 'etc' / 'shadow').write_text('')

            # Should not raise
            builder._create_users_and_groups(['nonexistent'])


class TestImageBuilderCopyTree:
    """Tests for directory copy."""

    @pytest.fixture
    def config(self):
        cfg = MockConfig()
        yield cfg
        cfg.cleanup()

    def test_copy_tree(self, config):
        """Test directory tree copy."""
        with patch('owrt.image.KernelBuilder'), \
             patch('owrt.image.PackageBuilder'), \
             patch('owrt.image.FITBuilder'), \
             patch('owrt.image.MetadataBuilder'), \
             patch('owrt.image.BootloaderBuilder'):
            builder = ImageBuilder(config)

            # Create source tree
            src = config._temp / 'src'
            src.mkdir()
            (src / 'dir1').mkdir()
            (src / 'dir1' / 'file1.txt').write_text('content1')
            (src / 'file2.txt').write_text('content2')

            # Create dest
            dst = config._temp / 'dst'
            dst.mkdir()

            builder._copy_tree(src, dst)

        assert (dst / 'dir1' / 'file1.txt').exists()
        assert (dst / 'file2.txt').exists()
        assert (dst / 'dir1' / 'file1.txt').read_text() == 'content1'


class TestImageBuilderBuildRootfsImages:
    """Tests for rootfs image building."""

    @pytest.fixture
    def config(self):
        cfg = MockConfig()
        yield cfg
        cfg.cleanup()

    def test_build_rootfs_images_squashfs(self, config):
        """Test squashfs image building is called."""
        with patch('owrt.image.KernelBuilder'), \
             patch('owrt.image.PackageBuilder'), \
             patch('owrt.image.FITBuilder'), \
             patch('owrt.image.MetadataBuilder'), \
             patch('owrt.image.BootloaderBuilder'):
            builder = ImageBuilder(config)

        profile = {
            'images': [
                {'name': 'sysupgrade.itb', 'filesystem': 'squashfs'},
            ],
        }

        with patch.object(builder, '_build_squashfs') as mock_squashfs:
            mock_squashfs.return_value = Path('/tmp/rootfs.squashfs')
            result = builder._build_rootfs_images(profile)

        mock_squashfs.assert_called_once()
        assert 'squashfs' in result


class TestImageBuilderIntegration:
    """Integration tests with real config."""

    def test_init_with_real_config(self):
        """Test initialization with real target config."""
        config = Config.load_target('armsr-armv8')

        with patch('owrt.image.KernelBuilder'), \
             patch('owrt.image.PackageBuilder'), \
             patch('owrt.image.FITBuilder'), \
             patch('owrt.image.MetadataBuilder'), \
             patch('owrt.image.BootloaderBuilder'):
            builder = ImageBuilder(config)

        assert builder.config is config
        assert 'armsr' in str(builder.images_dir)

    def test_get_image_prefix_real_config(self):
        """Test image prefix with real config."""
        config = Config.load_target('armsr-armv8')

        with patch('owrt.image.KernelBuilder'), \
             patch('owrt.image.PackageBuilder'), \
             patch('owrt.image.FITBuilder'), \
             patch('owrt.image.MetadataBuilder'), \
             patch('owrt.image.BootloaderBuilder'):
            builder = ImageBuilder(config)

        prefix = builder._get_image_prefix()

        assert 'openwrt' in prefix
        assert 'armsr-armv8' in prefix


class TestImageBuilderVersionInfo:
    """Tests for version information."""

    @pytest.fixture
    def config(self):
        cfg = MockConfig()
        yield cfg
        cfg.cleanup()

    def test_version_constants(self, config):
        """Test version constants are set."""
        with patch('owrt.image.KernelBuilder'), \
             patch('owrt.image.PackageBuilder'), \
             patch('owrt.image.FITBuilder'), \
             patch('owrt.image.MetadataBuilder'), \
             patch('owrt.image.BootloaderBuilder'):
            builder = ImageBuilder(config)

        assert builder.VERSION_DIST == 'openwrt'
        assert builder.VERSION_NUMBER == 'SNAPSHOT'
