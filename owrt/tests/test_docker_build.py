"""
Tests for docker_build.py - Docker image building with content-based caching.
"""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
import tempfile

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from owrt.docker_build import DockerImageBuilder, ImageInfo


class TestImageInfo:
    """Tests for ImageInfo dataclass."""

    def test_image_ref(self):
        """Test image_ref property."""
        info = ImageInfo(
            name='openwrt-base',
            tag='abc123',
            content_hash='abc123',
            exists=True,
            full_tag='openwrt-base:abc123',
        )
        assert info.image_ref == 'openwrt-base:abc123'

    def test_attributes(self):
        """Test all attributes are accessible."""
        info = ImageInfo(
            name='test',
            tag='v1',
            content_hash='hash123',
            exists=False,
            full_tag='test:v1',
        )
        assert info.name == 'test'
        assert info.tag == 'v1'
        assert info.content_hash == 'hash123'
        assert info.exists is False
        assert info.full_tag == 'test:v1'


class TestDockerImageBuilder:
    """Tests for DockerImageBuilder class."""

    @pytest.fixture
    def mock_project_dir(self, tmp_path):
        """Create a mock project directory structure."""
        # Create docker directory with Dockerfile
        docker_dir = tmp_path / 'docker'
        docker_dir.mkdir()
        (docker_dir / 'Dockerfile').write_text('FROM ubuntu:24.04\n')
        (docker_dir / 'Dockerfile.base').write_text('FROM ubuntu:24.04\n')

        # Create owrt directory
        owrt_dir = tmp_path / 'owrt'
        owrt_dir.mkdir()
        (owrt_dir / 'toolchain.py').write_text('# toolchain builder\n')

        # Create tools directory
        tools_dir = tmp_path / 'owrt' / 'tools'
        tools_dir.mkdir()
        (tools_dir / 'test.yaml').write_text('name: test\nversion: "1.0"\n')

        return tmp_path

    def test_init(self, mock_project_dir):
        """Test initialization."""
        builder = DockerImageBuilder(mock_project_dir)
        assert builder.project_dir == mock_project_dir
        assert builder.registry is None
        assert builder.verbose is False

    def test_init_with_registry(self, mock_project_dir):
        """Test initialization with registry."""
        builder = DockerImageBuilder(
            mock_project_dir,
            registry='ghcr.io/openwrt',
            verbose=True,
        )
        assert builder.registry == 'ghcr.io/openwrt'
        assert builder.verbose is True

    def test_image_name_without_registry(self, mock_project_dir):
        """Test image name without registry prefix."""
        builder = DockerImageBuilder(mock_project_dir)
        assert builder._image_name('openwrt-base') == 'openwrt-base'

    def test_image_name_with_registry(self, mock_project_dir):
        """Test image name with registry prefix."""
        builder = DockerImageBuilder(mock_project_dir, registry='ghcr.io/openwrt')
        assert builder._image_name('openwrt-base') == 'ghcr.io/openwrt/openwrt-base'

    def test_hash_file(self, mock_project_dir):
        """Test file hashing."""
        builder = DockerImageBuilder(mock_project_dir)
        dockerfile = mock_project_dir / 'docker' / 'Dockerfile'
        h = builder._hash_file(dockerfile)
        assert isinstance(h, str)
        assert len(h) == 64  # SHA256 hex digest

    def test_hash_file_consistent(self, mock_project_dir):
        """Test that file hash is consistent."""
        builder = DockerImageBuilder(mock_project_dir)
        dockerfile = mock_project_dir / 'docker' / 'Dockerfile'
        h1 = builder._hash_file(dockerfile)
        h2 = builder._hash_file(dockerfile)
        assert h1 == h2

    def test_hash_directory(self, mock_project_dir):
        """Test directory hashing."""
        builder = DockerImageBuilder(mock_project_dir)
        h = builder._hash_directory(mock_project_dir / 'docker')
        assert isinstance(h, str)
        assert len(h) == 64

    def test_hash_directory_empty(self, tmp_path):
        """Test hashing non-existent directory."""
        builder = DockerImageBuilder(tmp_path)
        h = builder._hash_directory(tmp_path / 'nonexistent')
        # Should return hash of empty content
        assert isinstance(h, str)

    def test_compute_base_hash(self, mock_project_dir):
        """Test computing base image hash."""
        builder = DockerImageBuilder(mock_project_dir)
        h = builder.compute_base_hash()
        assert isinstance(h, str)
        assert len(h) == 12  # Truncated hash

    def test_compute_base_hash_cached(self, mock_project_dir):
        """Test that base hash is cached."""
        builder = DockerImageBuilder(mock_project_dir)
        h1 = builder.compute_base_hash()
        h2 = builder.compute_base_hash()
        assert h1 == h2
        # Verify it was cached
        assert 'base' in builder._hash_cache

    @patch('subprocess.run')
    def test_image_exists_local(self, mock_run, mock_project_dir):
        """Test checking if image exists locally."""
        mock_run.return_value = MagicMock(returncode=0)
        builder = DockerImageBuilder(mock_project_dir)

        assert builder._image_exists('test:tag') is True
        mock_run.assert_called_once()
        call_args = mock_run.call_args[0][0]
        assert 'image' in call_args
        assert 'inspect' in call_args

    @patch('subprocess.run')
    def test_image_not_exists(self, mock_run, mock_project_dir):
        """Test checking non-existent image."""
        mock_run.return_value = MagicMock(returncode=1)
        builder = DockerImageBuilder(mock_project_dir)

        assert builder._image_exists('nonexistent:tag') is False

    @patch('subprocess.run')
    def test_image_exists_remote(self, mock_run, mock_project_dir):
        """Test checking if image exists in remote registry."""
        mock_run.return_value = MagicMock(returncode=0)
        builder = DockerImageBuilder(mock_project_dir, registry='ghcr.io/test')

        assert builder._image_exists('test:tag', check_remote=True) is True
        call_args = mock_run.call_args[0][0]
        assert 'manifest' in call_args
        assert 'inspect' in call_args

    def test_log_verbose(self, mock_project_dir, capsys):
        """Test logging in verbose mode."""
        builder = DockerImageBuilder(mock_project_dir, verbose=True)
        builder._log('test message')
        captured = capsys.readouterr()
        assert 'test message' in captured.out

    def test_log_quiet(self, mock_project_dir, capsys):
        """Test logging in quiet mode."""
        builder = DockerImageBuilder(mock_project_dir, verbose=False)
        builder._log('test message')
        captured = capsys.readouterr()
        assert captured.out == ''

    def test_export_hashes(self, mock_project_dir):
        """Test exporting hashes."""
        builder = DockerImageBuilder(mock_project_dir)
        hashes = builder.export_hashes()

        assert isinstance(hashes, dict)
        assert 'base' in hashes
        assert 'tools' in hashes

    def test_status(self, mock_project_dir):
        """Test getting status."""
        builder = DockerImageBuilder(mock_project_dir)

        with patch.object(builder, '_image_exists', return_value=False):
            status = builder.status()

        assert isinstance(status, dict)
        assert 'base' in status
        assert isinstance(status['base'], ImageInfo)

    def test_compute_tools_hash(self, mock_project_dir):
        """Test computing tools image hash."""
        builder = DockerImageBuilder(mock_project_dir)
        h = builder.compute_tools_hash()
        assert isinstance(h, str)
        assert len(h) == 12

    def test_compute_toolchain_hash(self, mock_project_dir):
        """Test computing toolchain hash for target."""
        # Create target directory
        target_dir = mock_project_dir / 'target' / 'linux' / 'armsr' / 'armv8'
        target_dir.mkdir(parents=True)
        (target_dir / 'target.yaml').write_text('name: armsr-armv8\narch: aarch64\n')

        builder = DockerImageBuilder(mock_project_dir)
        h = builder.compute_toolchain_hash('armsr-armv8')
        assert isinstance(h, str)
        assert len(h) == 12
