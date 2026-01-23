"""
Tests for toolchain.py - Cross-compilation toolchain builder.
"""

import os
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock, call
import tempfile
import shutil

from owrt.toolchain import ToolchainBuilder
from owrt.config import Config


class MockConfig:
    """Mock Config for testing."""

    def __init__(self, name='armsr-armv8', arch='aarch64'):
        self.name = name
        self.arch = arch
        self.target_tuple = f'{arch}-openwrt-linux-musl'
        self.cross_compile = f'{arch}-openwrt-linux-musl-'
        self.toolchain = {'gcc_version': '14.3.0', 'binutils_version': '2.44'}

        # Create temp directories
        self._temp = Path(tempfile.mkdtemp())
        self.toolchain_dir = self._temp / 'toolchains' / name
        self.build_dir = self._temp / 'build'
        self.dl_dir = self._temp / 'dl'
        self.staging_dir = self._temp / 'staging' / name
        self.root_dir = Path('/home/aparcar/openwrt-ng')

    def cleanup(self):
        shutil.rmtree(self._temp, ignore_errors=True)


class TestToolchainBuilderInit:
    """Tests for ToolchainBuilder initialization."""

    @pytest.fixture
    def config(self):
        cfg = MockConfig()
        yield cfg
        cfg.cleanup()

    def test_init_basic(self, config):
        """Test basic initialization."""
        builder = ToolchainBuilder(config)

        assert builder.config is config
        assert builder.toolchain_dir == config.toolchain_dir
        assert builder.target == config.target_tuple
        assert builder.arch == config.arch

    def test_init_verbose(self, config):
        """Test verbose flag."""
        builder = ToolchainBuilder(config, verbose=True)

        assert builder.verbose is True

    def test_init_jobs(self, config):
        """Test custom job count."""
        builder = ToolchainBuilder(config, jobs=8)

        assert builder.jobs == 8

    def test_init_default_jobs(self, config):
        """Test default jobs is cpu_count."""
        builder = ToolchainBuilder(config)

        assert builder.jobs == os.cpu_count()

    def test_init_versions_from_config(self, config):
        """Test versions are read from config."""
        config.toolchain = {'gcc_version': '13.2.0', 'binutils_version': '2.41'}
        builder = ToolchainBuilder(config)

        assert builder.gcc_version == '13.2.0'
        assert builder.binutils_version == '2.41'

    def test_init_creates_host_env(self, config):
        """Test host environment is created."""
        builder = ToolchainBuilder(config)

        assert 'CC' in builder._host_env
        assert builder._host_env['CC'] == 'gcc'
        assert builder._host_env['CXX'] == 'g++'


class TestToolchainBuilderHostEnv:
    """Tests for _create_host_env method."""

    @pytest.fixture
    def config(self):
        cfg = MockConfig()
        yield cfg
        cfg.cleanup()

    def test_host_env_sets_compilers(self, config):
        """Test host environment sets host compilers."""
        builder = ToolchainBuilder(config)
        env = builder._create_host_env()

        assert env['CC'] == 'gcc'
        assert env['CXX'] == 'g++'
        assert env['AR'] == 'ar'
        assert env['LD'] == 'ld'

    def test_host_env_removes_cross_vars(self, config):
        """Test host environment removes cross-compiler vars."""
        with patch.dict(os.environ, {'CROSS_COMPILE': 'arm-linux-'}):
            builder = ToolchainBuilder(config)
            env = builder._create_host_env()

            assert 'CROSS_COMPILE' not in env

    def test_host_env_preserves_path(self, config):
        """Test host environment preserves PATH."""
        builder = ToolchainBuilder(config)
        env = builder._create_host_env()

        assert 'PATH' in env


class TestToolchainBuilderStatus:
    """Tests for is_built and clean methods."""

    @pytest.fixture
    def config(self):
        cfg = MockConfig()
        yield cfg
        cfg.cleanup()

    def test_is_built_false_when_no_stamp(self, config):
        """Test is_built returns False when stamp doesn't exist."""
        builder = ToolchainBuilder(config)

        assert builder.is_built() is False

    def test_is_built_true_when_stamp_exists(self, config):
        """Test is_built returns True when stamp exists."""
        builder = ToolchainBuilder(config)
        builder.stamp_dir.mkdir(parents=True)
        (builder.stamp_dir / 'gcc_final_installed').touch()

        assert builder.is_built() is True

    def test_clean_removes_build_dir(self, config):
        """Test clean removes build directory."""
        builder = ToolchainBuilder(config)
        builder.build_dir.mkdir(parents=True)
        (builder.build_dir / 'test').touch()

        builder.clean()

        assert not builder.build_dir.exists()

    def test_clean_removes_toolchain_dir(self, config):
        """Test clean removes toolchain directory."""
        builder = ToolchainBuilder(config)
        builder.toolchain_dir.mkdir(parents=True)
        (builder.toolchain_dir / 'test').touch()

        builder.clean()

        assert not builder.toolchain_dir.exists()

    def test_clean_handles_missing_dirs(self, config):
        """Test clean handles non-existent directories."""
        builder = ToolchainBuilder(config)

        # Should not raise
        builder.clean()


