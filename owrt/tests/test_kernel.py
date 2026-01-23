"""
Tests for kernel.py - Linux kernel builder.
"""

import os
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
import tempfile
import shutil

from owrt.kernel import KernelBuilder
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
            'source_hash': 'abc123',
        }
        self.image = {'kernel_name': 'Image'}
        self.default_packages = []
        self.poc_dir = Path('/home/aparcar/openwrt-ng/poc')
        self.openwrt_dir = Path('/home/aparcar/openwrt-ng')

        # Create temp directories
        self._temp = Path(tempfile.mkdtemp())
        self.kernel_build_dir = self._temp / 'kernel-build' / name
        self.build_dir = self._temp / 'build'
        self.dl_dir = self._temp / 'dl'
        self.staging_dir = self._temp / 'staging' / name

    def get_kernel_patch_dirs(self):
        return []

    def get_kernel_config_files(self):
        return []

    def get_dts_dir(self):
        return None

    def get_profile(self, name):
        raise ValueError(f"Profile {name} not found")

    def cleanup(self):
        shutil.rmtree(self._temp, ignore_errors=True)


class TestKernelBuilderInit:
    """Tests for KernelBuilder initialization."""

    @pytest.fixture
    def config(self):
        cfg = MockConfig()
        yield cfg
        cfg.cleanup()

    def test_init_basic(self, config):
        """Test basic initialization."""
        with patch('owrt.kernel.ToolchainBuilder'), \
             patch('owrt.kernel.KmodRegistry'):
            builder = KernelBuilder(config)

        assert builder.config is config
        assert builder.version == '6.12'
        assert builder.full_version == '6.12.65'

    def test_init_verbose(self, config):
        """Test verbose flag."""
        with patch('owrt.kernel.ToolchainBuilder'), \
             patch('owrt.kernel.KmodRegistry'):
            builder = KernelBuilder(config, verbose=True)

        assert builder.verbose is True

    def test_init_jobs(self, config):
        """Test custom job count."""
        with patch('owrt.kernel.ToolchainBuilder'), \
             patch('owrt.kernel.KmodRegistry'):
            builder = KernelBuilder(config, jobs=8)

        assert builder.jobs == 8

    def test_init_default_jobs(self, config):
        """Test default jobs is cpu_count."""
        with patch('owrt.kernel.ToolchainBuilder'), \
             patch('owrt.kernel.KmodRegistry'):
            builder = KernelBuilder(config)

        assert builder.jobs == os.cpu_count()

    def test_init_ccache(self, config):
        """Test ccache flag."""
        with patch('owrt.kernel.ToolchainBuilder'), \
             patch('owrt.kernel.KmodRegistry'):
            builder = KernelBuilder(config, use_ccache=True)

        assert builder.use_ccache is True


class TestKernelBuilderArchitecture:
    """Tests for architecture mapping."""

    @pytest.fixture
    def config(self):
        cfg = MockConfig()
        yield cfg
        cfg.cleanup()

    def test_aarch64_maps_to_arm64(self, config):
        """Test aarch64 maps to arm64."""
        config.arch = 'aarch64'
        with patch('owrt.kernel.ToolchainBuilder'), \
             patch('owrt.kernel.KmodRegistry'):
            builder = KernelBuilder(config)

        assert builder.kernel_arch == 'arm64'

    def test_arm_maps_to_arm(self, config):
        """Test arm maps to arm."""
        config.arch = 'arm'
        with patch('owrt.kernel.ToolchainBuilder'), \
             patch('owrt.kernel.KmodRegistry'):
            builder = KernelBuilder(config)

        assert builder.kernel_arch == 'arm'

    def test_x86_64_maps_to_x86(self, config):
        """Test x86_64 maps to x86."""
        config.arch = 'x86_64'
        with patch('owrt.kernel.ToolchainBuilder'), \
             patch('owrt.kernel.KmodRegistry'):
            builder = KernelBuilder(config)

        assert builder.kernel_arch == 'x86'

    def test_mips_maps_to_mips(self, config):
        """Test mips maps to mips."""
        config.arch = 'mips'
        with patch('owrt.kernel.ToolchainBuilder'), \
             patch('owrt.kernel.KmodRegistry'):
            builder = KernelBuilder(config)

        assert builder.kernel_arch == 'mips'

    def test_mipsel_maps_to_mips(self, config):
        """Test mipsel maps to mips."""
        config.arch = 'mipsel'
        with patch('owrt.kernel.ToolchainBuilder'), \
             patch('owrt.kernel.KmodRegistry'):
            builder = KernelBuilder(config)

        assert builder.kernel_arch == 'mips'


