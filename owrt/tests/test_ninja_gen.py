"""
Tests for ninja_gen.py - Ninja build file generator.
"""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
import tempfile
import shutil
import os

from owrt.ninja_gen import NinjaGenerator, NinjaRunner
from owrt.resolver import BuildPlan, BuildTarget
from owrt.config import Config


class MockConfig:
    """Mock Config for testing."""

    def __init__(self, name='test-target', arch='aarch64'):
        self.name = name
        self.arch = arch
        self.toolchain = {'gcc_version': '14.0.0', 'libc': 'musl'}
        self.kernel = {'full_version': '6.12.0'}

        # Create temp directories
        self._temp = Path(tempfile.mkdtemp())
        self.build_dir = self._temp / 'build'
        self.packages_dir = self._temp / 'packages' / arch
        self.packages_dir.mkdir(parents=True)

    def get_default_profile_name(self):
        return 'generic'

    def cleanup(self):
        shutil.rmtree(self._temp, ignore_errors=True)


class TestNinjaGenerator:
    """Tests for NinjaGenerator class."""

    @pytest.fixture
    def config(self):
        """Create mock config."""
        cfg = MockConfig()
        yield cfg
        cfg.cleanup()

    @pytest.fixture
    def simple_plan(self):
        """Create a simple build plan."""
        targets = {
            'toolchain': BuildTarget(
                name='toolchain',
                target_type='toolchain',
                deps=[],
            ),
            'kernel': BuildTarget(
                name='kernel',
                target_type='kernel',
                deps=['toolchain'],
            ),
            'libc': BuildTarget(
                name='libc',
                target_type='package',
                deps=['toolchain'],
            ),
            'busybox': BuildTarget(
                name='busybox',
                target_type='package',
                deps=['libc'],
            ),
        }
        build_order = ['toolchain', 'kernel', 'libc', 'busybox']
        to_build = {'toolchain', 'kernel', 'libc', 'busybox'}
        return BuildPlan(
            targets=targets,
            build_order=build_order,
            cached=set(),
            to_build=to_build,
        )

    def test_init(self, config):
        """Test NinjaGenerator initialization."""
        gen = NinjaGenerator(config)

        assert gen.config is config
        assert gen.build_dir == config.build_dir / config.name
        assert gen.ninja_file == gen.build_dir / 'build.ninja'

    def test_generate_creates_file(self, config, simple_plan):
        """Test generate creates ninja file."""
        gen = NinjaGenerator(config)

        ninja_file = gen.generate(simple_plan)

        assert ninja_file.exists()
        content = ninja_file.read_text()
        assert 'ninja_required_version' in content

    def test_generate_header(self, config):
        """Test header generation."""
        gen = NinjaGenerator(config)

        lines = gen._generate_header()

        assert any('Auto-generated' in line for line in lines)
        assert any(config.name in line for line in lines)

    def test_generate_variables(self, config):
        """Test variable generation."""
        gen = NinjaGenerator(config)

        lines = gen._generate_variables()

        assert any('builddir' in line for line in lines)
        assert any('target' in line for line in lines)
        assert any('python' in line for line in lines)

    def test_generate_rules(self, config):
        """Test rule generation."""
        gen = NinjaGenerator(config)

        lines = gen._generate_rules()

        # Check for expected rules
        assert any('rule toolchain' in line for line in lines)
        assert any('rule kernel' in line for line in lines)
        assert any('rule package' in line for line in lines)
        assert any('rule download' in line for line in lines)

    def test_generate_builds_includes_all_targets(self, config, simple_plan):
        """Test all targets are included in builds."""
        gen = NinjaGenerator(config)

        lines = gen._generate_builds(simple_plan)
        content = '\n'.join(lines)

        assert 'toolchain' in content
        assert 'kernel' in content
        assert 'busybox' in content

    def test_generate_aliases(self, config, simple_plan):
        """Test alias generation."""
        gen = NinjaGenerator(config)
        gen._plan_targets = simple_plan.targets

        lines = gen._generate_aliases(simple_plan)
        content = '\n'.join(lines)

        assert 'build all:' in content
        assert 'default all' in content

    def test_cached_targets_skipped(self, config, simple_plan):
        """Test cached targets are marked as such."""
        simple_plan.cached = {'busybox'}
        gen = NinjaGenerator(config)

        lines = gen._generate_builds(simple_plan)
        content = '\n'.join(lines)

        # Should have a comment about cached
        assert 'busybox: cached' in content

    def test_dependencies_in_build(self, config, simple_plan):
        """Test dependencies are included in build statements."""
        gen = NinjaGenerator(config)
        gen._effective_hashes = {'toolchain': 'abc123', 'libc': 'def456'}

        target = simple_plan.targets['libc']
        lines = gen._generate_target_build(target)
        content = '\n'.join(lines)

        assert 'toolchain.stamp' in content

    def test_pool_definitions(self, config):
        """Test pool definitions are included."""
        gen = NinjaGenerator(config)

        lines = gen._generate_rules()
        content = '\n'.join(lines)

        assert 'pool download_pool' in content
        assert 'pool package_pool' in content


