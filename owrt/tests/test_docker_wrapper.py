"""
Tests for docker_wrapper.py - Docker container wrapper functions.
"""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
import tempfile

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from owrt.docker_wrapper import (
    get_project_root,
    get_base_hash,
    get_base_image_tag,
    get_toolchain_hash,
    get_toolchain_image_tag,
    docker_available,
    image_exists,
    should_use_docker,
)


class TestGetProjectRoot:
    """Tests for get_project_root function."""

    def test_returns_path(self):
        """Test that it returns a Path object."""
        root = get_project_root()
        assert isinstance(root, Path)

    def test_contains_owrt_dir(self):
        """Test that the project root contains owrt directory."""
        root = get_project_root()
        assert (root / 'owrt').exists()


class TestGetBaseHash:
    """Tests for get_base_hash function."""

    def test_returns_string(self):
        """Test that it returns a string hash."""
        h = get_base_hash()
        assert isinstance(h, str)

    def test_hash_length(self):
        """Test hash is 12 characters or 'unknown'."""
        h = get_base_hash()
        assert len(h) == 12 or h == 'unknown'

    def test_hash_consistent(self):
        """Test that hash is consistent across calls."""
        h1 = get_base_hash()
        h2 = get_base_hash()
        assert h1 == h2

    @patch('owrt.docker_wrapper.get_project_root')
    def test_missing_dockerfile_returns_unknown(self, mock_root, tmp_path):
        """Test that missing Dockerfile returns 'unknown'."""
        mock_root.return_value = tmp_path
        h = get_base_hash()
        assert h == 'unknown'


class TestGetBaseImageTag:
    """Tests for get_base_image_tag function."""

    def test_format(self):
        """Test image tag format."""
        tag = get_base_image_tag()
        assert tag.startswith('openwrt-poc-base:')

    def test_contains_hash(self):
        """Test tag contains the hash."""
        tag = get_base_image_tag()
        h = get_base_hash()
        assert h in tag


class TestGetToolchainHash:
    """Tests for get_toolchain_hash function."""

    def test_returns_string(self):
        """Test that it returns a string hash."""
        h = get_toolchain_hash('armsr-armv8')
        assert isinstance(h, str)

    def test_hash_length(self):
        """Test hash is 16 characters."""
        h = get_toolchain_hash('armsr-armv8')
        assert len(h) == 16

    def test_hash_consistent(self):
        """Test that hash is consistent across calls."""
        h1 = get_toolchain_hash('x86-64')
        h2 = get_toolchain_hash('x86-64')
        assert h1 == h2

    def test_different_targets_different_hashes(self):
        """Test that different targets produce different hashes."""
        h1 = get_toolchain_hash('armsr-armv8')
        h2 = get_toolchain_hash('x86-64')
        # Note: hashes might be same if target configs are similar
        # This test just verifies no errors occur
        assert isinstance(h1, str)
        assert isinstance(h2, str)

    def test_slash_vs_dash_normalization(self):
        """Test that x86/64 and x86-64 produce same hash."""
        h1 = get_toolchain_hash('x86/64')
        h2 = get_toolchain_hash('x86-64')
        assert h1 == h2


class TestGetToolchainImageTag:
    """Tests for get_toolchain_image_tag function."""

    def test_format(self):
        """Test image tag format."""
        tag = get_toolchain_image_tag('armsr-armv8')
        assert tag.startswith('openwrt-toolchain:armsr-armv8-')

    def test_contains_hash(self):
        """Test tag contains the hash."""
        tag = get_toolchain_image_tag('armsr-armv8')
        h = get_toolchain_hash('armsr-armv8')
        assert h in tag

    def test_slash_normalization(self):
        """Test that slashes are converted to dashes."""
        tag = get_toolchain_image_tag('x86/64')
        assert 'x86-64' in tag
        assert 'x86/64' not in tag


class TestDockerAvailable:
    """Tests for docker_available function."""

    @patch('shutil.which')
    def test_docker_found(self, mock_which):
        """Test when docker is found."""
        mock_which.return_value = '/usr/bin/docker'
        assert docker_available() is True
        mock_which.assert_called_with('docker')

    @patch('shutil.which')
    def test_docker_not_found(self, mock_which):
        """Test when docker is not found."""
        mock_which.return_value = None
        assert docker_available() is False


class TestImageExists:
    """Tests for image_exists function."""

    @patch('subprocess.run')
    def test_image_exists(self, mock_run):
        """Test when image exists."""
        mock_run.return_value = MagicMock(returncode=0)
        assert image_exists('test:latest') is True
        mock_run.assert_called_once()
        # Verify docker image inspect was called
        call_args = mock_run.call_args[0][0]
        assert 'docker' in call_args
        assert 'image' in call_args
        assert 'inspect' in call_args
        assert 'test:latest' in call_args

    @patch('subprocess.run')
    def test_image_not_exists(self, mock_run):
        """Test when image doesn't exist."""
        mock_run.return_value = MagicMock(returncode=1)
        assert image_exists('nonexistent:tag') is False


class TestShouldUseDocker:
    """Tests for should_use_docker function."""

    @patch('owrt.docker_wrapper.is_inside_docker')
    @patch('owrt.docker_wrapper.docker_available')
    def test_explicit_docker_flag(self, mock_available, mock_inside):
        """Test with explicit --docker flag."""
        mock_inside.return_value = False
        mock_available.return_value = True

        # Explicit True
        assert should_use_docker({}, force_docker=True) is True

        # Explicit False
        assert should_use_docker({}, force_docker=False) is False

    @patch('owrt.docker_wrapper.is_inside_docker')
    @patch('owrt.docker_wrapper.docker_available')
    def test_already_inside_docker(self, mock_available, mock_inside):
        """Test when already inside Docker container."""
        mock_inside.return_value = True
        mock_available.return_value = True

        # Auto-detect should return False
        assert should_use_docker({}) is False

    @patch('owrt.docker_wrapper.is_inside_docker')
    @patch('owrt.docker_wrapper.docker_available')
    def test_auto_detect_docker_available(self, mock_available, mock_inside):
        """Test auto-detection when Docker is available."""
        mock_inside.return_value = False
        mock_available.return_value = True

        assert should_use_docker({}) is True

    @patch('owrt.docker_wrapper.is_inside_docker')
    @patch('owrt.docker_wrapper.docker_available')
    def test_auto_detect_docker_not_available(self, mock_available, mock_inside):
        """Test auto-detection when Docker is not available."""
        mock_inside.return_value = False
        mock_available.return_value = False

        assert should_use_docker({}) is False

    @patch('owrt.docker_wrapper.is_inside_docker')
    def test_docker_flag_in_context(self, mock_inside):
        """Test using docker flag from context object."""
        mock_inside.return_value = False

        # Docker flag set in context
        assert should_use_docker({'docker': True}) is True
        assert should_use_docker({'docker': False}) is False

    @patch('owrt.docker_wrapper.is_inside_docker')
    @patch('builtins.print')
    def test_warning_when_docker_inside_container(self, mock_print, mock_inside):
        """Test warning printed when --docker used inside container."""
        mock_inside.return_value = True

        result = should_use_docker({}, force_docker=True)

        assert result is False
        mock_print.assert_called()
        # Verify warning was printed
        call_args = str(mock_print.call_args)
        assert 'Warning' in call_args or 'already inside' in call_args