class TestKernelBuilderStatus:
    """Tests for is_built and clean methods."""

    @pytest.fixture
    def config(self):
        cfg = MockConfig()
        yield cfg
        cfg.cleanup()

    def test_is_built_false_when_no_stamp(self, config):
        """Test is_built returns False when stamp doesn't exist."""
        with patch('owrt.kernel.ToolchainBuilder'), \
             patch('owrt.kernel.KmodRegistry'):
            builder = KernelBuilder(config)

        assert builder.is_built() is False

    def test_is_built_true_when_stamp_exists(self, config):
        """Test is_built returns True when stamp exists."""
        with patch('owrt.kernel.ToolchainBuilder'), \
             patch('owrt.kernel.KmodRegistry'):
            builder = KernelBuilder(config)
            builder.stamp_dir.mkdir(parents=True)
            (builder.stamp_dir / 'kernel_built').touch()

        assert builder.is_built() is True

    def test_clean_removes_build_dir(self, config):
        """Test clean removes build directory."""
        with patch('owrt.kernel.ToolchainBuilder'), \
             patch('owrt.kernel.KmodRegistry'):
            builder = KernelBuilder(config)
            builder.build_dir.mkdir(parents=True)
            (builder.build_dir / 'test').touch()

            builder.clean()

        assert not builder.build_dir.exists()

    def test_clean_handles_missing_dirs(self, config):
        """Test clean handles non-existent directories."""
        with patch('owrt.kernel.ToolchainBuilder'), \
             patch('owrt.kernel.KmodRegistry'):
            builder = KernelBuilder(config)

        # Should not raise
        builder.clean()


class TestKernelBuilderVermagic:
    """Tests for vermagic computation."""

    @pytest.fixture
    def config(self):
        cfg = MockConfig()
        yield cfg
        cfg.cleanup()

    def test_compute_vermagic(self, config):
        """Test vermagic computation."""
        with patch('owrt.kernel.ToolchainBuilder'), \
             patch('owrt.kernel.KmodRegistry'):
            builder = KernelBuilder(config)
            builder.build_dir.mkdir(parents=True)
            builder.src_dir.mkdir(parents=True)

            # Create a sample .config
            config_content = """CONFIG_A=y
CONFIG_B=m
# CONFIG_C is not set
CONFIG_D=y
"""
            (builder.src_dir / '.config').write_text(config_content)

            builder._compute_vermagic()

        vermagic_file = builder.build_dir / '.vermagic'
        assert vermagic_file.exists()
        vermagic = vermagic_file.read_text().strip()
        assert len(vermagic) == 32  # MD5 hash

    def test_get_vermagic_returns_computed(self, config):
        """Test get_vermagic returns computed value."""
        with patch('owrt.kernel.ToolchainBuilder'), \
             patch('owrt.kernel.KmodRegistry'):
            builder = KernelBuilder(config)
            builder.build_dir.mkdir(parents=True)

            # Create vermagic file
            (builder.build_dir / '.vermagic').write_text('abc123def456')

        result = builder.get_vermagic()
        assert result == 'abc123def456'

    def test_get_vermagic_returns_unknown(self, config):
        """Test get_vermagic returns 'unknown' when not computed."""
        with patch('owrt.kernel.ToolchainBuilder'), \
             patch('owrt.kernel.KmodRegistry'):
            builder = KernelBuilder(config)

        result = builder.get_vermagic()
        assert result == 'unknown'


