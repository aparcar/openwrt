"""Tests for APK packaging (apk.py)."""

import gzip
import pytest
import tarfile
import tempfile
import shutil
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock, call
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any

from owrt.apk import APKPackager, APKRootfs, APKRepository


@dataclass
class MockPackageConfig:
    """Mock PackageConfig for testing."""
    name: str
    version: str = "1.0.0"
    release: int = 1
    license: str = "GPL-2.0"
    runtime_deps: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    conflicts: List[str] = field(default_factory=list)
    provides: List[str] = field(default_factory=list)
    replaces: List[str] = field(default_factory=list)
    scripts: Dict[str, str] = field(default_factory=dict)
    alternatives: List[str] = field(default_factory=list)
    _raw_data: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MockVariantConfig:
    """Mock VariantConfig for testing."""
    name: str
    package_name: str
    source_name: str
    version: str = "1.0.0"
    release: int = 1
    license: str = "GPL-2.0"
    runtime_deps: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    description: str = ""


@dataclass
class MockConfig:
    """Mock Config for testing."""
    arch: str = "aarch64"
    build_dir: Path = field(default_factory=lambda: Path("/tmp/build"))
    staging_dir: Path = field(default_factory=lambda: Path("/tmp/staging"))
    rootfs_dir: Path = field(default_factory=lambda: Path("/tmp/rootfs"))
    toolchain_dir: Path = field(default_factory=lambda: Path("/tmp/toolchain"))
    target_tuple: str = "aarch64-openwrt-linux-musl"


