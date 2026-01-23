"""
Tests for package.py - Package builder.
"""

import os
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock, PropertyMock
import tempfile
import shutil

from owrt.package import PackageBuilder
from owrt.config import Config, PackageConfig


class MockConfig:
    """Mock Config for testing."""

    def __init__(self, name='armsr-armv8', arch='aarch64'):
        self.name = name
        self.arch = arch
        self.target_tuple = f'{arch}-openwrt-linux-musl'
        self.cross_compile = f'{arch}-openwrt-linux-musl-'
        self.toolchain = {'gcc_version': '14.3.0', 'libc': 'musl'}
        self.kernel = {'full_version': '6.12.65'}
        self.cpu = {'endian': 'little'}
        self.board = 'armsr'
        self.subtarget = 'armv8'
        self.openwrt_dir = Path('/home/aparcar/openwrt-ng')

        # Create temp directories
        self._temp = Path(tempfile.mkdtemp())
        self.packages_dir = self._temp / 'packages' / name
        self.staging_dir = self._temp / 'staging' / name
        self.build_dir = self._temp / 'build'
        self.dl_dir = self._temp / 'dl'
        self.root_dir = Path('/home/aparcar/openwrt-ng')

    def cleanup(self):
        shutil.rmtree(self._temp, ignore_errors=True)


class TestPackageBuilderInit:
    """Tests for PackageBuilder initialization."""

    @pytest.fixture
    def config(self):
        cfg = MockConfig()
        yield cfg
        cfg.cleanup()

    def test_init_basic(self, config):
        """Test basic initialization."""
        with patch('owrt.package.ToolchainBuilder'):
            builder = PackageBuilder(config)

        assert builder.config is config
        assert builder.packages_dir == config.packages_dir
        assert builder.staging_dir == config.staging_dir

    def test_init_verbose(self, config):
        """Test verbose flag."""
        with patch('owrt.package.ToolchainBuilder'):
            builder = PackageBuilder(config, verbose=True)

        assert builder.verbose is True

    def test_init_jobs(self, config):
        """Test custom job count."""
        with patch('owrt.package.ToolchainBuilder'):
            builder = PackageBuilder(config, jobs=8)

        assert builder.jobs == 8

    def test_init_default_jobs(self, config):
        """Test default jobs is cpu_count."""
        with patch('owrt.package.ToolchainBuilder'):
            builder = PackageBuilder(config)

        assert builder.jobs == os.cpu_count()

    def test_init_ccache(self, config):
        """Test ccache flag."""
        with patch('owrt.package.ToolchainBuilder'):
            builder = PackageBuilder(config, use_ccache=True)

        assert builder.use_ccache is True


class TestPackageBuilderPaths:
    """Tests for path-related methods."""

    @pytest.fixture
    def config(self):
        cfg = MockConfig()
        yield cfg
        cfg.cleanup()

    def test_get_package_staging_dir(self, config):
        """Test package staging directory path."""
        with patch('owrt.package.ToolchainBuilder'):
            builder = PackageBuilder(config)

        staging_dir = builder._get_package_staging_dir('busybox')

        assert staging_dir == config.packages_dir / 'busybox' / 'staging'

    def test_get_package_deps_dir(self, config):
        """Test package deps directory path."""
        with patch('owrt.package.ToolchainBuilder'):
            builder = PackageBuilder(config)

        deps_dir = builder._get_package_deps_dir('busybox')

        assert deps_dir == config.packages_dir / 'busybox' / 'deps'

    def test_apk_dir_is_arch_specific(self, config):
        """Test APK directory is architecture-specific."""
        with patch('owrt.package.ToolchainBuilder'):
            builder = PackageBuilder(config)

        assert config.arch in str(builder.apk_dir)

    def test_repo_dir_is_arch_specific(self, config):
        """Test repo directory is architecture-specific."""
        with patch('owrt.package.ToolchainBuilder'):
            builder = PackageBuilder(config)

        assert config.arch in str(builder.repo_dir)


class TestPackageBuilderClean:
    """Tests for clean method."""

    @pytest.fixture
    def config(self):
        cfg = MockConfig()
        yield cfg
        cfg.cleanup()

    def test_clean_removes_packages_dir(self, config):
        """Test clean removes packages directory."""
        with patch('owrt.package.ToolchainBuilder'):
            builder = PackageBuilder(config)
            builder.packages_dir.mkdir(parents=True)
            (builder.packages_dir / 'test').touch()

            builder.clean()

        assert not builder.packages_dir.exists()

    def test_clean_removes_staging_dir(self, config):
        """Test clean removes staging directory."""
        with patch('owrt.package.ToolchainBuilder'):
            builder = PackageBuilder(config)
            builder.staging_dir.mkdir(parents=True)
            (builder.staging_dir / 'test').touch()

            builder.clean()

        assert not builder.staging_dir.exists()

    def test_clean_handles_missing_dirs(self, config):
        """Test clean handles non-existent directories."""
        with patch('owrt.package.ToolchainBuilder'):
            builder = PackageBuilder(config)

        # Should not raise
        builder.clean()


