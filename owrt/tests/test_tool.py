"""
Tests for tool.py - Host tool builder.
"""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
import tempfile
import shutil

from owrt.tool import ToolConfig, ToolBuilder


class TestToolConfig:
    """Tests for ToolConfig class."""

    def test_init_with_data(self):
        """Test ToolConfig initialization with data dict."""
        data = {
            'name': 'test-tool',
            'version': '1.0.0',
            'license': 'MIT',
            'source': {'type': 'tarball', 'url': 'https://example.com/test.tar.gz'},
            'dependencies': ['dep1', 'dep2'],
            'build': {'system': 'cmake'},
            'metadata': {'title': 'Test Tool'},
        }
        tool_dir = Path('/tmp/test-tool')

        config = ToolConfig(data, tool_dir)

        assert config.name == 'test-tool'
        assert config.version == '1.0.0'
        assert config.license == 'MIT'
        assert config.source['type'] == 'tarball'
        assert config.dependencies == ['dep1', 'dep2']
        assert config.build_system == 'cmake'
        assert config.metadata['title'] == 'Test Tool'
        assert config.tool_dir == tool_dir

    def test_init_with_defaults(self):
        """Test ToolConfig with minimal data uses defaults."""
        data = {}
        tool_dir = Path('/tmp/empty-tool')

        config = ToolConfig(data, tool_dir)

        assert config.name == ''
        assert config.version == ''
        assert config.license == ''
        assert config.dependencies == []
        assert config.build_system == 'autotools'  # default

    def test_build_system_default(self):
        """Test build_system defaults to autotools."""
        data = {'name': 'test', 'build': {}}
        config = ToolConfig(data, Path('/tmp'))

        assert config.build_system == 'autotools'

    def test_build_system_custom(self):
        """Test build_system can be set to different values."""
        for system in ['cmake', 'meson', 'make', 'custom']:
            data = {'name': 'test', 'build': {'system': system}}
            config = ToolConfig(data, Path('/tmp'))
            assert config.build_system == system

    def test_list_tools(self):
        """Test that list_tools returns available tools."""
        tools = ToolConfig.list_tools()

        # Should find tools in owrt/tools/
        assert isinstance(tools, list)
        # We know apk exists
        assert 'apk' in tools

    def test_find_tool_exists(self):
        """Test finding an existing tool."""
        tool = ToolConfig.find_tool('apk')

        assert tool is not None
        assert tool.name == 'apk'
        assert tool.version != ''

    def test_find_tool_not_exists(self):
        """Test finding a non-existent tool returns None."""
        tool = ToolConfig.find_tool('nonexistent-tool-xyz')

        assert tool is None