class TestNinjaRunner:
    """Tests for NinjaRunner class."""

    @pytest.fixture
    def temp_dir(self):
        """Create temp directory with ninja file."""
        temp = Path(tempfile.mkdtemp())
        ninja_file = temp / 'build.ninja'
        ninja_file.write_text('''
rule touch
  command = touch $out

build test.stamp: touch
''')
        yield ninja_file
        shutil.rmtree(temp, ignore_errors=True)

    def test_init(self, temp_dir):
        """Test NinjaRunner initialization."""
        runner = NinjaRunner(temp_dir, verbose=True, jobs=4)

        assert runner.ninja_file == temp_dir
        assert runner.verbose is True
        assert runner.jobs == 4

    def test_init_default_jobs(self, temp_dir):
        """Test default jobs is cpu_count."""
        runner = NinjaRunner(temp_dir)

        assert runner.jobs == os.cpu_count()

    def test_run_builds_command(self, temp_dir):
        """Test run constructs correct command."""
        runner = NinjaRunner(temp_dir, verbose=True, jobs=4)

        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            runner.run()

        call_args = mock_run.call_args[0][0]
        assert 'ninja' in call_args
        assert '-f' in call_args
        assert '-j' in call_args
        assert '4' in call_args
        assert '-v' in call_args

    def test_run_with_targets(self, temp_dir):
        """Test run with specific targets."""
        runner = NinjaRunner(temp_dir)

        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            runner.run(targets=['packages', 'images'])

        call_args = mock_run.call_args[0][0]
        assert 'packages' in call_args
        assert 'images' in call_args

    def test_run_returns_success(self, temp_dir):
        """Test run returns True on success."""
        runner = NinjaRunner(temp_dir)

        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = runner.run()

        assert result is True

    def test_run_returns_failure(self, temp_dir):
        """Test run returns False on failure."""
        runner = NinjaRunner(temp_dir)

        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=1)
            result = runner.run()

        assert result is False

    def test_clean(self, temp_dir):
        """Test clean command."""
        runner = NinjaRunner(temp_dir)

        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = runner.clean()

        call_args = mock_run.call_args[0][0]
        assert 'clean' in call_args
        assert result is True

    def test_graph(self, temp_dir):
        """Test graph generation."""
        runner = NinjaRunner(temp_dir)
        output = temp_dir.parent / 'graph.dot'

        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout='digraph { a -> b }'
            )
            result = runner.graph(output)

        assert result is True
        assert output.exists()
        assert 'digraph' in output.read_text()


class TestNinjaGeneratorIntegration:
    """Integration tests with real config."""

    def test_generate_with_real_target(self):
        """Test generating ninja file for real target."""
        config = Config.load_target('armsr-armv8')

        # Create a simple plan
        targets = {
            'toolchain': BuildTarget(
                name='toolchain',
                target_type='toolchain',
                deps=[],
            ),
        }
        plan = BuildPlan(
            targets=targets,
            build_order=['toolchain'],
            cached=set(),
            to_build={'toolchain'},
        )

        # Use temp directory
        temp_dir = Path(tempfile.mkdtemp())
        try:
            gen = NinjaGenerator(config, build_dir=temp_dir / config.name)
            ninja_file = gen.generate(plan)

            assert ninja_file.exists()
            content = ninja_file.read_text()

            # Verify content
            assert 'armsr-armv8' in content
            assert 'aarch64' in content
            assert 'rule toolchain' in content
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
