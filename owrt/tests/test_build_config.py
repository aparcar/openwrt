"""
Tests for build_config.py - BuildConfig, ImageConfig, BuildOptions.
"""

import pytest
import tempfile
from pathlib import Path

import yaml
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from owrt.build_config import BuildConfig, ImageConfig, BuildOptions, parse_size


class TestParseSize:
    """Tests for parse_size helper function."""

    def test_parse_size_bytes(self):
        """Test parsing plain bytes."""
        assert parse_size('1024') == 1024
        assert parse_size('0') == 0

    def test_parse_size_kilobytes(self):
        """Test parsing kilobytes."""
        assert parse_size('1K') == 1024
        assert parse_size('2k') == 2048
        assert parse_size('10K') == 10 * 1024

    def test_parse_size_megabytes(self):
        """Test parsing megabytes."""
        assert parse_size('1M') == 1024 * 1024
        assert parse_size('100m') == 100 * 1024 * 1024

    def test_parse_size_gigabytes(self):
        """Test parsing gigabytes."""
        assert parse_size('1G') == 1024 * 1024 * 1024
        assert parse_size('2g') == 2 * 1024 * 1024 * 1024

    def test_parse_size_empty(self):
        """Test parsing empty string."""
        assert parse_size('') == 0

    def test_parse_size_whitespace(self):
        """Test parsing with whitespace."""
        assert parse_size(' 1M ') == 1024 * 1024


class TestImageConfig:
    """Tests for ImageConfig dataclass."""

    def test_defaults(self):
        """Test default values."""
        config = ImageConfig()
        assert config.rootfs_size == 100 * 1024 * 1024
        assert config.kernel_size == 16 * 1024 * 1024
        assert config.compression == 'gzip'
        assert config.rootfs_type == 'squashfs'

    def test_from_dict_empty(self):
        """Test creating from empty dict uses defaults."""
        config = ImageConfig.from_dict({})
        assert config.compression == 'gzip'

    def test_from_dict_custom(self):
        """Test creating from dict with custom values."""
        config = ImageConfig.from_dict({
            'rootfs_size': '200M',
            'kernel_size': '8M',
            'compression': 'xz',
            'rootfs_type': 'ext4',
        })
        assert config.rootfs_size == 200 * 1024 * 1024
        assert config.kernel_size == 8 * 1024 * 1024
        assert config.compression == 'xz'
        assert config.rootfs_type == 'ext4'


class TestBuildOptions:
    """Tests for BuildOptions dataclass."""

    def test_defaults(self):
        """Test default values."""
        options = BuildOptions()
        assert options.jobs is None
        assert options.verbose is False
        assert options.continue_on_error is False
        assert options.dl_dir is None

    def test_from_dict_empty(self):
        """Test creating from empty dict uses defaults."""
        options = BuildOptions.from_dict({})
        assert options.verbose is False

    def test_from_dict_custom(self):
        """Test creating from dict with custom values."""
        options = BuildOptions.from_dict({
            'jobs': 8,
            'verbose': True,
            'continue_on_error': True,
            'dl_dir': '/tmp/downloads',
        })
        assert options.jobs == 8
        assert options.verbose is True
        assert options.continue_on_error is True
        assert options.dl_dir == Path('/tmp/downloads')