class TestToolBuilder:
    """Tests for ToolBuilder class."""

    @pytest.fixture
    def temp_build_dir(self):
        """Create a temporary build directory."""
        temp_dir = Path(tempfile.mkdtemp())
        yield temp_dir
        shutil.rmtree(temp_dir, ignore_errors=True)

    def test_init(self, temp_build_dir):
        """Test ToolBuilder initialization."""
        builder = ToolBuilder(temp_build_dir, verbose=True, jobs=4)

        assert builder.build_dir == temp_build_dir
        assert builder.tools_dir == temp_build_dir / 'host-tools'
        assert builder.staging_dir == temp_build_dir / 'host-staging'
        assert builder.dl_dir == temp_build_dir / 'dl'
        assert builder.verbose is True
        assert builder.jobs == 4

    def test_init_default_jobs(self, temp_build_dir):
        """Test ToolBuilder uses cpu_count for default jobs."""
        import os
        builder = ToolBuilder(temp_build_dir)

        assert builder.jobs == os.cpu_count()

    def test_clean(self, temp_build_dir):
        """Test clean removes build directories."""
        builder = ToolBuilder(temp_build_dir)

        # Create directories
        builder.tools_dir.mkdir(parents=True)
        builder.staging_dir.mkdir(parents=True)
        (builder.tools_dir / 'test-file').touch()
        (builder.staging_dir / 'test-file').touch()

        assert builder.tools_dir.exists()
        assert builder.staging_dir.exists()

        builder.clean()

        assert not builder.tools_dir.exists()
        assert not builder.staging_dir.exists()

    def test_clean_nonexistent_dirs(self, temp_build_dir):
        """Test clean handles non-existent directories gracefully."""
        builder = ToolBuilder(temp_build_dir)

        # Should not raise
        builder.clean()

    def test_resolve_dependencies_simple(self, temp_build_dir):
        """Test dependency resolution with no dependencies."""
        builder = ToolBuilder(temp_build_dir)

        # Mock tool loading to return tools with no deps
        with patch.object(builder, '_load_tool') as mock_load:
            mock_tool = MagicMock()
            mock_tool.dependencies = []
            mock_load.return_value = mock_tool

            order = builder._resolve_dependencies(['tool1', 'tool2'])

        assert order == ['tool1', 'tool2']

    def test_resolve_dependencies_with_deps(self, temp_build_dir):
        """Test dependency resolution orders dependencies first."""
        builder = ToolBuilder(temp_build_dir)

        # Create mock tools with dependencies
        tool_a = MagicMock()
        tool_a.dependencies = ['tool_b']  # A depends on B

        tool_b = MagicMock()
        tool_b.dependencies = []

        def mock_load(name):
            if name == 'tool_a':
                return tool_a
            elif name == 'tool_b':
                return tool_b
            return None

        with patch.object(builder, '_load_tool', side_effect=mock_load):
            order = builder._resolve_dependencies(['tool_a'])

        # tool_b should come before tool_a
        assert order.index('tool_b') < order.index('tool_a')

    def test_resolve_dependencies_no_duplicates(self, temp_build_dir):
        """Test dependency resolution doesn't include duplicates."""
        builder = ToolBuilder(temp_build_dir)

        # A and B both depend on C
        tool_a = MagicMock()
        tool_a.dependencies = ['tool_c']

        tool_b = MagicMock()
        tool_b.dependencies = ['tool_c']

        tool_c = MagicMock()
        tool_c.dependencies = []

        def mock_load(name):
            return {'tool_a': tool_a, 'tool_b': tool_b, 'tool_c': tool_c}.get(name)

        with patch.object(builder, '_load_tool', side_effect=mock_load):
            order = builder._resolve_dependencies(['tool_a', 'tool_b'])

        # tool_c should appear only once
        assert order.count('tool_c') == 1

    def test_load_tool_caches(self, temp_build_dir):
        """Test that _load_tool caches loaded tools."""
        builder = ToolBuilder(temp_build_dir)

        with patch.object(ToolConfig, 'find_tool') as mock_find:
            mock_tool = MagicMock()
            mock_find.return_value = mock_tool

            # Load twice
            result1 = builder._load_tool('test-tool')
            result2 = builder._load_tool('test-tool')

        # Should only call find_tool once (cached)
        assert mock_find.call_count == 1
        assert result1 is result2

    def test_get_staging_dir(self, temp_build_dir):
        """Test get_staging_dir returns correct path."""
        builder = ToolBuilder(temp_build_dir)

        assert builder.get_staging_dir() == temp_build_dir / 'host-staging'

    def test_get_apk_binary_not_built(self, temp_build_dir):
        """Test get_apk_binary returns None when not built."""
        builder = ToolBuilder(temp_build_dir)

        assert builder.get_apk_binary() is None

    def test_get_apk_binary_in_bin(self, temp_build_dir):
        """Test get_apk_binary finds apk in bin/."""
        builder = ToolBuilder(temp_build_dir)
        apk_path = builder.staging_dir / 'bin' / 'apk'
        apk_path.parent.mkdir(parents=True)
        apk_path.touch()

        assert builder.get_apk_binary() == apk_path

    def test_get_apk_binary_in_usr_bin(self, temp_build_dir):
        """Test get_apk_binary finds apk in usr/bin/."""
        builder = ToolBuilder(temp_build_dir)
        apk_path = builder.staging_dir / 'usr' / 'bin' / 'apk'
        apk_path.parent.mkdir(parents=True)
        apk_path.touch()

        assert builder.get_apk_binary() == apk_path

    def test_build_tools_creates_directories(self, temp_build_dir):
        """Test build_tools creates necessary directories."""
        builder = ToolBuilder(temp_build_dir)

        # Mock _build_tool to avoid actual building
        with patch.object(builder, '_build_tool'):
            with patch.object(builder, '_resolve_dependencies', return_value=[]):
                builder.build_tools([])

        assert builder.tools_dir.exists()
        assert builder.staging_dir.exists()
        assert builder.stamp_dir.exists()
        assert (builder.staging_dir / 'bin').exists()
        assert (builder.staging_dir / 'lib').exists()
        assert (builder.staging_dir / 'include').exists()
        assert (builder.staging_dir / 'lib' / 'pkgconfig').exists()


class TestToolBuilderBuildEnv:
    """Tests for ToolBuilder build environment setup."""

    @pytest.fixture
    def builder(self):
        """Create a ToolBuilder with temp directory."""
        temp_dir = Path(tempfile.mkdtemp())
        builder = ToolBuilder(temp_dir, verbose=False)
        yield builder
        shutil.rmtree(temp_dir, ignore_errors=True)

    def test_get_build_env_includes_staging(self, builder):
        """Test build environment includes staging directory paths."""
        tool = MagicMock()
        tool.build = {'env': {}}
        tool.version = '1.0.0'

        env = builder._get_build_env(tool)

        assert str(builder.staging_dir) in env['PATH']
        assert str(builder.staging_dir / 'include') in env['CFLAGS']
        assert str(builder.staging_dir / 'lib') in env['LDFLAGS']

    def test_get_build_env_includes_pkgconfig(self, builder):
        """Test build environment includes pkg-config path."""
        tool = MagicMock()
        tool.build = {'env': {}}
        tool.version = '1.0.0'

        env = builder._get_build_env(tool)

        assert str(builder.staging_dir / 'lib' / 'pkgconfig') in env['PKG_CONFIG_PATH']

    def test_get_build_env_tool_specific(self, builder):
        """Test tool-specific environment variables are added."""
        tool = MagicMock()
        tool.build = {'env': {'CUSTOM_VAR': 'custom_value'}}
        tool.version = '1.0.0'

        env = builder._get_build_env(tool)

        assert env['CUSTOM_VAR'] == 'custom_value'

    def test_get_build_env_version_substitution(self, builder):
        """Test ${version} is substituted in env values."""
        tool = MagicMock()
        tool.build = {'env': {'VERSION_VAR': 'v${version}'}}
        tool.version = '2.0.0'

        env = builder._get_build_env(tool)

        assert env['VERSION_VAR'] == 'v2.0.0'
