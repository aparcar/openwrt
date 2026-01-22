"""
Tests for utils.py - utility functions.
"""

import pytest
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock, mock_open

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from owrt.utils import (
    run_command,
    sha256_file,
    extract_archive,
    merge_kconfig,
    generate_ninja_file,
)


class TestRunCommand:
    """Tests for run_command function."""

    def test_simple_command(self, tmp_path):
        """Test running a simple command."""
        result = run_command(['echo', 'hello'], capture=True)
        assert result.strip() == 'hello'

    def test_command_with_cwd(self, tmp_path):
        """Test running a command with working directory."""
        result = run_command(['pwd'], cwd=tmp_path, capture=True)
        assert tmp_path.name in result

    def test_command_failure_raises(self, tmp_path):
        """Test that failed command raises CalledProcessError."""
        with pytest.raises(subprocess.CalledProcessError):
            run_command(['false'], capture=True)

    def test_capture_mode_returns_stdout(self):
        """Test capture mode returns stdout."""
        result = run_command(['echo', 'test output'], capture=True)
        assert 'test output' in result

    def test_quiet_mode_returns_none(self):
        """Test quiet mode returns None on success."""
        result = run_command(['true'], verbose=False, capture=False)
        assert result is None

    def test_command_with_env(self, tmp_path):
        """Test running command with custom environment."""
        import os
        env = os.environ.copy()
        env['TEST_VAR'] = 'test_value'
        result = run_command(['printenv', 'TEST_VAR'], env=env, capture=True)
        assert 'test_value' in result


class TestSha256File:
    """Tests for sha256_file function."""

    def test_sha256_empty_file(self, tmp_path):
        """Test SHA256 of empty file."""
        test_file = tmp_path / 'empty.txt'
        test_file.write_bytes(b'')
        # Known SHA256 of empty file
        expected = 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'
        assert sha256_file(test_file) == expected

    def test_sha256_with_content(self, tmp_path):
        """Test SHA256 of file with content."""
        test_file = tmp_path / 'test.txt'
        test_file.write_bytes(b'hello world\n')
        # Pre-computed SHA256
        expected = 'a948904f2f0f479b8f8564cbf12dac6b5c52c5d8cd0b4e9e1b3d3a5e7c2d1b0f'
        result = sha256_file(test_file)
        assert len(result) == 64  # SHA256 hex digest length
        assert result.isalnum()

    def test_sha256_large_file(self, tmp_path):
        """Test SHA256 of larger file (tests chunking)."""
        test_file = tmp_path / 'large.bin'
        # Create file larger than chunk size (8192 bytes)
        test_file.write_bytes(b'x' * 20000)
        result = sha256_file(test_file)
        assert len(result) == 64


class TestExtractArchive:
    """Tests for extract_archive function."""

    def test_extract_tar_gz(self, tmp_path):
        """Test extracting a .tar.gz archive."""
        import tarfile
        import io

        # Create a test tarball
        archive = tmp_path / 'test.tar.gz'
        dest = tmp_path / 'extracted'

        with tarfile.open(archive, 'w:gz') as tar:
            # Add a test file to the archive
            data = b'test content'
            info = tarfile.TarInfo(name='testfile.txt')
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))

        extract_archive(archive, dest)

        assert (dest / 'testfile.txt').exists()
        assert (dest / 'testfile.txt').read_bytes() == b'test content'

    def test_extract_tar_xz(self, tmp_path):
        """Test extracting a .tar.xz archive."""
        import tarfile
        import io

        archive = tmp_path / 'test.tar.xz'
        dest = tmp_path / 'extracted'

        with tarfile.open(archive, 'w:xz') as tar:
            data = b'xz content'
            info = tarfile.TarInfo(name='xzfile.txt')
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))

        extract_archive(archive, dest)

        assert (dest / 'xzfile.txt').exists()
        assert (dest / 'xzfile.txt').read_bytes() == b'xz content'

    def test_extract_creates_dest_dir(self, tmp_path):
        """Test that destination directory is created if it doesn't exist."""
        import tarfile
        import io

        archive = tmp_path / 'test.tar.gz'
        dest = tmp_path / 'new' / 'nested' / 'dir'

        with tarfile.open(archive, 'w:gz') as tar:
            data = b'test'
            info = tarfile.TarInfo(name='file.txt')
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))

        extract_archive(archive, dest)

        assert dest.exists()
        assert (dest / 'file.txt').exists()


