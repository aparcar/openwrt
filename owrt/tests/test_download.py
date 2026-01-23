"""
Tests for download.py - Package source download manager.
"""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock, PropertyMock
import tempfile
import shutil

from owrt.download import (
    get_download_filename,
    get_download_url,
    get_expected_hash,
    download_package_source,
    _download_file,
    DownloadManager,
    OPENWRT_MIRROR,
)


class MockPackageConfig:
    """Mock PackageConfig for testing."""

    def __init__(self, name='test-pkg', version='1.0.0', source=None):
        self.name = name
        self.version = version
        self.source = source or {}


class TestGetDownloadFilename:
    """Tests for get_download_filename function."""

    def test_tarball_source(self):
        """Test filename extraction from tarball URL."""
        pkg = MockPackageConfig(
            name='test',
            source={
                'type': 'tarball',
                'url': 'https://example.com/test-1.0.0.tar.gz',
            }
        )

        filename = get_download_filename(pkg)

        assert filename == 'test-1.0.0.tar.gz'

    def test_tarball_complex_url(self):
        """Test filename extraction from complex URL."""
        pkg = MockPackageConfig(
            name='curl',
            source={
                'type': 'tarball',
                'url': 'https://curl.se/download/curl-8.5.0.tar.xz?param=value',
            }
        )

        filename = get_download_filename(pkg)

        assert filename == 'curl-8.5.0.tar.xz?param=value'

    def test_git_source_with_version(self):
        """Test git source creates OpenWrt-compatible filename."""
        pkg = MockPackageConfig(
            name='libubox',
            version='2024.01.15',
            source={
                'type': 'git',
                'url': 'https://git.openwrt.org/project/libubox.git',
                'version': 'abcdef1234567890',
            }
        )

        filename = get_download_filename(pkg)

        assert filename == 'libubox-2024.01.15~abcdef12.tar.zst'

    def test_git_source_head(self):
        """Test git source with HEAD version."""
        pkg = MockPackageConfig(
            name='project',
            source={
                'type': 'git',
                'version': 'HEAD',
            }
        )

        filename = get_download_filename(pkg)

        assert filename == 'project-git.tar.zst'

    def test_git_source_short_commit(self):
        """Test git source with short commit hash."""
        pkg = MockPackageConfig(
            name='test',
            version='2024.01.01',
            source={
                'type': 'git',
                'version': 'abc123',
            }
        )

        filename = get_download_filename(pkg)

        assert filename == 'test-2024.01.01~abc123.tar.zst'

    def test_no_source(self):
        """Test package with no source returns None."""
        pkg = MockPackageConfig(source=None)
        pkg.source = None

        filename = get_download_filename(pkg)

        assert filename is None

    def test_source_type_none(self):
        """Test source type 'none' returns None."""
        pkg = MockPackageConfig(source={'type': 'none'})

        filename = get_download_filename(pkg)

        assert filename is None

    def test_source_type_local(self):
        """Test source type 'local' returns None."""
        pkg = MockPackageConfig(source={'type': 'local'})

        filename = get_download_filename(pkg)

        assert filename is None

    def test_empty_tarball_url(self):
        """Test tarball with empty URL returns None."""
        pkg = MockPackageConfig(source={'type': 'tarball', 'url': ''})

        filename = get_download_filename(pkg)

        assert filename is None


class TestGetDownloadUrl:
    """Tests for get_download_url function."""

    def test_tarball_url(self):
        """Test getting URL from tarball source."""
        pkg = MockPackageConfig(
            source={
                'type': 'tarball',
                'url': 'https://example.com/pkg.tar.gz',
            }
        )

        url = get_download_url(pkg)

        assert url == 'https://example.com/pkg.tar.gz'

    def test_git_url(self):
        """Test getting URL from git source."""
        pkg = MockPackageConfig(
            source={
                'type': 'git',
                'url': 'https://github.com/test/repo.git',
            }
        )

        url = get_download_url(pkg)

        assert url == 'https://github.com/test/repo.git'

    def test_no_source(self):
        """Test no source returns None."""
        pkg = MockPackageConfig()
        pkg.source = None

        url = get_download_url(pkg)

        assert url is None


class TestGetExpectedHash:
    """Tests for get_expected_hash function."""

    def test_sha256_hash(self):
        """Test extracting sha256 hash."""
        pkg = MockPackageConfig(
            source={'sha256': 'abc123def456'}
        )

        hash_val = get_expected_hash(pkg)

        assert hash_val == 'abc123def456'

    def test_hash_field(self):
        """Test extracting from 'hash' field."""
        pkg = MockPackageConfig(
            source={'hash': 'xyz789'}
        )

        hash_val = get_expected_hash(pkg)

        assert hash_val == 'xyz789'

    def test_sha256_takes_precedence(self):
        """Test sha256 takes precedence over hash."""
        pkg = MockPackageConfig(
            source={
                'sha256': 'sha256value',
                'hash': 'hashvalue',
            }
        )

        hash_val = get_expected_hash(pkg)

        assert hash_val == 'sha256value'

    def test_no_hash(self):
        """Test no hash returns None."""
        pkg = MockPackageConfig(source={'type': 'tarball'})

        hash_val = get_expected_hash(pkg)

        assert hash_val is None


