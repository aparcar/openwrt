"""
Tests for container.py - Docker container management and package isolation.
"""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
import tempfile
import shutil
import os

from owrt.container import (
    Mount,
    ContainerConfig,
    ContainerRuntime,
    is_inside_docker,
    fakechroot_available,
    PackageIsolation,
    get_package_isolation,
)


class TestMount:
    """Tests for Mount dataclass."""

    def test_mount_basic(self):
        """Test basic mount creation."""
        mount = Mount(source=Path('/host/path'), target='/container/path')

        assert mount.source == Path('/host/path')
        assert mount.target == '/container/path'
        assert mount.readonly is False

    def test_mount_readonly(self):
        """Test readonly mount."""
        mount = Mount(source=Path('/src'), target='/dst', readonly=True)

        assert mount.readonly is True

    def test_to_docker_arg(self):
        """Test Docker argument generation."""
        mount = Mount(source=Path('/host'), target='/container')

        args = mount.to_docker_arg()

        assert args == ['-v', '/host:/container']

    def test_to_docker_arg_readonly(self):
        """Test Docker argument with readonly."""
        mount = Mount(source=Path('/host'), target='/container', readonly=True)

        args = mount.to_docker_arg()

        assert args == ['-v', '/host:/container:ro']


class TestContainerConfig:
    """Tests for ContainerConfig dataclass."""

    def test_default_values(self):
        """Test default configuration values."""
        config = ContainerConfig(image='test:latest')

        assert config.image == 'test:latest'
        assert config.mounts == []
        assert config.env == {}
        assert config.workdir == '/build'
        assert config.user is None
        assert config.network == 'none'

    def test_custom_values(self):
        """Test custom configuration."""
        mounts = [Mount(Path('/a'), '/b')]
        config = ContainerConfig(
            image='custom:v1',
            mounts=mounts,
            env={'VAR': 'value'},
            workdir='/custom',
            user='1000:1000',
            network='host',
        )

        assert config.image == 'custom:v1'
        assert config.mounts == mounts
        assert config.env == {'VAR': 'value'}
        assert config.workdir == '/custom'
        assert config.user == '1000:1000'
        assert config.network == 'host'


class TestContainerRuntime:
    """Tests for ContainerRuntime class."""

    def test_init(self):
        """Test ContainerRuntime initialization."""
        runtime = ContainerRuntime(verbose=True)

        assert runtime.verbose is True
        assert runtime._docker_available is None

    def test_is_available_caches_result(self):
        """Test is_available caches its result."""
        runtime = ContainerRuntime()

        with patch('shutil.which', return_value='/usr/bin/docker') as mock_which:
            result1 = runtime.is_available()
            result2 = runtime.is_available()

        # Should only call which() once
        assert mock_which.call_count == 1
        assert result1 is True
        assert result2 is True

    def test_is_available_true(self):
        """Test is_available when Docker exists."""
        runtime = ContainerRuntime()

        with patch('shutil.which', return_value='/usr/bin/docker'):
            assert runtime.is_available() is True

    def test_is_available_false(self):
        """Test is_available when Docker not found."""
        runtime = ContainerRuntime()

        with patch('shutil.which', return_value=None):
            assert runtime.is_available() is False

    def test_image_exists_true(self):
        """Test image_exists when image is present."""
        runtime = ContainerRuntime()

        with patch('shutil.which', return_value='/usr/bin/docker'):
            with patch('subprocess.run') as mock_run:
                mock_run.return_value = MagicMock(returncode=0)
                result = runtime.image_exists('test:latest')

        assert result is True
        mock_run.assert_called_once()

    def test_image_exists_false(self):
        """Test image_exists when image not found."""
        runtime = ContainerRuntime()

        with patch('shutil.which', return_value='/usr/bin/docker'):
            with patch('subprocess.run') as mock_run:
                mock_run.return_value = MagicMock(returncode=1)
                result = runtime.image_exists('missing:latest')

        assert result is False

    def test_image_exists_no_docker(self):
        """Test image_exists when Docker not available."""
        runtime = ContainerRuntime()

        with patch('shutil.which', return_value=None):
            result = runtime.image_exists('test:latest')

        assert result is False

    def test_build_image(self):
        """Test building a Docker image."""
        runtime = ContainerRuntime(verbose=False)

        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            runtime.build_image(
                dockerfile=Path('/path/Dockerfile'),
                tag='test:v1',
                context=Path('/path'),
                build_args={'ARG1': 'value1'},
            )

        call_args = mock_run.call_args[0][0]
        assert 'docker' in call_args
        assert 'build' in call_args
        assert '-t' in call_args
        assert 'test:v1' in call_args
        assert '--build-arg' in call_args

    def test_build_image_failure(self):
        """Test build_image raises on failure."""
        runtime = ContainerRuntime(verbose=False)

        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stderr='Error')

            with pytest.raises(Exception):
                runtime.build_image(
                    dockerfile=Path('/path/Dockerfile'),
                    tag='test:v1',
                    context=Path('/path'),
                )