class TestBuildConfig:
    """Tests for BuildConfig class."""

    def test_from_args_minimal(self):
        """Test creating from command-line args."""
        config = BuildConfig.from_args(target='armsr-armv8')
        assert config.target_name == 'armsr-armv8'
        assert config.profile_name == 'generic'
        assert config.packages == []

    def test_from_args_with_options(self):
        """Test creating from args with all options."""
        config = BuildConfig.from_args(
            target='x86-64',
            profile='custom',
            packages=['luci', 'dropbear'],
            verbose=True,
        )
        assert config.target_name == 'x86-64'
        assert config.profile_name == 'custom'
        assert config.packages == ['luci', 'dropbear']
        assert config.build.verbose is True

    def test_load_from_file(self, tmp_path):
        """Test loading from YAML file."""
        config_file = tmp_path / 'config.yaml'
        config_file.write_text(yaml.dump({
            'target': 'armsr-armv8',
            'profile': 'generic',
            'packages': ['luci'],
            'kernel': {
                'IPV6': 'y',
            },
        }))

        config = BuildConfig.load(config_file)
        assert config.target_name == 'armsr-armv8'
        assert config.profile_name == 'generic'
        assert config.packages == ['luci']
        assert 'CONFIG_IPV6' in config.kernel_config

    def test_load_missing_target_raises(self, tmp_path):
        """Test that missing target raises ValueError."""
        config_file = tmp_path / 'config.yaml'
        config_file.write_text(yaml.dump({
            'profile': 'generic',
        }))

        with pytest.raises(ValueError, match="must specify 'target'"):
            BuildConfig.load(config_file)

    def test_load_not_found_raises(self, tmp_path):
        """Test that missing file raises FileNotFoundError."""
        # Change to tmp_path so no config.yaml is found
        import os
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            with pytest.raises(FileNotFoundError):
                BuildConfig.load()
        finally:
            os.chdir(old_cwd)

    def test_kernel_config_prefix(self, tmp_path):
        """Test that CONFIG_ prefix is normalized."""
        config_file = tmp_path / 'config.yaml'
        config_file.write_text(yaml.dump({
            'target': 'test',
            'kernel': {
                'IPV6': 'y',
                'CONFIG_MODULES': 'y',
            },
        }))

        config = BuildConfig.load(config_file)
        assert 'CONFIG_IPV6' in config.kernel_config
        assert 'CONFIG_MODULES' in config.kernel_config
        assert config.kernel_config['CONFIG_IPV6'] == 'y'

    def test_get_kernel_modules(self):
        """Test extracting kernel modules from package list."""
        config = BuildConfig.from_args(
            target='test',
            packages=['kmod-usb-core', 'luci', 'kmod-fs-ext4'],
        )
        # Mock load_target to avoid needing real target
        config._target_config = type('Config', (), {
            'default_packages': ['base-files', 'kmod-nf-conntrack'],
        })()

        kmods = config.get_kernel_modules()
        assert 'kmod-usb-core' in kmods
        assert 'kmod-fs-ext4' in kmods
        assert 'kmod-nf-conntrack' in kmods
        assert 'luci' not in kmods

    def test_get_kernel_config_fragment(self, tmp_path):
        """Test generating kernel config fragment."""
        config_file = tmp_path / 'config.yaml'
        config_file.write_text(yaml.dump({
            'target': 'test',
            'kernel': {
                'IPV6': 'y',
                'MODULES': 'n',
            },
        }))

        config = BuildConfig.load(config_file)
        fragment = config.get_kernel_config_fragment()

        assert 'CONFIG_IPV6=y' in fragment
        assert '# CONFIG_MODULES is not set' in fragment

    def test_save(self, tmp_path):
        """Test saving config to file."""
        config = BuildConfig.from_args(
            target='armsr-armv8',
            profile='custom',
            packages=['luci'],
        )
        config.kernel_config = {'CONFIG_IPV6': 'y'}

        output_file = tmp_path / 'saved.yaml'
        config.save(output_file)

        # Reload and verify
        with open(output_file) as f:
            data = yaml.safe_load(f)

        assert data['target'] == 'armsr-armv8'
        assert data['profile'] == 'custom'
        assert data['packages'] == ['luci']
        assert data['kernel']['IPV6'] == 'y'

    def test_get_source_override(self):
        """Test getting source overrides."""
        config = BuildConfig(
            target_name='test',
            source_overrides={
                'luci': {'type': 'git', 'url': 'https://example.com/luci.git'},
            },
        )

        override = config.get_source_override('luci')
        assert override is not None
        assert override['type'] == 'git'

        assert config.get_source_override('unknown') is None

    def test_str_representation(self):
        """Test string representation."""
        config = BuildConfig.from_args(target='armsr-armv8')
        config._target_config = type('Config', (), {
            'default_packages': ['base-files'],
        })()

        s = str(config)
        assert 'armsr-armv8' in s
        assert 'generic' in s

    def test_write_kernel_config_fragment(self, tmp_path):
        """Test writing kernel config fragment to file."""
        config = BuildConfig(
            target_name='test',
            kernel_config={'CONFIG_IPV6': 'y'},
        )

        output_file = tmp_path / 'kernel' / 'fragment.config'
        result = config.write_kernel_config_fragment(output_file)

        assert result == output_file
        assert output_file.exists()
        assert 'CONFIG_IPV6=y' in output_file.read_text()