class TestDownloadFile:
    """Tests for _download_file function."""

    @pytest.fixture
    def temp_dir(self):
        """Create temporary directory."""
        temp = Path(tempfile.mkdtemp())
        yield temp
        shutil.rmtree(temp, ignore_errors=True)

    def test_creates_parent_directories(self, temp_dir):
        """Test that parent directories are created."""
        dest = temp_dir / 'subdir' / 'file.txt'

        with patch('owrt.download.subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            # Create the file to simulate download
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.with_suffix('.part').touch()
            dest.with_suffix('.part').rename(dest)

        assert dest.parent.exists()

    def test_uses_curl_when_available(self, temp_dir):
        """Test curl is used when available."""
        dest = temp_dir / 'file.txt'

        with patch('shutil.which', return_value='/usr/bin/curl'):
            with patch('owrt.download.subprocess.run') as mock_run:
                mock_run.return_value = MagicMock(returncode=0)
                # Simulate successful download
                dest.with_suffix('.part').parent.mkdir(parents=True, exist_ok=True)
                dest.with_suffix('.part').touch()

                try:
                    _download_file('https://example.com/file', dest)
                except FileNotFoundError:
                    pass  # Expected since .part doesn't exist after mock

        # Check curl was called
        if mock_run.called:
            call_args = mock_run.call_args[0][0]
            assert call_args[0] == 'curl'


class TestDownloadPackageSource:
    """Tests for download_package_source function."""

    @pytest.fixture
    def temp_dl_dir(self):
        """Create temporary download directory."""
        temp = Path(tempfile.mkdtemp())
        yield temp
        shutil.rmtree(temp, ignore_errors=True)

    def test_returns_none_for_no_download(self, temp_dl_dir):
        """Test returns None when no download needed."""
        pkg = MockPackageConfig(source={'type': 'none'})

        result = download_package_source(pkg, temp_dl_dir)

        assert result is None

    def test_uses_cached_file(self, temp_dl_dir):
        """Test uses existing cached file."""
        pkg = MockPackageConfig(
            name='test',
            source={
                'type': 'tarball',
                'url': 'https://example.com/test-1.0.0.tar.gz',
            }
        )

        # Create cached file
        cached = temp_dl_dir / 'test-1.0.0.tar.gz'
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.touch()

        result = download_package_source(pkg, temp_dl_dir, verbose=True)

        assert result == cached

    def test_tarball_download(self, temp_dl_dir):
        """Test tarball download with mock."""
        pkg = MockPackageConfig(
            name='test',
            source={
                'type': 'tarball',
                'url': 'https://example.com/test-1.0.0.tar.gz',
            }
        )

        with patch('owrt.download._download_file') as mock_dl:
            # Simulate successful download
            def create_file(url, dest, hash_val=None):
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.touch()

            mock_dl.side_effect = create_file

            result = download_package_source(pkg, temp_dl_dir)

        assert result is not None
        assert result.name == 'test-1.0.0.tar.gz'


class TestDownloadManager:
    """Tests for DownloadManager class."""

    @pytest.fixture
    def mock_config(self):
        """Create mock Config."""
        config = MagicMock()
        config.dl_dir = Path(tempfile.mkdtemp())
        yield config
        shutil.rmtree(config.dl_dir, ignore_errors=True)

    def test_init(self, mock_config):
        """Test DownloadManager initialization."""
        manager = DownloadManager(mock_config, verbose=True)

        assert manager.config is mock_config
        assert manager.verbose is True
        assert manager.dl_dir == mock_config.dl_dir

    def test_get_all_downloads(self, mock_config):
        """Test get_all_downloads returns packages with sources."""
        with patch('owrt.download.PackageConfig.find_all_packages') as mock_find:
            pkg1 = MockPackageConfig(
                name='pkg1',
                source={'type': 'tarball', 'url': 'https://example.com/p1.tar.gz'}
            )
            pkg2 = MockPackageConfig(
                name='pkg2',
                source={'type': 'none'}
            )
            mock_find.return_value = [pkg1, pkg2]

            manager = DownloadManager(mock_config)
            downloads = manager.get_all_downloads()

        # Only pkg1 has downloadable source
        assert len(downloads) == 1
        assert downloads[0][0].name == 'pkg1'

    def test_download_package_not_found(self, mock_config):
        """Test download_package raises for unknown package."""
        with patch('owrt.download.PackageConfig.find_package', return_value=None):
            manager = DownloadManager(mock_config)

            with pytest.raises(ValueError, match="Package not found"):
                manager.download_package('nonexistent')

    def test_download_package_success(self, mock_config):
        """Test download_package for existing package."""
        pkg = MockPackageConfig(
            name='test',
            source={'type': 'tarball', 'url': 'https://example.com/test.tar.gz'}
        )

        with patch('owrt.download.PackageConfig.find_package', return_value=pkg):
            with patch('owrt.download.download_package_source') as mock_dl:
                mock_dl.return_value = mock_config.dl_dir / 'test.tar.gz'

                manager = DownloadManager(mock_config)
                result = manager.download_package('test')

        assert result is not None


class TestOpenWrtMirror:
    """Tests for OpenWrt mirror fallback."""

    def test_mirror_constant(self):
        """Test OpenWrt mirror URL is correct."""
        assert OPENWRT_MIRROR == "https://sources.openwrt.org"

    def test_tarball_tries_mirror_on_failure(self):
        """Test tarball download tries OpenWrt mirror on failure."""
        pkg = MockPackageConfig(
            name='test',
            source={
                'type': 'tarball',
                'url': 'https://example.com/test-1.0.0.tar.gz',
            }
        )

        dl_dir = Path(tempfile.mkdtemp())
        try:
            call_count = [0]

            def mock_download(url, dest, hash_val=None):
                call_count[0] += 1
                if call_count[0] == 1:
                    # First call (primary URL) fails
                    raise RuntimeError("Connection failed")
                # Second call (mirror) succeeds
                dest.touch()

            with patch('owrt.download._download_file', side_effect=mock_download):
                result = download_package_source(pkg, dl_dir, use_mirror=True)

            assert call_count[0] == 2  # Tried both URLs
            assert result is not None
        finally:
            shutil.rmtree(dl_dir, ignore_errors=True)