class TestAPKPackager:
    """Tests for APKPackager class."""

    @pytest.fixture
    def temp_dir(self):
        """Create a temporary directory for tests."""
        tmpdir = tempfile.mkdtemp()
        yield Path(tmpdir)
        shutil.rmtree(tmpdir)

    @pytest.fixture
    def mock_config(self, temp_dir):
        """Create a mock config with temp directory."""
        return MockConfig(
            build_dir=temp_dir / 'build',
            staging_dir=temp_dir / 'staging',
            rootfs_dir=temp_dir / 'rootfs',
            toolchain_dir=temp_dir / 'toolchain',
        )

    @pytest.fixture
    def packager_with_apk(self, mock_config, temp_dir):
        """Create APKPackager with a mock apk binary."""
        apk_binary = temp_dir / 'apk'
        apk_binary.write_text("#!/bin/sh\nexit 0")
        apk_binary.chmod(0o755)
        return APKPackager(mock_config, apk_binary=apk_binary)

    def test_init_finds_apk_in_host_staging(self, mock_config, temp_dir):
        """Test that APKPackager finds apk in host-staging."""
        host_staging_bin = mock_config.build_dir / 'host-staging' / 'bin'
        host_staging_bin.mkdir(parents=True)
        apk_binary = host_staging_bin / 'apk'
        apk_binary.write_text("#!/bin/sh\nexit 0")
        apk_binary.chmod(0o755)

        packager = APKPackager(mock_config)
        assert packager.apk_binary == apk_binary

    def test_init_no_apk_found(self, temp_dir):
        """Test APKPackager when no apk binary found."""
        config = MockConfig(build_dir=temp_dir / 'empty')
        packager = APKPackager(config)
        assert packager.apk_binary is None

    def test_have_apk_true(self, packager_with_apk):
        """Test have_apk returns True when apk exists."""
        assert packager_with_apk.have_apk() is True

    def test_have_apk_false(self, temp_dir):
        """Test have_apk returns False when apk missing."""
        config = MockConfig(build_dir=temp_dir / 'empty')
        packager = APKPackager(config)
        assert packager.have_apk() is False

    def test_packages_dir_uses_arch(self, packager_with_apk):
        """Test packages directory includes architecture."""
        assert 'aarch64' in str(packager_with_apk.packages_dir)

    def test_output_dir_uses_arch(self, packager_with_apk):
        """Test output directory includes architecture."""
        assert 'aarch64' in str(packager_with_apk.output_dir)

    def test_get_apk_arch_aarch64(self, mock_config, temp_dir):
        """Test APK arch mapping for aarch64."""
        apk_binary = temp_dir / 'apk'
        apk_binary.write_text("#!/bin/sh\nexit 0")
        apk_binary.chmod(0o755)
        
        mock_config.arch = 'aarch64'
        packager = APKPackager(mock_config, apk_binary=apk_binary)
        assert packager._get_apk_arch() == 'aarch64'

    def test_get_apk_arch_arm(self, mock_config, temp_dir):
        """Test APK arch mapping for arm."""
        apk_binary = temp_dir / 'apk'
        apk_binary.write_text("#!/bin/sh\nexit 0")
        apk_binary.chmod(0o755)
        
        mock_config.arch = 'arm'
        packager = APKPackager(mock_config, apk_binary=apk_binary)
        assert packager._get_apk_arch() == 'armv7'

    def test_get_apk_arch_x86_64(self, mock_config, temp_dir):
        """Test APK arch mapping for x86_64."""
        apk_binary = temp_dir / 'apk'
        apk_binary.write_text("#!/bin/sh\nexit 0")
        apk_binary.chmod(0o755)
        
        mock_config.arch = 'x86_64'
        packager = APKPackager(mock_config, apk_binary=apk_binary)
        assert packager._get_apk_arch() == 'x86_64'

    def test_get_apk_arch_i386(self, mock_config, temp_dir):
        """Test APK arch mapping for i386."""
        apk_binary = temp_dir / 'apk'
        apk_binary.write_text("#!/bin/sh\nexit 0")
        apk_binary.chmod(0o755)
        
        mock_config.arch = 'i386'
        packager = APKPackager(mock_config, apk_binary=apk_binary)
        assert packager._get_apk_arch() == 'x86'

    def test_build_metadata_basic(self, packager_with_apk):
        """Test metadata building for basic package."""
        pkg = MockPackageConfig(
            name="test-pkg",
            version="1.2.3",
            release=5,
            license="MIT",
            metadata={'description': 'A test package'}
        )
        
        metadata = packager_with_apk._build_metadata(pkg, 'aarch64')
        
        assert metadata['name'] == 'test-pkg'
        assert metadata['version'] == '1.2.3-r5'
        assert metadata['description'] == 'A test package'
        assert metadata['arch'] == 'aarch64'
        assert metadata['license'] == 'MIT'
        assert metadata['origin'] == 'test-pkg'

    def test_build_metadata_variant(self, packager_with_apk):
        """Test metadata building for variant package."""
        pkg = MockVariantConfig(
            name="variant1",
            package_name="test-pkg-variant1",
            source_name="test-pkg",
            version="2.0.0",
            release=1,
            description="Variant description"
        )
        
        metadata = packager_with_apk._build_metadata(pkg, 'aarch64')
        
        assert metadata['name'] == 'test-pkg-variant1'
        assert metadata['origin'] == 'test-pkg'
        assert metadata['description'] == 'Variant description'

    def test_build_metadata_multiline_description(self, packager_with_apk):
        """Test that multiline descriptions use only first line."""
        pkg = MockPackageConfig(
            name="test-pkg",
            metadata={'description': 'First line\nSecond line\nThird line'}
        )
        
        metadata = packager_with_apk._build_metadata(pkg, 'aarch64')
        
        assert metadata['description'] == 'First line'
        assert '\n' not in metadata['description']

    def test_build_metadata_fallback_title(self, packager_with_apk):
        """Test metadata falls back to title when no description."""
        pkg = MockPackageConfig(
            name="test-pkg",
            metadata={'title': 'Package Title'}
        )
        
        metadata = packager_with_apk._build_metadata(pkg, 'aarch64')
        
        assert metadata['description'] == 'Package Title'

    def test_build_metadata_fallback_name(self, packager_with_apk):
        """Test metadata falls back to name when no description or title."""
        pkg = MockPackageConfig(name="test-pkg", metadata={})
        
        metadata = packager_with_apk._build_metadata(pkg, 'aarch64')
        
        assert metadata['description'] == 'test-pkg'

    def test_create_package_no_apk_raises(self, temp_dir):
        """Test create_package raises when no apk binary."""
        config = MockConfig(build_dir=temp_dir / 'empty')
        packager = APKPackager(config)
        pkg = MockPackageConfig(name="test")
        
        with pytest.raises(RuntimeError, match="apk binary not found"):
            packager.create_package(pkg, temp_dir, temp_dir)

    @patch('owrt.apk.run_command')
    def test_create_package_basic(self, mock_run, packager_with_apk, temp_dir):
        """Test basic package creation."""
        pkg = MockPackageConfig(
            name="test-pkg",
            version="1.0.0",
            release=1
        )
        
        pkg_dir = temp_dir / 'pkg'
        staging_dir = temp_dir / 'staging'
        pkg_dir.mkdir()
        staging_dir.mkdir()
        
        # Create dummy apk file
        packager_with_apk.output_dir.mkdir(parents=True)
        expected_apk = packager_with_apk.output_dir / 'test-pkg-1.0.0-r1.apk'
        expected_apk.write_text("dummy")
        
        result = packager_with_apk.create_package(pkg, pkg_dir, staging_dir)
        
        assert mock_run.called
        assert result == expected_apk

    @patch('owrt.apk.run_command')
    def test_create_package_with_dependencies(self, mock_run, packager_with_apk, temp_dir):
        """Test package creation includes dependencies."""
        pkg = MockPackageConfig(
            name="test-pkg",
            runtime_deps=["libc", "libgcc"]
        )
        
        pkg_dir = temp_dir / 'pkg'
        staging_dir = temp_dir / 'staging'
        pkg_dir.mkdir()
        staging_dir.mkdir()
        
        packager_with_apk.output_dir.mkdir(parents=True)
        expected_apk = packager_with_apk.output_dir / 'test-pkg-1.0.0-r1.apk'
        expected_apk.write_text("dummy")
        
        packager_with_apk.create_package(pkg, pkg_dir, staging_dir)
        
        # Check that depends info was passed
        cmd = mock_run.call_args[0][0]
        assert '--info' in cmd
        deps_idx = None
        for i, arg in enumerate(cmd):
            if arg.startswith('depends:'):
                deps_idx = i
                break
        assert deps_idx is not None
        assert 'libc' in cmd[deps_idx]
        assert 'libgcc' in cmd[deps_idx]

    @patch('owrt.apk.run_command')
    def test_create_package_with_conflicts(self, mock_run, packager_with_apk, temp_dir):
        """Test package creation includes conflicts as !pkgname."""
        pkg = MockPackageConfig(
            name="test-pkg",
            conflicts=["old-pkg", "deprecated-pkg"]
        )
        
        pkg_dir = temp_dir / 'pkg'
        staging_dir = temp_dir / 'staging'
        pkg_dir.mkdir()
        staging_dir.mkdir()
        
        packager_with_apk.output_dir.mkdir(parents=True)
        expected_apk = packager_with_apk.output_dir / 'test-pkg-1.0.0-r1.apk'
        expected_apk.write_text("dummy")
        
        packager_with_apk.create_package(pkg, pkg_dir, staging_dir)
        
        cmd = mock_run.call_args[0][0]
        cmd_str = ' '.join(cmd)
        assert '!old-pkg' in cmd_str
        assert '!deprecated-pkg' in cmd_str

    @patch('owrt.apk.run_command')
    def test_create_package_with_provides(self, mock_run, packager_with_apk, temp_dir):
        """Test package creation includes provides."""
        pkg = MockPackageConfig(
            name="test-pkg",
            provides=["virtual-pkg", "alias-pkg"]
        )
        
        pkg_dir = temp_dir / 'pkg'
        staging_dir = temp_dir / 'staging'
        pkg_dir.mkdir()
        staging_dir.mkdir()
        
        packager_with_apk.output_dir.mkdir(parents=True)
        expected_apk = packager_with_apk.output_dir / 'test-pkg-1.0.0-r1.apk'
        expected_apk.write_text("dummy")
        
        packager_with_apk.create_package(pkg, pkg_dir, staging_dir)
        
        cmd = mock_run.call_args[0][0]
        provides_found = False
        for i, arg in enumerate(cmd):
            if arg.startswith('provides:'):
                provides_found = True
                assert 'virtual-pkg' in arg
                assert 'alias-pkg' in arg
        assert provides_found

    def test_build_pkginfo_legacy(self, packager_with_apk):
        """Test legacy PKGINFO building."""
        pkg = MockPackageConfig(
            name="test-pkg",
            version="1.0.0",
            release=1,
            license="GPL-2.0",
            runtime_deps=["libc"],
            metadata={'description': 'Test package'}
        )
        
        pkginfo = packager_with_apk._build_pkginfo(pkg, 'aarch64')
        
        assert 'pkgname = test-pkg' in pkginfo
        assert 'pkgver = 1.0.0-r1' in pkginfo
        assert 'pkgdesc = Test package' in pkginfo
        assert 'arch = aarch64' in pkginfo
        assert 'license = GPL-2.0' in pkginfo
        assert 'depend = libc' in pkginfo
        assert 'provides = test-pkg=1.0.0-r1' in pkginfo

    def test_strip_binaries_no_directory(self, packager_with_apk, temp_dir):
        """Test strip_binaries returns 0 for non-existent directory."""
        result = packager_with_apk.strip_binaries(temp_dir / 'nonexistent')
        assert result == 0

    def test_strip_binaries_no_strip_tool(self, mock_config, temp_dir):
        """Test strip_binaries skips when strip tool not found."""
        apk_binary = temp_dir / 'apk'
        apk_binary.write_text("#!/bin/sh\nexit 0")
        apk_binary.chmod(0o755)
        
        packager = APKPackager(mock_config, apk_binary=apk_binary, verbose=True)
        
        # Create a directory with a file (not ELF)
        staging = temp_dir / 'staging'
        staging.mkdir()
        (staging / 'test.txt').write_text("not elf")
        
        result = packager.strip_binaries(staging)
        assert result == 0