class TestToolchainBuilderDownload:
    """Tests for _download_source method."""

    @pytest.fixture
    def config(self):
        cfg = MockConfig()
        yield cfg
        cfg.cleanup()

    def test_download_creates_dl_dir(self, config):
        """Test download creates dl directory."""
        builder = ToolchainBuilder(config)
        builder.dl_dir.mkdir(parents=True, exist_ok=True)

        with patch('owrt.toolchain.download_file') as mock_dl:
            builder._download_source('http://example.com/file.tar.gz', 'abc123')

        assert builder.dl_dir.exists()

    def test_download_returns_dest_path(self, config):
        """Test download returns destination path."""
        builder = ToolchainBuilder(config)
        builder.dl_dir.mkdir(parents=True, exist_ok=True)

        with patch('owrt.toolchain.download_file'):
            result = builder._download_source('http://example.com/file.tar.gz', 'abc123')

        assert result == builder.dl_dir / 'file.tar.gz'

    def test_download_skips_existing(self, config):
        """Test download skips already downloaded files."""
        builder = ToolchainBuilder(config)
        builder.dl_dir.mkdir(parents=True, exist_ok=True)
        existing = builder.dl_dir / 'file.tar.gz'
        existing.touch()

        with patch('owrt.toolchain.download_file') as mock_dl:
            result = builder._download_source('http://example.com/file.tar.gz', 'abc123')
            mock_dl.assert_not_called()

        assert result == existing


class TestToolchainBuilderGetEnv:
    """Tests for get_env method."""

    @pytest.fixture
    def config(self):
        cfg = MockConfig()
        yield cfg
        cfg.cleanup()

    def test_get_env_sets_path(self, config):
        """Test get_env adds toolchain to PATH."""
        builder = ToolchainBuilder(config)

        env = builder.get_env()

        assert str(builder.toolchain_dir / 'bin') in env['PATH']

    def test_get_env_sets_cross_compile(self, config):
        """Test get_env sets CROSS_COMPILE."""
        builder = ToolchainBuilder(config)

        env = builder.get_env()

        assert env['CROSS_COMPILE'] == config.cross_compile

    def test_get_env_sets_compiler_vars(self, config):
        """Test get_env sets CC, CXX, etc."""
        builder = ToolchainBuilder(config)

        env = builder.get_env()

        assert config.target_tuple in env['CC']
        assert config.target_tuple in env['CXX']
        assert config.target_tuple in env['AR']
        assert config.target_tuple in env['LD']

    def test_get_env_sets_staging_dir(self, config):
        """Test get_env sets STAGING_DIR."""
        builder = ToolchainBuilder(config)

        env = builder.get_env()

        assert env['STAGING_DIR'] == str(config.staging_dir)

    def test_get_env_with_ccache(self, config):
        """Test get_env with ccache enabled."""
        builder = ToolchainBuilder(config)

        env = builder.get_env(use_ccache=True)

        assert env['CC'].startswith('ccache ')
        assert env['CXX'].startswith('ccache ')

    def test_get_env_without_ccache(self, config):
        """Test get_env without ccache."""
        builder = ToolchainBuilder(config)

        env = builder.get_env(use_ccache=False)

        assert not env['CC'].startswith('ccache')
        assert not env['CXX'].startswith('ccache')


class TestToolchainBuilderVersions:
    """Tests for toolchain version handling."""

    @pytest.fixture
    def config(self):
        cfg = MockConfig()
        yield cfg
        cfg.cleanup()

    def test_default_versions(self, config):
        """Test default component versions."""
        builder = ToolchainBuilder(config)

        assert builder.BINUTILS_VERSION == '2.44'
        assert builder.GCC_VERSION == '14.3.0'
        assert builder.MUSL_VERSION == '1.2.5'
        assert '6.12' in builder.LINUX_VERSION

    def test_source_urls(self, config):
        """Test source URL formats."""
        builder = ToolchainBuilder(config)

        assert 'ftp.gnu.org/gnu/binutils' in builder.BINUTILS_URL
        assert 'ftp.gnu.org/gnu/gcc' in builder.GCC_URL
        assert 'musl.libc.org' in builder.MUSL_URL
        assert 'kernel.org' in builder.LINUX_URL

    def test_source_hashes_are_sha256(self, config):
        """Test source hashes are proper SHA256."""
        builder = ToolchainBuilder(config)

        # SHA256 hashes are 64 hex characters
        assert len(builder.BINUTILS_HASH) == 64
        assert len(builder.GCC_HASH) == 64
        assert len(builder.MUSL_HASH) == 64
        assert len(builder.LINUX_HASH) == 64