class TestPackageBuilderDependencies:
    """Tests for dependency resolution."""

    @pytest.fixture
    def config(self):
        cfg = MockConfig()
        yield cfg
        cfg.cleanup()

    def test_resolve_dependencies_single(self, config):
        """Test resolving dependencies for single package."""
        with patch('owrt.package.ToolchainBuilder'):
            builder = PackageBuilder(config)

        with patch.object(builder, '_load_package') as mock_load:
            mock_pkg = MagicMock()
            mock_pkg.build_deps = []
            mock_pkg.runtime_deps = []
            mock_load.return_value = mock_pkg

            result = builder._resolve_dependencies(['busybox'])

        assert 'busybox' in result

    def test_resolve_dependencies_with_deps(self, config):
        """Test resolving dependencies with build deps."""
        with patch('owrt.package.ToolchainBuilder'):
            builder = PackageBuilder(config)

        with patch.object(builder, '_load_package') as mock_load:
            mock_libc = MagicMock()
            mock_libc.build_deps = []
            mock_libc.runtime_deps = []

            mock_busybox = MagicMock()
            mock_busybox.build_deps = ['libc']
            mock_busybox.runtime_deps = []

            def load_side_effect(name):
                if name == 'libc':
                    return mock_libc
                return mock_busybox

            mock_load.side_effect = load_side_effect

            result = builder._resolve_dependencies(['busybox'])

        # libc should come before busybox
        assert result.index('libc') < result.index('busybox')

    def test_resolve_dependencies_avoids_cycles(self, config):
        """Test resolving dependencies handles cycles."""
        with patch('owrt.package.ToolchainBuilder'):
            builder = PackageBuilder(config)

        with patch.object(builder, '_load_package') as mock_load:
            mock_pkg = MagicMock()
            mock_pkg.build_deps = ['busybox']  # Self-reference
            mock_pkg.runtime_deps = []
            mock_load.return_value = mock_pkg

            # Should not infinite loop
            result = builder._resolve_dependencies(['busybox'])

        assert result.count('busybox') == 1


class TestPackageBuilderStamps:
    """Tests for stamp file handling."""

    @pytest.fixture
    def config(self):
        cfg = MockConfig()
        yield cfg
        cfg.cleanup()

    def test_find_stamp_not_found(self, config):
        """Test finding stamp when none exists."""
        with patch('owrt.package.ToolchainBuilder'):
            builder = PackageBuilder(config)
            builder.stamp_dir.mkdir(parents=True)

        result = builder._find_stamp('busybox')

        assert result is None

    def test_find_stamp_found(self, config):
        """Test finding existing stamp."""
        with patch('owrt.package.ToolchainBuilder'):
            builder = PackageBuilder(config)
            builder.stamp_dir.mkdir(parents=True)
            stamp = builder.stamp_dir / 'busybox.built_abc123'
            stamp.touch()

        result = builder._find_stamp('busybox')

        assert result == stamp

    def test_clean_old_stamps(self, config):
        """Test cleaning old stamps."""
        with patch('owrt.package.ToolchainBuilder'):
            builder = PackageBuilder(config)
            builder.stamp_dir.mkdir(parents=True)

            # Create multiple stamps
            (builder.stamp_dir / 'busybox.built_old1').touch()
            (builder.stamp_dir / 'busybox.built_old2').touch()

            builder._clean_old_stamps('busybox')

        # All stamps should be removed
        stamps = list(builder.stamp_dir.glob('busybox.built_*'))
        assert len(stamps) == 0