class TestAPKRootfs:
    """Tests for APKRootfs class."""

    @pytest.fixture
    def temp_dir(self):
        """Create a temporary directory for tests."""
        tmpdir = tempfile.mkdtemp()
        yield Path(tmpdir)
        shutil.rmtree(tmpdir)

    @pytest.fixture
    def mock_config(self, temp_dir):
        """Create a mock config with temp directory."""
        return MockConfig(
            build_dir=temp_dir / 'build',
            staging_dir=temp_dir / 'staging',
            rootfs_dir=temp_dir / 'rootfs',
            toolchain_dir=temp_dir / 'toolchain',
        )

    @pytest.fixture
    def rootfs_with_apk(self, mock_config, temp_dir):
        """Create APKRootfs with a mock apk binary."""
        apk_binary = temp_dir / 'apk'
        apk_binary.write_text("#!/bin/sh\nexit 0")
        apk_binary.chmod(0o755)
        return APKRootfs(mock_config, apk_binary=apk_binary)

    def test_init_finds_apk(self, mock_config, temp_dir):
        """Test that APKRootfs finds apk in host-staging."""
        host_staging_bin = mock_config.build_dir / 'host-staging' / 'bin'
        host_staging_bin.mkdir(parents=True)
        apk_binary = host_staging_bin / 'apk'
        apk_binary.write_text("#!/bin/sh\nexit 0")
        apk_binary.chmod(0o755)

        rootfs = APKRootfs(mock_config)
        assert rootfs.apk_binary == apk_binary

    def test_have_apk_true(self, rootfs_with_apk):
        """Test have_apk returns True when apk exists."""
        assert rootfs_with_apk.have_apk() is True

    def test_have_apk_false(self, temp_dir):
        """Test have_apk returns False when apk missing."""
        config = MockConfig(build_dir=temp_dir / 'empty')
        rootfs = APKRootfs(config)
        assert rootfs.have_apk() is False

    def test_repo_dir_uses_arch(self, rootfs_with_apk):
        """Test repo directory includes architecture."""
        assert 'aarch64' in str(rootfs_with_apk.repo_dir)

    def test_fallback_rootfs_creates_structure(self, rootfs_with_apk, temp_dir):
        """Test fallback rootfs creates basic structure."""
        rootfs_with_apk.rootfs_dir = temp_dir / 'rootfs'
        rootfs_with_apk.rootfs_dir.mkdir(parents=True)
        
        result = rootfs_with_apk._fallback_rootfs([])
        
        assert result.exists()
        assert (result / 'bin').exists()
        assert (result / 'etc').exists()
        assert (result / 'lib').exists()

    def test_finalize_rootfs_creates_directories(self, rootfs_with_apk, temp_dir):
        """Test finalize_rootfs creates expected directories."""
        rootfs_with_apk.rootfs_dir = temp_dir / 'rootfs'
        rootfs_with_apk.rootfs_dir.mkdir(parents=True)
        
        rootfs_with_apk._finalize_rootfs()
        
        assert (rootfs_with_apk.rootfs_dir / 'bin').exists()
        assert (rootfs_with_apk.rootfs_dir / 'dev').exists()
        assert (rootfs_with_apk.rootfs_dir / 'etc' / 'init.d').exists()
        assert (rootfs_with_apk.rootfs_dir / 'proc').exists()
        assert (rootfs_with_apk.rootfs_dir / 'sys').exists()
        assert (rootfs_with_apk.rootfs_dir / 'tmp').exists()
        assert (rootfs_with_apk.rootfs_dir / 'var' / 'log').exists()

    def test_finalize_rootfs_creates_symlinks(self, rootfs_with_apk, temp_dir):
        """Test finalize_rootfs creates expected symlinks."""
        rootfs_with_apk.rootfs_dir = temp_dir / 'rootfs'
        rootfs_with_apk.rootfs_dir.mkdir(parents=True)
        (rootfs_with_apk.rootfs_dir / 'bin').mkdir()
        
        # Create busybox target
        busybox = rootfs_with_apk.rootfs_dir / 'bin' / 'busybox'
        busybox.write_text("dummy")
        
        rootfs_with_apk._finalize_rootfs()
        
        # Check symlinks
        sh_link = rootfs_with_apk.rootfs_dir / 'bin' / 'sh'
        assert sh_link.is_symlink()
        assert sh_link.readlink() == Path('busybox')

    def test_finalize_rootfs_creates_init_symlink(self, rootfs_with_apk, temp_dir):
        """Test finalize_rootfs creates /init symlink for initramfs."""
        rootfs_with_apk.rootfs_dir = temp_dir / 'rootfs'
        rootfs_with_apk.rootfs_dir.mkdir(parents=True)
        
        rootfs_with_apk._finalize_rootfs()
        
        init_link = rootfs_with_apk.rootfs_dir / 'init'
        assert init_link.is_symlink()
        assert str(init_link.readlink()) == '/sbin/init'

    def test_generate_list_files_creates_dir(self, rootfs_with_apk, temp_dir):
        """Test _generate_list_files creates packages directory."""
        rootfs_with_apk.rootfs_dir = temp_dir / 'rootfs'
        rootfs_with_apk.rootfs_dir.mkdir(parents=True)
        
        rootfs_with_apk._generate_list_files()
        
        # Should not crash when installed db doesn't exist
        packages_dir = rootfs_with_apk.rootfs_dir / 'lib' / 'apk' / 'packages'
        # Directory not created if no installed db
        assert not packages_dir.exists()

    def test_generate_list_files_parses_db(self, rootfs_with_apk, temp_dir):
        """Test _generate_list_files parses installed database."""
        rootfs_with_apk.rootfs_dir = temp_dir / 'rootfs'
        rootfs_with_apk.rootfs_dir.mkdir(parents=True)
        
        # Create mock installed database
        db_dir = rootfs_with_apk.rootfs_dir / 'lib' / 'apk' / 'db'
        db_dir.mkdir(parents=True)
        
        installed_content = """P:busybox
F:bin
R:busybox
R:sh
F:etc
R:passwd

P:base-files
F:etc
R:hosts
R:fstab
"""
        (db_dir / 'installed').write_text(installed_content)
        
        rootfs_with_apk._generate_list_files()
        
        packages_dir = rootfs_with_apk.rootfs_dir / 'lib' / 'apk' / 'packages'
        assert packages_dir.exists()
        
        busybox_list = packages_dir / 'busybox.list'
        assert busybox_list.exists()
        content = busybox_list.read_text()
        assert '/bin/busybox' in content
        assert '/bin/sh' in content
        
        base_files_list = packages_dir / 'base-files.list'
        assert base_files_list.exists()
        content = base_files_list.read_text()
        assert '/etc/hosts' in content

    def test_run_postinst_scripts_no_scripts(self, rootfs_with_apk, temp_dir):
        """Test _run_postinst_scripts handles missing scripts.tar.gz."""
        rootfs_with_apk.rootfs_dir = temp_dir / 'rootfs'
        rootfs_with_apk.rootfs_dir.mkdir(parents=True)
        (rootfs_with_apk.rootfs_dir / 'lib' / 'apk' / 'db').mkdir(parents=True)
        
        # Should not crash when scripts.tar.gz doesn't exist
        rootfs_with_apk._run_postinst_scripts()

    def test_run_postinst_scripts_extracts_and_runs(self, rootfs_with_apk, temp_dir):
        """Test _run_postinst_scripts extracts and runs scripts."""
        rootfs_with_apk.rootfs_dir = temp_dir / 'rootfs'
        rootfs_with_apk.rootfs_dir.mkdir(parents=True)
        db_dir = rootfs_with_apk.rootfs_dir / 'lib' / 'apk' / 'db'
        db_dir.mkdir(parents=True)
        
        # Create empty installed db to avoid errors
        (db_dir / 'installed').write_text("")
        
        # Create scripts.tar with a post-install script
        scripts_tar_path = temp_dir / 'scripts.tar'
        with tarfile.open(scripts_tar_path, 'w') as tar:
            # Create a simple post-install script
            script_content = b'#!/bin/sh\necho "postinst ran"\n'
            import io
            script_info = tarfile.TarInfo(name='test-pkg-1.0.0-r1.X1abc123.post-install')
            script_info.size = len(script_content)
            tar.addfile(script_info, io.BytesIO(script_content))
        
        # Compress it
        with open(scripts_tar_path, 'rb') as f_in:
            with gzip.open(db_dir / 'scripts.tar.gz', 'wb') as f_out:
                f_out.write(f_in.read())
        
        rootfs_with_apk._run_postinst_scripts()
        
        # Script should have been extracted and then removed
        assert not (db_dir / 'test-pkg-1.0.0-r1.X1abc123.post-install').exists()