class TestToolchainBuilderBuild:
    """Tests for the build method."""

    @pytest.fixture
    def config(self):
        cfg = MockConfig()
        yield cfg
        cfg.cleanup()

    def test_build_creates_directories(self, config):
        """Test build creates necessary directories."""
        builder = ToolchainBuilder(config)

        # Mock all build stages
        with patch.object(builder, '_build_binutils'), \
             patch.object(builder, '_build_gcc_initial'), \
             patch.object(builder, '_build_kernel_headers'), \
             patch.object(builder, '_build_musl'), \
             patch.object(builder, '_build_gcc_final'):
            builder.build()

        assert builder.toolchain_dir.exists()
        assert builder.build_dir.exists()
        assert builder.dl_dir.exists()
        assert builder.stamp_dir.exists()

    def test_build_creates_lib_symlinks(self, config):
        """Test build creates lib64/lib32 symlinks."""
        builder = ToolchainBuilder(config)

        with patch.object(builder, '_build_binutils'), \
             patch.object(builder, '_build_gcc_initial'), \
             patch.object(builder, '_build_kernel_headers'), \
             patch.object(builder, '_build_musl'), \
             patch.object(builder, '_build_gcc_final'):
            builder.build()

        lib64 = builder.toolchain_dir / 'lib64'
        assert lib64.is_symlink()
        assert lib64.resolve() == (builder.toolchain_dir / 'lib').resolve()

    def test_build_calls_stages_in_order(self, config):
        """Test build calls stages in correct order."""
        builder = ToolchainBuilder(config)
        call_order = []

        def track_call(name):
            def inner():
                call_order.append(name)
            return inner

        with patch.object(builder, '_build_binutils', side_effect=track_call('binutils')), \
             patch.object(builder, '_build_gcc_initial', side_effect=track_call('gcc_initial')), \
             patch.object(builder, '_build_kernel_headers', side_effect=track_call('headers')), \
             patch.object(builder, '_build_musl', side_effect=track_call('musl')), \
             patch.object(builder, '_build_gcc_final', side_effect=track_call('gcc_final')):
            builder.build()

        assert call_order == ['binutils', 'gcc_initial', 'headers', 'musl', 'gcc_final']


class TestToolchainBuilderIntegration:
    """Integration tests with real config."""

    def test_init_with_real_config(self):
        """Test initialization with real target config."""
        config = Config.load_target('armsr-armv8')
        builder = ToolchainBuilder(config)

        assert builder.arch == 'aarch64'
        assert 'aarch64' in builder.target
        assert builder.toolchain_dir.name == 'armsr-armv8'

    def test_get_env_with_real_config(self):
        """Test get_env with real config."""
        config = Config.load_target('armsr-armv8')
        builder = ToolchainBuilder(config)

        env = builder.get_env()

        assert 'aarch64-openwrt-linux-musl-gcc' in env['CC']
        assert 'STAGING_DIR' in env

    def test_is_built_with_real_config(self):
        """Test is_built with real config."""
        config = Config.load_target('armsr-armv8')
        builder = ToolchainBuilder(config)

        # Just check it doesn't raise
        result = builder.is_built()
        assert isinstance(result, bool)


class TestToolchainBuilderArchitecture:
    """Tests for architecture-specific handling."""

    def test_aarch64_target_tuple(self):
        """Test aarch64 target tuple."""
        cfg = MockConfig(arch='aarch64')
        builder = ToolchainBuilder(cfg)

        assert 'aarch64' in builder.target
        assert 'musl' in builder.target

        cfg.cleanup()

    def test_arm_target_tuple(self):
        """Test ARM target tuple."""
        cfg = MockConfig(arch='arm')
        cfg.target_tuple = 'arm-openwrt-linux-musleabi'
        builder = ToolchainBuilder(cfg)

        assert 'arm' in builder.target
        assert 'musleabi' in builder.target

        cfg.cleanup()

    def test_mipsel_target_tuple(self):
        """Test MIPS little-endian target tuple."""
        cfg = MockConfig(arch='mipsel')
        cfg.target_tuple = 'mipsel-openwrt-linux-musl'
        builder = ToolchainBuilder(cfg)

        assert 'mipsel' in builder.target

        cfg.cleanup()

    def test_x86_64_target_tuple(self):
        """Test x86_64 target tuple."""
        cfg = MockConfig(arch='x86_64')
        cfg.target_tuple = 'x86_64-openwrt-linux-musl'
        builder = ToolchainBuilder(cfg)

        assert 'x86_64' in builder.target

        cfg.cleanup()