class TestPackageBuilderHash:
    """Tests for package hash computation."""

    @pytest.fixture
    def config(self):
        cfg = MockConfig()
        yield cfg
        cfg.cleanup()

    def test_compute_package_hash_basic(self, config):
        """Test basic hash computation."""
        with patch('owrt.package.ToolchainBuilder'):
            builder = PackageBuilder(config)

        # Create mock package with pkg_dir
        temp_pkg_dir = Path(tempfile.mkdtemp())
        try:
            yaml_file = temp_pkg_dir / 'package.yaml'
            yaml_file.write_text('name: test\nversion: 1.0')

            mock_pkg = MagicMock()
            mock_pkg.pkg_dir = temp_pkg_dir
            mock_pkg.version = '1.0'
            mock_pkg.release = '1'
            mock_pkg.build_deps = []
            mock_pkg.runtime_deps = []
            mock_pkg.install = {}

            result = builder._compute_package_hash(mock_pkg)

            assert isinstance(result, str)
            assert len(result) == 12  # Truncated hash
        finally:
            shutil.rmtree(temp_pkg_dir)

    def test_compute_package_hash_includes_patches(self, config):
        """Test hash includes patches directory."""
        with patch('owrt.package.ToolchainBuilder'):
            builder = PackageBuilder(config)

        temp_pkg_dir = Path(tempfile.mkdtemp())
        try:
            yaml_file = temp_pkg_dir / 'package.yaml'
            yaml_file.write_text('name: test\nversion: 1.0')

            patches_dir = temp_pkg_dir / 'patches'
            patches_dir.mkdir()
            (patches_dir / '001-fix.patch').write_text('patch content')

            mock_pkg = MagicMock()
            mock_pkg.pkg_dir = temp_pkg_dir
            mock_pkg.version = '1.0'
            mock_pkg.release = '1'
            mock_pkg.build_deps = []
            mock_pkg.runtime_deps = []
            mock_pkg.install = {}

            hash1 = builder._compute_package_hash(mock_pkg)

            # Modify patch
            (patches_dir / '001-fix.patch').write_text('new patch content')

            hash2 = builder._compute_package_hash(mock_pkg)

            assert hash1 != hash2
        finally:
            shutil.rmtree(temp_pkg_dir)

    def test_compute_package_hash_includes_version(self, config):
        """Test hash includes version."""
        with patch('owrt.package.ToolchainBuilder'):
            builder = PackageBuilder(config)

        temp_pkg_dir = Path(tempfile.mkdtemp())
        try:
            yaml_file = temp_pkg_dir / 'package.yaml'
            yaml_file.write_text('name: test\nversion: 1.0')

            mock_pkg = MagicMock()
            mock_pkg.pkg_dir = temp_pkg_dir
            mock_pkg.version = '1.0'
            mock_pkg.release = '1'
            mock_pkg.build_deps = []
            mock_pkg.runtime_deps = []
            mock_pkg.install = {}

            hash1 = builder._compute_package_hash(mock_pkg)

            mock_pkg.version = '2.0'

            hash2 = builder._compute_package_hash(mock_pkg)

            assert hash1 != hash2
        finally:
            shutil.rmtree(temp_pkg_dir)


class TestPackageBuilderIsolation:
    """Tests for fakechroot isolation."""

    @pytest.fixture
    def config(self):
        cfg = MockConfig()
        yield cfg
        cfg.cleanup()

    def test_get_isolation_returns_none_if_unavailable(self, config):
        """Test isolation returns None if fakechroot unavailable."""
        with patch('owrt.package.ToolchainBuilder'), \
             patch('owrt.package.get_fakechroot_isolation', return_value=None):
            builder = PackageBuilder(config)

            result = builder._get_isolation()

        assert result is None

    def test_get_isolation_cached(self, config):
        """Test isolation backend is cached."""
        mock_isolation = MagicMock()
        with patch('owrt.package.ToolchainBuilder'), \
             patch('owrt.package.get_fakechroot_isolation', return_value=mock_isolation):
            builder = PackageBuilder(config)

            result1 = builder._get_isolation()
            result2 = builder._get_isolation()

        assert result1 is result2


class TestPackageBuilderBuildCommands:
    """Tests for build command generation."""

    @pytest.fixture
    def config(self):
        cfg = MockConfig()
        yield cfg
        cfg.cleanup()

    def test_autotools_commands(self, config):
        """Test autotools command generation."""
        with patch('owrt.package.ToolchainBuilder'):
            builder = PackageBuilder(config)

        mock_pkg = MagicMock()
        mock_pkg.build = {'configure_args': ['--disable-nls']}

        commands = builder._get_autotools_isolation_commands(mock_pkg, {})

        assert len(commands) == 4  # configure, make, install x2
        assert '/src/configure' in commands[0]
        assert '--disable-nls' in commands[0]

    def test_cmake_commands(self, config):
        """Test cmake command generation."""
        with patch('owrt.package.ToolchainBuilder'):
            builder = PackageBuilder(config)

        mock_pkg = MagicMock()
        mock_pkg.build = {
            'configure_args': [],
            'cmake_options': ['-DBUILD_TESTS=OFF'],
        }

        commands = builder._get_cmake_isolation_commands(mock_pkg, {})

        assert len(commands) == 4  # cmake, make, install x2
        # cmake args are wrapped in sh -c
        cmake_cmd = commands[0][2]
        assert 'cmake' in cmake_cmd
        assert 'CMAKE_SYSTEM_NAME=Linux' in cmake_cmd

    def test_make_commands(self, config):
        """Test make command generation."""
        with patch('owrt.package.ToolchainBuilder'):
            builder = PackageBuilder(config)

        mock_pkg = MagicMock()
        mock_pkg.build = {'make_args': ['PREFIX=/usr'], 'make_install_args': [], 'env': {}}

        commands = builder._get_make_isolation_commands(mock_pkg, {})

        assert len(commands) == 2  # make, install
        # make args are wrapped in sh -c
        make_cmd = commands[0][2]
        assert 'make' in make_cmd
        assert 'PREFIX=/usr' in make_cmd

    def test_custom_commands(self, config):
        """Test custom command generation."""
        with patch('owrt.package.ToolchainBuilder'):
            builder = PackageBuilder(config)

        mock_pkg = MagicMock()
        mock_pkg.build = {
            'configure_script': './autogen.sh',
            'compile_script': 'make -j$JOBS',
            'install_script': 'make install',
        }

        commands = builder._get_custom_isolation_commands(mock_pkg, {})

        assert len(commands) == 3  # configure, compile, install