class TestAPKRepository:
    """Tests for APKRepository class."""

    @pytest.fixture
    def temp_dir(self):
        """Create a temporary directory for tests."""
        tmpdir = tempfile.mkdtemp()
        yield Path(tmpdir)
        shutil.rmtree(tmpdir)

    @pytest.fixture
    def repo_with_apk(self, temp_dir):
        """Create APKRepository with a mock apk binary."""
        apk_binary = temp_dir / 'apk'
        apk_binary.write_text("#!/bin/sh\nexit 0")
        apk_binary.chmod(0o755)
        repo_dir = temp_dir / 'repo'
        return APKRepository(repo_dir, apk_binary=apk_binary, arch='aarch64')

    def test_init_with_explicit_apk(self, temp_dir):
        """Test APKRepository with explicit apk binary."""
        apk_binary = temp_dir / 'apk'
        apk_binary.write_text("#!/bin/sh\nexit 0")
        apk_binary.chmod(0o755)
        
        repo = APKRepository(temp_dir / 'repo', apk_binary=apk_binary)
        assert repo.apk_binary == apk_binary

    def test_init_finds_apk_in_build_dir(self, temp_dir):
        """Test APKRepository finds apk in build_dir."""
        build_dir = temp_dir / 'build'
        host_staging_bin = build_dir / 'host-staging' / 'bin'
        host_staging_bin.mkdir(parents=True)
        apk_binary = host_staging_bin / 'apk'
        apk_binary.write_text("#!/bin/sh\nexit 0")
        apk_binary.chmod(0o755)
        
        repo = APKRepository(temp_dir / 'repo', build_dir=build_dir)
        assert repo.apk_binary == apk_binary

    def test_init_no_apk_found(self, temp_dir):
        """Test APKRepository when no apk binary found."""
        repo = APKRepository(temp_dir / 'repo')
        assert repo.apk_binary is None

    def test_have_apk_true(self, repo_with_apk):
        """Test have_apk returns True when apk exists."""
        assert repo_with_apk.have_apk() is True

    def test_have_apk_false(self, temp_dir):
        """Test have_apk returns False when apk missing."""
        repo = APKRepository(temp_dir / 'repo')
        assert repo.have_apk() is False

    def test_get_arch_dir(self, repo_with_apk):
        """Test _get_arch_dir returns arch-specific path."""
        arch_dir = repo_with_apk._get_arch_dir()
        assert arch_dir.name == 'aarch64'
        assert arch_dir.parent == repo_with_apk.repo_dir

    def test_add_package_creates_arch_dir(self, repo_with_apk, temp_dir):
        """Test add_package creates architecture directory."""
        # Create a dummy apk file
        apk_file = temp_dir / 'test-pkg-1.0.0-r1.apk'
        apk_file.write_text("dummy apk content")
        
        repo_with_apk.add_package(apk_file)
        
        arch_dir = repo_with_apk._get_arch_dir()
        assert arch_dir.exists()
        assert (arch_dir / 'test-pkg-1.0.0-r1.apk').exists()

    def test_add_package_copies_file(self, repo_with_apk, temp_dir):
        """Test add_package copies apk file to arch dir."""
        apk_file = temp_dir / 'test-pkg-1.0.0-r1.apk'
        apk_file.write_text("dummy apk content")
        
        repo_with_apk.add_package(apk_file)
        
        copied = repo_with_apk._get_arch_dir() / 'test-pkg-1.0.0-r1.apk'
        assert copied.exists()
        assert copied.read_text() == "dummy apk content"

    def test_generate_index_no_apk_raises(self, temp_dir):
        """Test generate_index raises when no apk binary."""
        repo = APKRepository(temp_dir / 'repo')
        
        with pytest.raises(RuntimeError, match="apk binary not found"):
            repo.generate_index()

    def test_generate_index_no_packages_warns(self, repo_with_apk, capsys):
        """Test generate_index warns when no packages."""
        repo_with_apk.generate_index()
        
        captured = capsys.readouterr()
        assert "No packages in repository" in captured.out

    @patch('owrt.apk.run_command')
    def test_generate_index_creates_index(self, mock_run, repo_with_apk, temp_dir):
        """Test generate_index creates packages.adb."""
        # Create some dummy apk files in arch dir
        arch_dir = repo_with_apk._get_arch_dir()
        arch_dir.mkdir(parents=True)
        (arch_dir / 'pkg1-1.0.apk').write_text("dummy1")
        (arch_dir / 'pkg2-2.0.apk').write_text("dummy2")
        
        repo_with_apk.generate_index()
        
        assert mock_run.called
        cmd = mock_run.call_args[0][0]
        assert 'mkndx' in cmd
        assert '--output' in cmd
        assert any('packages.adb' in str(arg) for arg in cmd)

    @patch('owrt.apk.run_command')
    def test_generate_index_includes_all_packages(self, mock_run, repo_with_apk, temp_dir, capsys):
        """Test generate_index includes all apk files."""
        arch_dir = repo_with_apk._get_arch_dir()
        arch_dir.mkdir(parents=True)
        (arch_dir / 'pkg1-1.0.apk').write_text("dummy1")
        (arch_dir / 'pkg2-2.0.apk').write_text("dummy2")
        (arch_dir / 'pkg3-3.0.apk').write_text("dummy3")
        
        repo_with_apk.generate_index()
        
        captured = capsys.readouterr()
        assert "3 packages" in captured.out

    @patch('owrt.apk.run_command')
    def test_generate_index_custom_description(self, mock_run, repo_with_apk, temp_dir):
        """Test generate_index uses custom description."""
        arch_dir = repo_with_apk._get_arch_dir()
        arch_dir.mkdir(parents=True)
        (arch_dir / 'pkg1-1.0.apk').write_text("dummy")
        
        repo_with_apk.generate_index(description="Custom Repo")
        
        cmd = mock_run.call_args[0][0]
        assert '--description' in cmd
        desc_idx = cmd.index('--description')
        assert cmd[desc_idx + 1] == 'Custom Repo'