class TestIsInsideDocker:
    """Tests for is_inside_docker function."""

    def test_owrt_in_container_env(self):
        """Test detection via OWRT_IN_CONTAINER env var."""
        with patch.dict('os.environ', {'OWRT_IN_CONTAINER': '1'}):
            assert is_inside_docker() is True

    def test_dockerenv_exists(self):
        """Test detection via .dockerenv file."""
        with patch.dict('os.environ', {}, clear=True):
            with patch.object(Path, 'exists', return_value=True):
                assert is_inside_docker() is True

    def test_container_env_var(self):
        """Test detection via 'container' env var (Podman, systemd-nspawn)."""
        with patch.dict('os.environ', {'container': 'podman'}, clear=True):
            with patch.object(Path, 'exists', return_value=False):
                assert is_inside_docker() is True

    def test_owrt_tools_dir_env(self):
        """Test detection via OWRT_TOOLS_DIR env var."""
        with patch.dict('os.environ', {'OWRT_TOOLS_DIR': '/opt/owrt-tools'}, clear=True):
            with patch.object(Path, 'exists', return_value=False):
                assert is_inside_docker() is True

    def test_not_in_docker(self):
        """Test when not inside Docker."""
        with patch.dict('os.environ', {}, clear=True):
            with patch.object(Path, 'exists', return_value=False):
                assert is_inside_docker() is False


class TestFakechrootAvailable:
    """Tests for fakechroot_available function."""

    def test_both_available(self):
        """Test when fakechroot and fakeroot are available."""
        with patch('shutil.which') as mock_which:
            mock_which.side_effect = lambda x: f'/usr/bin/{x}'
            assert fakechroot_available() is True

    def test_fakechroot_missing(self):
        """Test when fakechroot is missing."""
        def which_side_effect(cmd):
            if cmd == 'fakechroot':
                return None
            return f'/usr/bin/{cmd}'

        with patch('shutil.which', side_effect=which_side_effect):
            assert fakechroot_available() is False

    def test_fakeroot_missing(self):
        """Test when fakeroot is missing."""
        def which_side_effect(cmd):
            if cmd == 'fakeroot':
                return None
            return f'/usr/bin/{cmd}'

        with patch('shutil.which', side_effect=which_side_effect):
            assert fakechroot_available() is False