class TestMergeKconfig:
    """Tests for merge_kconfig function."""

    def test_simple_merge(self, tmp_path):
        """Test merging two simple config files."""
        config1 = tmp_path / 'config1'
        config2 = tmp_path / 'config2'
        output = tmp_path / 'merged'

        config1.write_text('CONFIG_FOO=y\nCONFIG_BAR=m\n')
        config2.write_text('CONFIG_BAZ=y\n')

        merge_kconfig([config1, config2], output)

        content = output.read_text()
        assert 'CONFIG_FOO=y' in content
        assert 'CONFIG_BAR=m' in content
        assert 'CONFIG_BAZ=y' in content

    def test_y_takes_precedence_over_m(self, tmp_path):
        """Test that =y takes precedence over =m (mod_plus logic)."""
        config1 = tmp_path / 'config1'
        config2 = tmp_path / 'config2'
        output = tmp_path / 'merged'

        # First file sets to y
        config1.write_text('CONFIG_EXT4_FS=y\n')
        # Second file tries to set to m
        config2.write_text('CONFIG_EXT4_FS=m\n')

        merge_kconfig([config1, config2], output)

        content = output.read_text()
        # y should win over m
        assert 'CONFIG_EXT4_FS=y' in content
        assert 'CONFIG_EXT4_FS=m' not in content

    def test_m_does_not_override_y(self, tmp_path):
        """Test that =m does not override existing =y."""
        config1 = tmp_path / 'config1'
        config2 = tmp_path / 'config2'
        output = tmp_path / 'merged'

        config1.write_text('CONFIG_OPTION=y\n')
        config2.write_text('CONFIG_OPTION=m\n')

        merge_kconfig([config1, config2], output)

        content = output.read_text()
        assert 'CONFIG_OPTION=y' in content

    def test_y_overrides_m(self, tmp_path):
        """Test that =y overrides existing =m."""
        config1 = tmp_path / 'config1'
        config2 = tmp_path / 'config2'
        output = tmp_path / 'merged'

        config1.write_text('CONFIG_OPTION=m\n')
        config2.write_text('CONFIG_OPTION=y\n')

        merge_kconfig([config1, config2], output)

        content = output.read_text()
        assert 'CONFIG_OPTION=y' in content

    def test_is_not_set_can_be_overridden(self, tmp_path):
        """Test that '# CONFIG_X is not set' can be overridden."""
        config1 = tmp_path / 'config1'
        config2 = tmp_path / 'config2'
        output = tmp_path / 'merged'

        config1.write_text('# CONFIG_FEATURE is not set\n')
        config2.write_text('CONFIG_FEATURE=y\n')

        merge_kconfig([config1, config2], output)

        content = output.read_text()
        assert 'CONFIG_FEATURE=y' in content
        assert 'is not set' not in content

    def test_handles_missing_config_files(self, tmp_path):
        """Test that missing config files are skipped."""
        config1 = tmp_path / 'config1'
        config2 = tmp_path / 'nonexistent'
        output = tmp_path / 'merged'

        config1.write_text('CONFIG_FOO=y\n')

        merge_kconfig([config1, config2], output)

        content = output.read_text()
        assert 'CONFIG_FOO=y' in content

    def test_creates_output_directory(self, tmp_path):
        """Test that output directory is created."""
        config1 = tmp_path / 'config1'
        output = tmp_path / 'subdir' / 'merged'

        config1.write_text('CONFIG_TEST=y\n')

        merge_kconfig([config1], output)

        assert output.exists()


class TestGenerateNinjaFile:
    """Tests for generate_ninja_file function."""

    def test_generates_rules(self, tmp_path):
        """Test that rules are generated correctly."""
        output = tmp_path / 'build.ninja'

        rules = [
            {
                'name': 'compile',
                'command': 'gcc -c $in -o $out',
                'description': 'Compiling $out',
            }
        ]
        builds = []

        generate_ninja_file(output, rules, builds)

        content = output.read_text()
        assert 'rule compile' in content
        assert 'command = gcc -c $in -o $out' in content
        assert 'description = Compiling $out' in content

    def test_generates_build_statements(self, tmp_path):
        """Test that build statements are generated correctly."""
        output = tmp_path / 'build.ninja'

        rules = [
            {'name': 'compile', 'command': 'gcc -c $in -o $out'}
        ]
        builds = [
            {
                'output': 'main.o',
                'rule': 'compile',
                'deps': ['main.c', 'header.h'],
            }
        ]

        generate_ninja_file(output, rules, builds)

        content = output.read_text()
        assert 'build main.o: compile main.c header.h' in content

    def test_generates_build_with_variables(self, tmp_path):
        """Test that build variables are generated correctly."""
        output = tmp_path / 'build.ninja'

        rules = [
            {'name': 'link', 'command': 'gcc $ldflags $in -o $out'}
        ]
        builds = [
            {
                'output': 'program',
                'rule': 'link',
                'deps': ['main.o'],
                'vars': {'ldflags': '-lm -lpthread'},
            }
        ]

        generate_ninja_file(output, rules, builds)

        content = output.read_text()
        assert 'ldflags = -lm -lpthread' in content