class TestAPKPackagerDevPackage:
    """Tests for APKPackager.create_dev_package method."""

    @pytest.fixture
    def temp_dir(self):
        """Create a temporary directory for tests."""
        tmpdir = tempfile.mkdtemp()
        yield Path(tmpdir)
        shutil.rmtree(tmpdir)

    @pytest.fixture
    def mock_config(self, temp_dir):
        """Create a mock config with temp directory."""
        return MockConfig(
            build_dir=temp_dir / 'build',
            staging_dir=temp_dir / 'staging',
            rootfs_dir=temp_dir / 'rootfs',
            toolchain_dir=temp_dir / 'toolchain',
        )

    @pytest.fixture
    def packager_with_apk(self, mock_config, temp_dir):
        """Create APKPackager with a mock apk binary."""
        apk_binary = temp_dir / 'apk'
        apk_binary.write_text("#!/bin/sh\nexit 0")
        apk_binary.chmod(0o755)
        return APKPackager(mock_config, apk_binary=apk_binary)

    def test_create_dev_package_no_apk(self, temp_dir):
        """Test create_dev_package returns None when no apk."""
        config = MockConfig(build_dir=temp_dir / 'empty')
        packager = APKPackager(config)
        
        result = packager.create_dev_package(
            "test-dev",
            {'version': '1.0', 'release': 1},
            temp_dir,
            'aarch64'
        )
        
        assert result is None

    @patch('owrt.apk.run_command')
    def test_create_dev_package_basic(self, mock_run, packager_with_apk, temp_dir):
        """Test basic dev package creation."""
        files_dir = temp_dir / 'dev-files'
        files_dir.mkdir()
        (files_dir / 'include').mkdir()
        (files_dir / 'include' / 'test.h').write_text("// header")
        
        config = {
            'version': '1.0.0',
            'release': 1,
            'description': 'Test development files',
            'license': 'MIT',
        }
        
        # Create expected output file
        packager_with_apk.output_dir.mkdir(parents=True)
        expected_apk = packager_with_apk.output_dir / 'test-dev-1.0.0-r1.apk'
        expected_apk.write_text("dummy")
        
        result = packager_with_apk.create_dev_package(
            "test-dev",
            config,
            files_dir,
            'aarch64'
        )
        
        assert mock_run.called
        assert result == expected_apk

    @patch('owrt.apk.run_command')
    def test_create_dev_package_sets_origin(self, mock_run, packager_with_apk, temp_dir):
        """Test dev package origin is set to base package name."""
        files_dir = temp_dir / 'dev-files'
        files_dir.mkdir()
        
        config = {'version': '1.0.0', 'release': 1}
        
        packager_with_apk.output_dir.mkdir(parents=True)
        expected_apk = packager_with_apk.output_dir / 'libubus-dev-1.0.0-r1.apk'
        expected_apk.write_text("dummy")
        
        packager_with_apk.create_dev_package(
            "libubus-dev",
            config,
            files_dir,
            'aarch64'
        )
        
        cmd = mock_run.call_args[0][0]
        cmd_str = ' '.join(cmd)
        # Origin should be libubus (without -dev)
        assert 'origin:libubus' in cmd_str

    @patch('owrt.apk.run_command')
    def test_create_dev_package_with_dependencies(self, mock_run, packager_with_apk, temp_dir):
        """Test dev package includes dependencies."""
        files_dir = temp_dir / 'dev-files'
        files_dir.mkdir()
        
        config = {
            'version': '1.0.0',
            'release': 1,
            'dependencies': {
                'runtime': ['libubox-dev', 'libjson-c-dev']
            }
        }
        
        packager_with_apk.output_dir.mkdir(parents=True)
        expected_apk = packager_with_apk.output_dir / 'test-dev-1.0.0-r1.apk'
        expected_apk.write_text("dummy")
        
        packager_with_apk.create_dev_package(
            "test-dev",
            config,
            files_dir,
            'aarch64'
        )
        
        cmd = mock_run.call_args[0][0]
        deps_found = False
        for arg in cmd:
            if arg.startswith('depends:'):
                deps_found = True
                assert 'libubox-dev' in arg
                assert 'libjson-c-dev' in arg
        assert deps_found
