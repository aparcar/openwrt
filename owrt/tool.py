"""
Host tool builder - builds tools that run on the build host.

These tools are used during the build process for:
- Package creation (apk-tools)
- Image generation
- Other host-side utilities

Unlike packages which are cross-compiled for the target,
host tools are built with the native compiler.
"""

import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional, List, Dict, Set

import yaml

from .utils import run_command, download_file, extract_archive, apply_patches


class ToolConfig:
    """Configuration for a host tool."""

    def __init__(self, data: dict, tool_dir: Path):
        self.data = data
        self.tool_dir = tool_dir

        self.name = data.get('name', '')
        self.version = data.get('version', '')
        self.license = data.get('license', '')
        self.source = data.get('source', {})
        self.dependencies = data.get('dependencies', [])
        self.build = data.get('build', {})
        self.metadata = data.get('metadata', {})

    @property
    def is_core_tool(self) -> bool:
        """Check if this is a core bootstrap tool."""
        return self.metadata.get('core_tool', False)

    @property
    def build_system(self) -> str:
        return self.build.get('system', 'autotools')

    @classmethod
    def find_tool(cls, name: str) -> Optional['ToolConfig']:
        """Find a tool by name in owrt/tools/."""
        owrt_dir = Path(__file__).parent
        tools_dir = owrt_dir / 'tools'

        tool_dir = tools_dir / name
        tool_yaml = tool_dir / 'tool.yaml'

        if tool_yaml.exists():
            with open(tool_yaml) as f:
                data = yaml.safe_load(f)
            return cls(data, tool_dir)

        return None

    @classmethod
    def list_tools(cls) -> List[str]:
        """List all available host tools."""
        owrt_dir = Path(__file__).parent
        tools_dir = owrt_dir / 'tools'
        tools = []

        if tools_dir.exists():
            for tool_dir in sorted(tools_dir.iterdir()):
                if (tool_dir / 'tool.yaml').exists():
                    tools.append(tool_dir.name)

        return tools