class TestKernelBuilderKmodConfig:
    """Tests for kmod config generation."""

    @pytest.fixture
    def config(self):
        cfg = MockConfig()
        yield cfg
        cfg.cleanup()

    def test_generate_kmod_config_empty(self, config):
        """Test kmod config is None when no kmods requested."""
        with patch('owrt.kernel.ToolchainBuilder'), \
             patch('owrt.kernel.KmodRegistry') as mock_registry_cls:
            builder = KernelBuilder(config)
            config.default_packages = ['base-files', 'busybox']

            result = builder._generate_kmod_config()

        assert result is None

    def test_generate_kmod_config_with_kmods(self, config):
        """Test kmod config is generated for kmod packages."""
        with patch('owrt.kernel.ToolchainBuilder'), \
             patch('owrt.kernel.KmodRegistry') as mock_registry_cls:
            mock_registry = MagicMock()
            mock_registry.get_kconfig_for_modules.return_value = ['CONFIG_USB=y']
            mock_registry_cls.return_value = mock_registry

            builder = KernelBuilder(config)
            builder.build_dir.mkdir(parents=True)
            config.default_packages = ['kmod-usb-core']

            result = builder._generate_kmod_config()

        assert result is not None
        assert result.exists()
        content = result.read_text()
        assert 'CONFIG_USB=y' in content


class TestKernelBuilderUserConfig:
    """Tests for user config generation."""

    @pytest.fixture
    def config(self):
        cfg = MockConfig()
        yield cfg
        cfg.cleanup()

    def test_generate_user_config_empty(self, config):
        """Test user config is None when no overrides."""
        with patch('owrt.kernel.ToolchainBuilder'), \
             patch('owrt.kernel.KmodRegistry'):
            builder = KernelBuilder(config)

            result = builder._generate_user_config(None)

        assert result is None

    def test_generate_user_config_with_overrides(self, config):
        """Test user config is generated for overrides."""
        with patch('owrt.kernel.ToolchainBuilder'), \
             patch('owrt.kernel.KmodRegistry'):
            builder = KernelBuilder(config)
            builder.build_dir.mkdir(parents=True)

            overrides = {'CONFIG_IPV6': 'y', 'CONFIG_DEBUG': 'n'}
            result = builder._generate_user_config(overrides)

        assert result is not None
        assert result.exists()
        content = result.read_text()
        assert 'CONFIG_IPV6=y' in content
        assert '# CONFIG_DEBUG is not set' in content


class TestKernelBuilderConfigHash:
    """Tests for config hash computation."""

    @pytest.fixture
    def config(self):
        cfg = MockConfig()
        yield cfg
        cfg.cleanup()

    def test_compute_config_hash(self, config):
        """Test config hash computation."""
        with patch('owrt.kernel.ToolchainBuilder'), \
             patch('owrt.kernel.KmodRegistry'):
            builder = KernelBuilder(config)

            hash1 = builder._compute_config_hash(None, None)
            hash2 = builder._compute_config_hash('profile1', None)
            hash3 = builder._compute_config_hash(None, {'CONFIG_X': 'y'})

        # Different inputs should produce different hashes
        assert hash1 != hash2
        assert hash1 != hash3
        assert hash2 != hash3

    def test_config_hash_is_deterministic(self, config):
        """Test config hash is deterministic."""
        with patch('owrt.kernel.ToolchainBuilder'), \
             patch('owrt.kernel.KmodRegistry'):
            builder = KernelBuilder(config)

            hash1 = builder._compute_config_hash('profile1', {'A': 'y'})
            hash2 = builder._compute_config_hash('profile1', {'A': 'y'})

        assert hash1 == hash2