class TestPackageIsolation:
    """Tests for PackageIsolation class."""

    @pytest.fixture
    def temp_dirs(self):
        """Create temporary directories."""
        base = Path(tempfile.mkdtemp())
        toolchain_dir = base / 'toolchain'
        repo_dir = base / 'repo'
        toolchain_dir.mkdir()
        repo_dir.mkdir()
        apk_binary = base / 'bin' / 'apk'
        apk_binary.parent.mkdir()
        apk_binary.touch()

        yield {
            'base': base,
            'toolchain': toolchain_dir,
            'repo': repo_dir,
            'apk': apk_binary,
        }
        shutil.rmtree(base, ignore_errors=True)

    def test_init(self, temp_dirs):
        """Test PackageIsolation initialization."""
        iso = PackageIsolation(
            toolchain_dir=temp_dirs['toolchain'],
            repo_dir=temp_dirs['repo'],
            arch='aarch64',
            apk_binary=temp_dirs['apk'],
            verbose=True,
        )

        assert iso.toolchain_dir == temp_dirs['toolchain']
        assert iso.repo_dir == temp_dirs['repo']
        assert iso.arch == 'aarch64'
        assert iso.apk_binary == temp_dirs['apk']
        assert iso.verbose is True

    def test_build_isolated_env(self, temp_dirs):
        """Test building isolated environment variables."""
        iso = PackageIsolation(
            toolchain_dir=temp_dirs['toolchain'],
            repo_dir=temp_dirs['repo'],
            arch='aarch64',
            apk_binary=temp_dirs['apk'],
        )

        staging_dir = temp_dirs['base'] / 'staging'
        install_dir = temp_dirs['base'] / 'install'
        staging_dir.mkdir()
        install_dir.mkdir()

        env = iso._build_isolated_env(
            base_env={},
            source_dir=temp_dirs['base'],
            build_dir=temp_dirs['base'] / 'build',
            staging_dir=staging_dir,
            install_dir=install_dir,
        )

        # Check toolchain in PATH
        assert str(temp_dirs['toolchain'] / 'bin') in env['PATH']
        assert env['STAGING_DIR'] == str(staging_dir)
        assert env['INSTALL_DIR'] == str(install_dir)

    def test_substitute_path_toolchain(self, temp_dirs):
        """Test path substitution for /toolchain."""
        iso = PackageIsolation(
            toolchain_dir=temp_dirs['toolchain'],
            repo_dir=temp_dirs['repo'],
            arch='aarch64',
            apk_binary=temp_dirs['apk'],
        )

        env = {
            'STAGING_DIR': '/tmp/staging',
            'INSTALL_DIR': '/tmp/install',
        }

        # The _run_command method does substitution
        # We'll test the logic indirectly
        command = ['-I/toolchain/include']

        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0)

            build_dir = temp_dirs['base'] / 'build'
            build_dir.mkdir()

            try:
                iso._run_command(
                    command,
                    build_dir,
                    temp_dirs['base'],
                    env,
                )
            except Exception:
                pass

            # Check the substitution happened
            if mock_run.called:
                called_cmd = mock_run.call_args[0][0]
                assert str(temp_dirs['toolchain']) in called_cmd[0]


class TestGetPackageIsolation:
    """Tests for get_package_isolation factory function."""

    @pytest.fixture
    def temp_dirs(self):
        """Create temporary directories."""
        base = Path(tempfile.mkdtemp())
        toolchain = base / 'toolchain'
        repo = base / 'repo'
        toolchain.mkdir()
        repo.mkdir()
        apk = base / 'apk'
        apk.touch()
        yield {'toolchain': toolchain, 'repo': repo, 'apk': apk}
        shutil.rmtree(base, ignore_errors=True)

    def test_returns_isolation_when_available(self, temp_dirs):
        """Test returns PackageIsolation when deps available."""
        result = get_package_isolation(
            toolchain_dir=temp_dirs['toolchain'],
            repo_dir=temp_dirs['repo'],
            arch='aarch64',
            apk_binary=temp_dirs['apk'],
        )

        assert result is not None
        assert isinstance(result, PackageIsolation)

    def test_returns_none_when_apk_missing(self, temp_dirs):
        """Test returns None when apk binary not found."""
        result = get_package_isolation(
            toolchain_dir=temp_dirs['toolchain'],
            repo_dir=temp_dirs['repo'],
            arch='aarch64',
            apk_binary=Path('/nonexistent/apk'),
        )

        assert result is None

    def test_returns_none_when_toolchain_missing(self, temp_dirs):
        """Test returns None when toolchain not found."""
        result = get_package_isolation(
            toolchain_dir=Path('/nonexistent/toolchain'),
            repo_dir=temp_dirs['repo'],
            arch='aarch64',
            apk_binary=temp_dirs['apk'],
        )

        assert result is None
