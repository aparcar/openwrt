"""
Tests for bootloader.py - ARM Trusted Firmware and U-Boot builder.
"""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock, PropertyMock
import tempfile

import yaml
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from owrt.bootloader import BootloaderBuilder


class TestBootloaderBuilder:
    """Tests for BootloaderBuilder class."""

    @pytest.fixture
    def mock_config(self, tmp_path):
        """Create a mock configuration object."""
        config = MagicMock()
        config.name = 'mediatek-filogic'
        config.build_dir = tmp_path / 'build'
        config.openwrt_dir = tmp_path
        config.poc_dir = tmp_path
        config.toolchain_dir = tmp_path / 'toolchain'
        config.cross_compile = 'aarch64-openwrt-linux-musl-'
        return config

    @pytest.fixture
    def tfa_package_yaml(self, tmp_path):
        """Create a mock TF-A package.yaml."""
        pkg_dir = tmp_path / 'package' / 'boot' / 'arm-trusted-firmware-mediatek'
        pkg_dir.mkdir(parents=True)

        pkg_data = {
            'name': 'arm-trusted-firmware-mediatek',
            'version': '2.10.0',
            'source': {
                'type': 'tarball',
                'url': 'https://example.com/tfa-${version}.tar.gz',
                'sha256': 'abc123',
            },
            'variants': {
                'mt7981-spim-nand-ubi-ddr4': {
                    'plat': 'mt7981',
                    'make_flags': ['PLAT=mt7981', 'DRAM_USE_DDR4=1'],
                },
                'mt7981-emmc-ddr3': {
                    'plat': 'mt7981',
                    'make_flags': ['PLAT=mt7981', 'BOOT_DEVICE=emmc'],
                },
            },
        }

        with open(pkg_dir / 'package.yaml', 'w') as f:
            yaml.dump(pkg_data, f)

        return pkg_dir

    @pytest.fixture
    def uboot_package_yaml(self, tmp_path):
        """Create a mock U-Boot package.yaml."""
        pkg_dir = tmp_path / 'package' / 'boot' / 'uboot-mediatek'
        pkg_dir.mkdir(parents=True)

        pkg_data = {
            'name': 'uboot-mediatek',
            'version': '2024.07',
            'source': {
                'type': 'tarball',
                'url': 'https://example.com/uboot-${version}.tar.gz',
                'sha256': 'def456',
            },
            'build': {
                'config_disable': ['CMD_SAVEENV'],
            },
            'variants': {
                'mt7981_openwrt_one-snand': {
                    'defconfig': 'mt7981_openwrt_one_defconfig',
                    'tfa_variant': 'mt7981-spim-nand-ubi-ddr4',
                    'fip': {
                        'compress': True,
                    },
                },
            },
        }

        with open(pkg_dir / 'package.yaml', 'w') as f:
            yaml.dump(pkg_data, f)

        return pkg_dir

    def test_init(self, mock_config):
        """Test BootloaderBuilder initialization."""
        builder = BootloaderBuilder(mock_config)

        assert builder.config == mock_config
        assert builder.verbose is False
        assert builder.build_base == mock_config.build_dir / 'bootloader' / mock_config.name

    def test_init_verbose(self, mock_config):
        """Test BootloaderBuilder initialization with verbose flag."""
        builder = BootloaderBuilder(mock_config, verbose=True)
        assert builder.verbose is True

    def test_paths(self, mock_config):
        """Test that paths are set correctly."""
        builder = BootloaderBuilder(mock_config)

        assert builder.staging_dir == mock_config.build_dir / 'bootloader-staging'
        assert builder.dl_dir == mock_config.build_dir / 'dl'
        assert builder.host_staging == mock_config.build_dir / 'host-staging'
        assert builder.boot_packages_dir == mock_config.openwrt_dir / 'package' / 'boot'

    def test_build_tfa_missing_package(self, mock_config):
        """Test build_tfa raises when package.yaml is missing."""
        builder = BootloaderBuilder(mock_config)

        with pytest.raises(FileNotFoundError, match="TF-A package definition not found"):
            builder.build_tfa('mt7981-test')

    def test_build_tfa_unknown_variant(self, mock_config, tfa_package_yaml):
        """Test build_tfa raises for unknown variant."""
        builder = BootloaderBuilder(mock_config)

        with pytest.raises(ValueError, match="Unknown TF-A variant"):
            builder.build_tfa('unknown-variant')

    def test_build_uboot_missing_package(self, mock_config):
        """Test build_uboot raises when package.yaml is missing."""
        builder = BootloaderBuilder(mock_config)

        with pytest.raises(FileNotFoundError, match="U-Boot package definition not found"):
            builder.build_uboot('mt7981_openwrt_one-snand')

    def test_build_uboot_unknown_variant(self, mock_config, uboot_package_yaml):
        """Test build_uboot raises for unknown variant."""
        builder = BootloaderBuilder(mock_config)

        with pytest.raises(ValueError, match="Unknown U-Boot variant"):
            builder.build_uboot('unknown-variant')

    def test_bl2_to_tfa_variant(self, mock_config):
        """Test BL2 to TFA variant mapping."""
        builder = BootloaderBuilder(mock_config)

        profile = {
            'hardware': {
                'soc': 'MT7981B',
            },
        }

        result = builder._bl2_to_tfa_variant('spim-nand-ubi-ddr4', profile)
        assert result == 'mt7981-spim-nand-ubi-ddr4'

    def test_bl2_to_tfa_variant_missing_soc(self, mock_config):
        """Test BL2 to TFA variant with missing SoC."""
        builder = BootloaderBuilder(mock_config)

        profile = {'hardware': {}}
        result = builder._bl2_to_tfa_variant('test', profile)
        assert result is None

    def test_fip_to_uboot_variant(self, mock_config):
        """Test FIP to U-Boot variant mapping."""
        builder = BootloaderBuilder(mock_config)

        assert builder._fip_to_uboot_variant('openwrt_one-snand') == 'mt7981_openwrt_one-snand'
        assert builder._fip_to_uboot_variant('openwrt_one-nor') == 'mt7981_openwrt_one-nor'
        # Unknown variants pass through
        assert builder._fip_to_uboot_variant('custom') == 'custom'

    def test_get_bl2_path_exists(self, mock_config):
        """Test get_bl2_path when file exists."""
        builder = BootloaderBuilder(mock_config)
        builder.staging_dir.mkdir(parents=True)

        # Create a mock BL2 file
        bl2_path = builder.staging_dir / 'test-variant-bl2.img'
        bl2_path.write_bytes(b'test')

        result = builder.get_bl2_path('test-variant')
        assert result == bl2_path

    def test_get_bl2_path_not_exists(self, mock_config):
        """Test get_bl2_path when file doesn't exist."""
        builder = BootloaderBuilder(mock_config)

        result = builder.get_bl2_path('nonexistent')
        assert result is None

    def test_get_bl31_path_exists(self, mock_config):
        """Test get_bl31_path when file exists."""
        builder = BootloaderBuilder(mock_config)
        builder.staging_dir.mkdir(parents=True)

        bl31_path = builder.staging_dir / 'test-variant-bl31.bin'
        bl31_path.write_bytes(b'test')

        result = builder.get_bl31_path('test-variant')
        assert result == bl31_path

    def test_get_bl31_path_not_exists(self, mock_config):
        """Test get_bl31_path when file doesn't exist."""
        builder = BootloaderBuilder(mock_config)

        result = builder.get_bl31_path('nonexistent')
        assert result is None

    def test_get_fip_path_exists(self, mock_config):
        """Test get_fip_path when file exists."""
        builder = BootloaderBuilder(mock_config)
        builder.staging_dir.mkdir(parents=True)

        fip_path = builder.staging_dir / 'test-variant-u-boot.fip'
        fip_path.write_bytes(b'test')

        result = builder.get_fip_path('test-variant')
        assert result == fip_path

    def test_get_fip_path_not_exists(self, mock_config):
        """Test get_fip_path when file doesn't exist."""
        builder = BootloaderBuilder(mock_config)

        result = builder.get_fip_path('nonexistent')
        assert result is None

    def test_build_for_profile_empty(self, mock_config):
        """Test build_for_profile with no artifacts."""
        builder = BootloaderBuilder(mock_config)

        profile = {'name': 'test', 'artifacts': []}
        result = builder.build_for_profile(profile)
        assert result == {}

    @patch.object(BootloaderBuilder, 'build_tfa')
    def test_build_for_profile_bl2(self, mock_build_tfa, mock_config):
        """Test build_for_profile with BL2 artifact."""
        mock_build_tfa.return_value = {'bl2': Path('/path/to/bl2.img')}
        builder = BootloaderBuilder(mock_config)

        profile = {
            'name': 'test',
            'hardware': {'soc': 'MT7981B'},
            'artifacts': [
                {'type': 'bl2', 'variant': 'spim-nand-ubi-ddr4'},
            ],
        }

        result = builder.build_for_profile(profile)
        mock_build_tfa.assert_called_once_with('mt7981-spim-nand-ubi-ddr4')
        assert 'tfa-mt7981-spim-nand-ubi-ddr4' in result

    @patch.object(BootloaderBuilder, 'build_uboot')
    def test_build_for_profile_fip(self, mock_build_uboot, mock_config):
        """Test build_for_profile with FIP artifact."""
        mock_build_uboot.return_value = {'fip': Path('/path/to/fip')}
        builder = BootloaderBuilder(mock_config)

        profile = {
            'name': 'test',
            'hardware': {},
            'artifacts': [
                {'type': 'fip', 'variant': 'openwrt_one-snand'},
            ],
        }

        result = builder.build_for_profile(profile)
        mock_build_uboot.assert_called_once_with('mt7981_openwrt_one-snand')
        assert 'uboot-mt7981_openwrt_one-snand' in result

    @patch.object(BootloaderBuilder, 'build_tfa')
    def test_build_for_profile_handles_errors(self, mock_build_tfa, mock_config, capsys):
        """Test build_for_profile continues on errors."""
        mock_build_tfa.side_effect = RuntimeError("Build failed")
        builder = BootloaderBuilder(mock_config)

        profile = {
            'name': 'test',
            'hardware': {'soc': 'MT7981'},
            'artifacts': [
                {'type': 'bl2', 'variant': 'test'},
            ],
        }

        # Should not raise, but warn
        result = builder.build_for_profile(profile)
        assert result == {}
        captured = capsys.readouterr()
        assert 'Warning' in captured.out or 'Failed' in captured.out


class TestFetchSource:
    """Tests for _fetch_source method."""

    @pytest.fixture
    def builder(self, tmp_path):
        """Create a BootloaderBuilder with mock config."""
        config = MagicMock()
        config.name = 'test'
        config.build_dir = tmp_path / 'build'
        config.openwrt_dir = tmp_path
        config.poc_dir = tmp_path
        config.toolchain_dir = tmp_path / 'toolchain'
        return BootloaderBuilder(config)

    def test_fetch_source_skip_existing(self, builder, tmp_path):
        """Test that existing source directory is not re-fetched."""
        dest_dir = tmp_path / 'source'
        dest_dir.mkdir()
        (dest_dir / 'existing_file').write_text('content')

        source = {'type': 'tarball', 'url': 'https://example.com/test.tar.gz'}

        # Should return early without error (no network call)
        builder._fetch_source(source, dest_dir, 'v1.0')
        # Directory should still contain the existing file
        assert (dest_dir / 'existing_file').exists()