class TestKernelBuilderPaths:
    """Tests for path methods."""

    @pytest.fixture
    def config(self):
        cfg = MockConfig()
        yield cfg
        cfg.cleanup()

    def test_get_kernel_path(self, config):
        """Test kernel path."""
        with patch('owrt.kernel.ToolchainBuilder'), \
             patch('owrt.kernel.KmodRegistry'):
            builder = KernelBuilder(config)

        path = builder.get_kernel_path()
        assert path == builder.output_dir / 'kernel.bin'

    def test_get_modules_dir(self, config):
        """Test modules directory path."""
        with patch('owrt.kernel.ToolchainBuilder'), \
             patch('owrt.kernel.KmodRegistry'):
            builder = KernelBuilder(config)

        path = builder.get_modules_dir()
        assert path == builder.output_dir / 'modules'

    def test_get_dtbs_dir(self, config):
        """Test DTBs directory path."""
        with patch('owrt.kernel.ToolchainBuilder'), \
             patch('owrt.kernel.KmodRegistry'):
            builder = KernelBuilder(config)

        path = builder.get_dtbs_dir()
        assert path == builder.output_dir / 'dtbs'

    def test_get_dtbos_dir(self, config):
        """Test DTBOs directory path."""
        with patch('owrt.kernel.ToolchainBuilder'), \
             patch('owrt.kernel.KmodRegistry'):
            builder = KernelBuilder(config)

        path = builder.get_dtbos_dir()
        assert path == builder.output_dir / 'dtbos'

    def test_get_initramfs_kernel_path(self, config):
        """Test initramfs kernel path."""
        with patch('owrt.kernel.ToolchainBuilder'), \
             patch('owrt.kernel.KmodRegistry'):
            builder = KernelBuilder(config)

        path = builder.get_initramfs_kernel_path()
        assert path == builder.output_dir / 'initramfs-kernel.bin'


class TestKernelBuilderBuildEnv:
    """Tests for build environment."""

    @pytest.fixture
    def config(self):
        cfg = MockConfig()
        yield cfg
        cfg.cleanup()

    def test_get_build_env_sets_kbuild_vars(self, config):
        """Test build env sets KBUILD variables."""
        with patch('owrt.kernel.ToolchainBuilder') as mock_tc_cls:
            mock_tc = MagicMock()
            mock_tc.get_env.return_value = {}
            mock_tc_cls.return_value = mock_tc

            with patch('owrt.kernel.KmodRegistry'):
                builder = KernelBuilder(config)
                env = builder._get_build_env()

        assert env['KBUILD_BUILD_HOST'] == 'openwrt'
        assert env['KBUILD_BUILD_USER'] == 'builder'
        assert env['KBUILD_BUILD_TIMESTAMP'] == '@0'

    def test_get_build_env_uses_toolchain(self, config):
        """Test build env uses toolchain environment."""
        with patch('owrt.kernel.ToolchainBuilder') as mock_tc_cls:
            mock_tc = MagicMock()
            mock_tc.get_env.return_value = {'CC': 'aarch64-gcc'}
            mock_tc_cls.return_value = mock_tc

            with patch('owrt.kernel.KmodRegistry'):
                builder = KernelBuilder(config)
                env = builder._get_build_env()

        assert env['CC'] == 'aarch64-gcc'


class TestKernelBuilderIntegration:
    """Integration tests with real config."""

    def test_init_with_real_config(self):
        """Test initialization with real target config."""
        config = Config.load_target('armsr-armv8')

        with patch('owrt.kernel.ToolchainBuilder'), \
             patch('owrt.kernel.KmodRegistry'):
            builder = KernelBuilder(config)

        assert builder.kernel_arch == 'arm64'
        assert '6.12' in builder.version

    def test_is_built_with_real_config(self):
        """Test is_built with real config."""
        config = Config.load_target('armsr-armv8')

        with patch('owrt.kernel.ToolchainBuilder'), \
             patch('owrt.kernel.KmodRegistry'):
            builder = KernelBuilder(config)

        # Just check it doesn't raise
        result = builder.is_built()
        assert isinstance(result, bool)


class TestKernelBuilderKernelUrl:
    """Tests for kernel URL construction."""

    @pytest.fixture
    def config(self):
        cfg = MockConfig()
        yield cfg
        cfg.cleanup()

    def test_kernel_url_base(self, config):
        """Test kernel URL base."""
        with patch('owrt.kernel.ToolchainBuilder'), \
             patch('owrt.kernel.KmodRegistry'):
            builder = KernelBuilder(config)

        assert 'kernel.org' in builder.KERNEL_URL_BASE