class TestPackageBuilderTargetOverlays:
    """Tests for target overlay handling."""

    @pytest.fixture
    def config(self):
        cfg = MockConfig()
        yield cfg
        cfg.cleanup()

    def test_overlay_only_for_base_files(self, config):
        """Test overlays only apply to base-files package."""
        with patch('owrt.package.ToolchainBuilder'):
            builder = PackageBuilder(config)

        # For non-base-files packages, should return empty list
        overlays = builder._get_target_overlay_dirs('busybox')

        assert overlays == []

    def test_overlay_returns_existing_dirs(self, config):
        """Test overlay returns existing directories."""
        with patch('owrt.package.ToolchainBuilder'):
            builder = PackageBuilder(config)

        # This test checks the method signature works correctly
        # Actual overlay dirs depend on openwrt_dir having the files
        overlays = builder._get_target_overlay_dirs('base-files')

        assert isinstance(overlays, list)


class TestPackageBuilderIntegration:
    """Integration tests with real config."""

    def test_init_with_real_config(self):
        """Test initialization with real target config."""
        config = Config.load_target('armsr-armv8')

        with patch('owrt.package.ToolchainBuilder'):
            builder = PackageBuilder(config)

        assert builder.config is config
        assert 'aarch64' in str(builder.apk_dir)

    def test_load_package_real(self):
        """Test loading a real package."""
        config = Config.load_target('armsr-armv8')

        with patch('owrt.package.ToolchainBuilder'):
            builder = PackageBuilder(config)
            pkg = builder._load_package('busybox')

        assert pkg is not None
        assert pkg.name == 'busybox'

    def test_load_package_not_found(self):
        """Test loading non-existent package returns None."""
        config = Config.load_target('armsr-armv8')

        with patch('owrt.package.ToolchainBuilder'):
            builder = PackageBuilder(config)
            pkg = builder._load_package('nonexistent-package-xyz')

        assert pkg is None


class TestPackageBuilderBuildPackages:
    """Tests for build_packages method."""

    @pytest.fixture
    def config(self):
        cfg = MockConfig()
        yield cfg
        cfg.cleanup()

    def test_build_packages_creates_directories(self, config):
        """Test build_packages creates necessary directories."""
        with patch('owrt.package.ToolchainBuilder'):
            builder = PackageBuilder(config)

        with patch.object(builder, '_resolve_dependencies', return_value=[]), \
             patch.object(builder, '_build_package'):
            builder.build_packages([], create_apk=False)

        assert builder.packages_dir.exists()
        assert builder.stamp_dir.exists()

    def test_build_packages_single_package_mode(self, config):
        """Test single_package mode skips dependency resolution."""
        with patch('owrt.package.ToolchainBuilder'):
            builder = PackageBuilder(config)

        with patch.object(builder, '_resolve_dependencies') as mock_resolve, \
             patch.object(builder, '_build_package'):
            builder.build_packages(['busybox'], single_package=True, create_apk=False)

        mock_resolve.assert_not_called()

    def test_build_packages_continue_on_error(self, config):
        """Test continue_on_error mode."""
        with patch('owrt.package.ToolchainBuilder'):
            builder = PackageBuilder(config)

        with patch.object(builder, '_resolve_dependencies', return_value=['pkg1', 'pkg2']), \
             patch.object(builder, '_build_package', side_effect=Exception('Build failed')):
            # Should not raise even though builds fail
            builder.build_packages(['pkg1', 'pkg2'], continue_on_error=True, create_apk=False)