class ToolBuilder:
    """Builds host tools for the build system."""

    def __init__(self, build_dir: Path, verbose: bool = False, jobs: Optional[int] = None):
        self.verbose = verbose
        self.jobs = jobs or os.cpu_count()

        # Paths
        self.build_dir = build_dir
        self.tools_dir = build_dir / 'host-tools'
        self.staging_dir = build_dir / 'host-staging'
        self.dl_dir = build_dir / 'dl'
        self.stamp_dir = self.tools_dir / 'stamp'

        # Tool cache
        self._tools: Dict[str, ToolConfig] = {}
        self._built: Set[str] = set()

    def clean(self):
        """Clean all tool build artifacts."""
        if self.tools_dir.exists():
            shutil.rmtree(self.tools_dir)
        if self.staging_dir.exists():
            shutil.rmtree(self.staging_dir)

    def build_tools(self, tools: List[str], force: bool = False):
        """Build a list of host tools with dependency resolution."""
        # Create directories
        self.tools_dir.mkdir(parents=True, exist_ok=True)
        self.staging_dir.mkdir(parents=True, exist_ok=True)
        self.stamp_dir.mkdir(parents=True, exist_ok=True)
        (self.staging_dir / 'bin').mkdir(parents=True, exist_ok=True)
        (self.staging_dir / 'lib').mkdir(parents=True, exist_ok=True)
        (self.staging_dir / 'include').mkdir(parents=True, exist_ok=True)
        (self.staging_dir / 'lib' / 'pkgconfig').mkdir(parents=True, exist_ok=True)

        # Resolve build order
        build_order = self._resolve_dependencies(tools)
        print(f"  Building {len(build_order)} host tools: {', '.join(build_order)}")

        # Build each tool
        for tool_name in build_order:
            self._build_tool(tool_name, force=force)

    def _resolve_dependencies(self, tools: List[str]) -> List[str]:
        """Resolve tool dependencies and return build order."""
        resolved = []
        seen = set()

        def resolve(name: str):
            if name in seen:
                return
            seen.add(name)

            tool = self._load_tool(name)
            if tool:
                for dep in tool.dependencies:
                    resolve(dep)

            resolved.append(name)

        for tool in tools:
            resolve(tool)

        return resolved

    def _load_tool(self, name: str) -> Optional[ToolConfig]:
        """Load a tool configuration."""
        if name in self._tools:
            return self._tools[name]

        tool = ToolConfig.find_tool(name)
        if tool:
            self._tools[name] = tool

        return tool

    def _build_tool(self, name: str, force: bool = False):
        """Build a single host tool."""
        stamp = self.stamp_dir / f'{name}.built'
        if not force and stamp.exists():
            print(f"    {name}: already built, skipping")
            return

        print(f"    {name}: building...")

        tool = self._load_tool(name)
        if not tool:
            print(f"    {name}: ERROR - tool definition not found")
            return

        # Create build directory
        build_dir = self.tools_dir / name / 'build'
        build_dir.mkdir(parents=True, exist_ok=True)

        # Download and extract source
        src_dir = self._prepare_source(tool, build_dir.parent)

        # Build using appropriate build system
        build_system = tool.build_system
        env = self._get_build_env(tool)

        if build_system == 'autotools':
            self._build_autotools(tool, src_dir, build_dir, env)
        elif build_system == 'cmake':
            self._build_cmake(tool, src_dir, build_dir, env)
        elif build_system == 'meson':
            self._build_meson(tool, src_dir, build_dir, env)
        elif build_system == 'make':
            self._build_make(tool, src_dir, build_dir, env)
        elif build_system == 'custom':
            self._build_custom(tool, src_dir, build_dir, env)
        else:
            print(f"      Unknown build system: {build_system}")
            return

        stamp.touch()
        self._built.add(name)

    def _prepare_source(self, tool: ToolConfig, tool_dir: Path) -> Path:
        """Download and extract tool source."""
        source = tool.source
        src_type = source.get('type', 'tarball')
        src_dir = tool_dir / 'src'

        if src_dir.exists():
            return src_dir

        # Ensure download directory exists
        self.dl_dir.mkdir(parents=True, exist_ok=True)

        if src_type == 'tarball':
            url = source.get('url', '')
            # Variable substitution
            url = url.replace('${name}', tool.name).replace('${version}', tool.version)

            filename = url.split('/')[-1]
            tarball = self.dl_dir / filename

            if not tarball.exists():
                expected_hash = source.get('sha256')
                mirrors = source.get('mirrors', [])

                # Try primary URL first, then mirrors
                urls = [url] + mirrors
                for try_url in urls:
                    try_url = try_url.replace('${name}', tool.name).replace('${version}', tool.version)
                    try:
                        download_file(try_url, tarball, expected_hash)
                        break
                    except Exception as e:
                        if try_url == urls[-1]:
                            raise
                        print(f"      Mirror failed: {try_url}, trying next...")

            # Extract
            extract_archive(tarball, tool_dir)
            # Find extracted directory
            dirs = [d for d in tool_dir.iterdir() if d.is_dir() and d.name not in ('build', 'src')]
            if dirs:
                dirs[0].rename(src_dir)

        elif src_type == 'git':
            url = source.get('url', '')
            version = source.get('version', 'HEAD')

            run_command(['git', 'clone', '--depth=1', url, str(src_dir)],
                       verbose=self.verbose)
            if version and version != 'HEAD':
                run_command(['git', 'fetch', '--depth=1', 'origin', version],
                           cwd=src_dir, verbose=self.verbose)
                run_command(['git', 'checkout', version],
                           cwd=src_dir, verbose=self.verbose)

        # Apply patches
        patch_dir_name = tool.build.get('patch_dir', 'patches')
        patches_path = tool.tool_dir / patch_dir_name
        if patches_path.exists():
            apply_patches(src_dir, patches_path, verbose=self.verbose)

        return src_dir

    def _get_build_env(self, tool: ToolConfig) -> dict:
        """Get environment for host tool builds."""
        env = os.environ.copy()

        # Use staging directory for installed tools
        env['STAGING_DIR'] = str(self.staging_dir)
        env['PATH'] = f"{self.staging_dir}/bin:{env.get('PATH', '')}"
        # Include both staging and system pkg-config paths for host tools
        env['PKG_CONFIG_PATH'] = f"{self.staging_dir}/lib/pkgconfig:/usr/lib/pkgconfig:/usr/lib/x86_64-linux-gnu/pkgconfig:/usr/share/pkgconfig"
        # Don't set PKG_CONFIG_LIBDIR as it overrides default search paths

        # Standard flags pointing to staging
        env['CFLAGS'] = f"-I{self.staging_dir}/include -O2"
        env['CXXFLAGS'] = f"-I{self.staging_dir}/include -O2"
        env['LDFLAGS'] = f"-L{self.staging_dir}/lib"
        env['CPPFLAGS'] = f"-I{self.staging_dir}/include"

        # For lua.pc to be found
        env['LUA_CFLAGS'] = f"-I{self.staging_dir}/include"
        env['LUA_LIBS'] = f"-L{self.staging_dir}/lib -llua -lm"

        # Host compiler
        env['CC'] = os.environ.get('CC', 'gcc')
        env['CXX'] = os.environ.get('CXX', 'g++')

        # Add tool-specific environment
        for key, value in tool.build.get('env', {}).items():
            # Variable substitution
            value = str(value).replace('${version}', tool.version)
            env[key] = value

        return env

    def _build_autotools(self, tool: ToolConfig, src_dir: Path, build_dir: Path, env: dict):
        """Build with autotools."""
        configure = src_dir / 'configure'

        # Check if autoreconf should be forced (e.g., patches modified configure.ac)
        force_autoreconf = tool.build.get('autoreconf', False)

        if force_autoreconf or not configure.exists():
            if (src_dir / 'configure.ac').exists():
                run_command(['autoreconf', '-fi'], cwd=src_dir, env=env, verbose=self.verbose)

        if not configure.exists():
            print(f"      No configure script found")
            return

        configure_args = [
            str(configure),
            f'--prefix={self.staging_dir}',
        ] + tool.build.get('configure_args', [])

        # Run configure and make from source directory (in-tree build)
        # This matches OpenWrt's approach and handles relative include paths correctly
        run_command(configure_args, cwd=src_dir, env=env, verbose=self.verbose)
        run_command(['make', f'-j{self.jobs}'], cwd=src_dir, env=env, verbose=self.verbose)
        run_command(['make', 'install'], cwd=src_dir, env=env, verbose=self.verbose)

    def _build_cmake(self, tool: ToolConfig, src_dir: Path, build_dir: Path, env: dict):
        """Build with CMake."""
        cmake_args = [
            'cmake',
            str(src_dir),
            '-DCMAKE_BUILD_TYPE=Release',
            f'-DCMAKE_INSTALL_PREFIX={self.staging_dir}',
            f'-DCMAKE_PREFIX_PATH={self.staging_dir}',
        ] + tool.build.get('configure_args', [])

        run_command(cmake_args, cwd=build_dir, env=env, verbose=self.verbose)
        run_command(['make', f'-j{self.jobs}'], cwd=build_dir, env=env, verbose=self.verbose)
        run_command(['make', 'install'], cwd=build_dir, env=env, verbose=self.verbose)

    def _build_meson(self, tool: ToolConfig, src_dir: Path, build_dir: Path, env: dict):
        """Build with Meson."""
        # For host builds, we don't need a cross file
        # Use direct prefix to staging_dir (no DESTDIR needed for host tools)
        meson_args = [
            'meson', 'setup',
            str(build_dir),
            str(src_dir),
            f'--prefix={self.staging_dir}',
            '--buildtype=release',
        ] + tool.build.get('configure_args', [])

        run_command(meson_args, env=env, verbose=self.verbose)
        run_command(['ninja', '-C', str(build_dir), f'-j{self.jobs}'],
                   env=env, verbose=self.verbose)

        # Direct install to staging_dir (prefix is already set correctly)
        run_command(['ninja', '-C', str(build_dir), 'install'],
                   env=env, verbose=self.verbose)

    def _build_make(self, tool: ToolConfig, src_dir: Path, build_dir: Path, env: dict):
        """Build with plain make."""
        make_args = ['make', f'-j{self.jobs}'] + tool.build.get('make_args', [])
        run_command(make_args, cwd=src_dir, env=env, verbose=self.verbose)

        install_args = ['make', f'INSTALL_TOP={self.staging_dir}', 'install']
        run_command(install_args, cwd=src_dir, env=env, verbose=self.verbose)

    def _build_custom(self, tool: ToolConfig, src_dir: Path, build_dir: Path, env: dict):
        """Build with custom scripts."""
        env = env.copy()
        env['JOBS'] = str(self.jobs)
        env['DESTDIR'] = str(self.staging_dir)

        # Configure
        configure_script = tool.build.get('configure_script', '')
        if configure_script:
            run_command(['sh', '-c', configure_script], cwd=src_dir, env=env, verbose=self.verbose)

        # Compile
        compile_script = tool.build.get('compile_script', '')
        if compile_script:
            run_command(['sh', '-c', compile_script], cwd=src_dir, env=env, verbose=self.verbose)

        # Install
        install_script = tool.build.get('install_script', '')
        if install_script:
            run_command(['sh', '-c', install_script], cwd=src_dir, env=env, verbose=self.verbose)

    def get_staging_dir(self) -> Path:
        """Get host staging directory path."""
        return self.staging_dir

    def get_apk_binary(self) -> Optional[Path]:
        """Get path to the apk binary if built."""
        apk_path = self.staging_dir / 'bin' / 'apk'
        if apk_path.exists():
            return apk_path
        # Try usr/bin as fallback
        apk_path = self.staging_dir / 'usr' / 'bin' / 'apk'
        if apk_path.exists():
            return apk_path
        return None
