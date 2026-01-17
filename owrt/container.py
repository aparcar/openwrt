"""
Package build isolation with per-package staging directories.

Provides isolation for package builds:
- Each package has its own staging directory
- Dependencies are installed via APK into the package's staging
- Packages only see their explicitly declared dependencies
- Build environment uses paths pointing to package-specific staging

All packages are built with isolation for consistent, secure builds.
"""

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class Mount:
    """A container mount specification."""
    source: Path
    target: str
    readonly: bool = False

    def to_docker_arg(self) -> List[str]:
        """Convert to Docker -v argument."""
        mode = ':ro' if self.readonly else ''
        return ['-v', f'{self.source}:{self.target}{mode}']


@dataclass
class ContainerConfig:
    """Configuration for a container run."""
    image: str
    mounts: List[Mount] = field(default_factory=list)
    env: Dict[str, str] = field(default_factory=dict)
    workdir: str = '/build'
    user: Optional[str] = None  # "uid:gid" format
    network: str = 'none'  # Disable network by default for security


class ContainerRuntime:
    """Docker container runtime for build operations."""

    def __init__(self, verbose: bool = False):
        self.verbose = verbose
        self._docker_available: Optional[bool] = None

    def is_available(self) -> bool:
        """Check if Docker is available."""
        if self._docker_available is None:
            self._docker_available = shutil.which('docker') is not None
        return self._docker_available

    def image_exists(self, image: str) -> bool:
        """Check if a Docker image exists locally."""
        if not self.is_available():
            return False
        result = subprocess.run(
            ['docker', 'image', 'inspect', image],
            capture_output=True,
        )
        return result.returncode == 0

    def build_image(
        self,
        dockerfile: Path,
        tag: str,
        context: Path,
        build_args: Optional[Dict[str, str]] = None,
    ):
        """Build a Docker image."""
        cmd = ['docker', 'build', '-f', str(dockerfile), '-t', tag]

        if build_args:
            for key, value in build_args.items():
                cmd.extend(['--build-arg', f'{key}={value}'])

        cmd.append(str(context))

        if self.verbose:
            print(f"  $ docker build -t {tag} ...")

        result = subprocess.run(
            cmd,
            capture_output=not self.verbose,
            text=True,
        )

        if result.returncode != 0:
            if not self.verbose and result.stderr:
                print(result.stderr)
            raise subprocess.CalledProcessError(result.returncode, cmd)


def is_inside_docker() -> bool:
    """Check if we're running inside a Docker container."""
    # Check for .dockerenv file
    if Path('/.dockerenv').exists():
        return True
    # Check cgroup
    try:
        with open('/proc/1/cgroup', 'r') as f:
            return 'docker' in f.read()
    except (FileNotFoundError, PermissionError):
        pass
    return False


def fakechroot_available() -> bool:
    """Check if fakechroot and fakeroot are available."""
    return (shutil.which('fakechroot') is not None and
            shutil.which('fakeroot') is not None)


class PackageIsolation:
    """
    Package build isolation with per-package staging.

    Provides isolation for package builds by:
    1. Creating a per-package staging directory
    2. Installing dependencies via APK to that staging
    3. Running builds with paths pointing to the isolated staging

    Each package:
    - Gets its own staging directory with its dependencies
    - Only sees libraries/headers from declared dependencies
    - Cannot access other packages' build artifacts
    """

    def __init__(
        self,
        toolchain_dir: Path,
        repo_dir: Path,
        arch: str,
        apk_binary: Path,
        verbose: bool = False,
    ):
        """
        Initialize package isolation.

        Args:
            toolchain_dir: Path to cross-compilation toolchain
            repo_dir: Path to APK repository
            arch: Target architecture (e.g., 'aarch64')
            apk_binary: Path to apk binary (host tool)
            verbose: Enable verbose output
        """
        self.toolchain_dir = toolchain_dir
        self.repo_dir = repo_dir
        self.arch = arch
        self.apk_binary = apk_binary
        self.verbose = verbose

    def build_package(
        self,
        pkg_name: str,
        source_dir: Path,
        build_dir: Path,
        staging_dir: Path,
        install_dir: Path,
        build_deps: List[str],
        env: Dict[str, str],
        commands: List[List[str]],
        network: bool = False,
    ):
        """
        Build a package with isolation via per-package staging.

        Args:
            pkg_name: Package name (for logging)
            source_dir: Package source directory
            build_dir: Build output directory
            staging_dir: Package's staging directory (deps installed here)
            install_dir: Package install directory
            build_deps: List of build dependency package names
            env: Environment variables for the build
            commands: List of commands to run
            network: Unused (network isolation provided by Docker)
        """
        # Ensure output directories exist
        staging_dir.mkdir(parents=True, exist_ok=True)
        install_dir.mkdir(parents=True, exist_ok=True)
        build_dir.mkdir(parents=True, exist_ok=True)

        # Install dependencies via APK to the staging directory
        if build_deps:
            self._install_deps(staging_dir, build_deps)

        # Build isolated environment variables
        isolated_env = self._build_isolated_env(
            env, source_dir, build_dir, staging_dir, install_dir
        )

        # Run commands in the build directory
        for cmd in commands:
            self._run_command(cmd, build_dir, source_dir, isolated_env)

    def _install_deps(self, staging_dir: Path, build_deps: List[str]):
        """Install build dependencies by extracting APK files to staging directory."""
        if self.verbose:
            print(f"      Installing deps: {', '.join(build_deps)}")

        # Prepare package names to extract
        # We need both the base package (for .so files) and dev package (for headers)
        packages_to_extract = []
        for dep in build_deps:
            if dep.endswith('-dev'):
                # Add both the dev package and the base package
                packages_to_extract.append(dep)
                base_pkg = dep[:-4]  # Remove '-dev' suffix
                packages_to_extract.append(base_pkg)
            else:
                # Add both the base package and dev package
                packages_to_extract.append(dep)
                packages_to_extract.append(f"{dep}-dev")
        # Remove duplicates while preserving order
        seen = set()
        dev_deps = []
        for pkg in packages_to_extract:
            if pkg not in seen:
                seen.add(pkg)
                dev_deps.append(pkg)

        # Find and extract APK files for each dependency
        repo_arch_dir = self.repo_dir / self.arch
        installed = []

        for dep in dev_deps:
            # Find the APK file (name-version-release.apk)
            apk_files = list(repo_arch_dir.glob(f"{dep}-[0-9]*.apk"))
            if not apk_files:
                if self.verbose:
                    print(f"      Warning: No APK found for {dep}")
                continue

            # Use the first match (should be sorted by version)
            apk_file = sorted(apk_files)[-1]  # Latest version

            # Extract APK to staging using apk extract
            cmd = [
                str(self.apk_binary),
                'extract',
                '--allow-untrusted',
                f'--destination={staging_dir}',
                str(apk_file),
            ]

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
            )

            if result.returncode == 0:
                installed.append(dep)
            elif self.verbose:
                print(f"      Warning: Failed to extract {apk_file.name}: {result.stderr}")

        if installed and self.verbose:
            print(f"      Installed: {', '.join(installed)}")

    def _build_isolated_env(
        self,
        base_env: Dict[str, str],
        source_dir: Path,
        build_dir: Path,
        staging_dir: Path,
        install_dir: Path,
    ) -> Dict[str, str]:
        """Build environment variables for isolated package build."""
        env = os.environ.copy()
        env.update(base_env)

        # Toolchain is in PATH
        env['PATH'] = f"{self.toolchain_dir}/bin:{os.environ.get('PATH', '')}"

        # Point to package's staging directory for deps
        staging_include = staging_dir / 'usr' / 'include'
        staging_lib = staging_dir / 'usr' / 'lib'
        staging_pkgconfig = staging_dir / 'usr' / 'lib' / 'pkgconfig'

        # Add staging paths to compiler flags
        if staging_include.exists():
            cflags = env.get('CFLAGS', '')
            env['CFLAGS'] = f"{cflags} -I{staging_include}"
            cxxflags = env.get('CXXFLAGS', '')
            env['CXXFLAGS'] = f"{cxxflags} -I{staging_include}"

        if staging_lib.exists():
            ldflags = env.get('LDFLAGS', '')
            env['LDFLAGS'] = f"{ldflags} -L{staging_lib} -Wl,-rpath-link={staging_lib}"

        # PKG_CONFIG paths point to staging
        if staging_pkgconfig.exists():
            env['PKG_CONFIG_PATH'] = str(staging_pkgconfig)
            env['PKG_CONFIG_LIBDIR'] = str(staging_pkgconfig)

        env['PKG_CONFIG_SYSROOT_DIR'] = str(staging_dir)
        env['STAGING_DIR'] = str(staging_dir)
        env['INSTALL_DIR'] = str(install_dir)

        return env

    def _run_command(
        self,
        command: List[str],
        build_dir: Path,
        source_dir: Path,
        env: Dict[str, str],
    ):
        """Run a build command with isolation."""
        toolchain_str = str(self.toolchain_dir)
        staging_str = env['STAGING_DIR']
        install_str = env['INSTALL_DIR']
        source_str = str(source_dir)
        # Shared staging is at build/staging/<target> - derives from per-package staging path
        # Per-package staging is at build/packages/<target>/<pkg>/staging
        # Shared staging is at build/staging/<target>
        shared_staging_str = env.get('SHARED_STAGING_DIR', staging_str)

        def substitute_path(arg: str) -> str:
            """Substitute virtual paths with real paths, handling embedded paths correctly."""
            import re

            # Split on delimiters that can separate paths (;, =, :, space)
            # Then substitute each segment individually
            # Pattern to split while keeping delimiters
            segments = re.split(r'([;=:\s])', arg)
            result = []

            for segment in segments:
                # Handle compiler flags like -I/shared-staging (shared staging for dev headers)
                if '-I/shared-staging/' in segment or segment.endswith('-I/shared-staging'):
                    segment = segment.replace('-I/shared-staging/', f'-I{shared_staging_str}/')
                    segment = segment.replace('-I/shared-staging', f'-I{shared_staging_str}')
                # Handle compiler flags like -I/staging, -L/staging (per-package staging)
                elif '-I/staging/' in segment or segment.endswith('-I/staging'):
                    segment = segment.replace('-I/staging/', f'-I{staging_str}/')
                    segment = segment.replace('-I/staging', f'-I{staging_str}')
                elif '-L/staging/' in segment or segment.endswith('-L/staging'):
                    segment = segment.replace('-L/staging/', f'-L{staging_str}/')
                    segment = segment.replace('-L/staging', f'-L{staging_str}')
                elif '-I/toolchain/' in segment:
                    segment = segment.replace('-I/toolchain/', f'-I{toolchain_str}/')
                elif '-L/toolchain/' in segment:
                    segment = segment.replace('-L/toolchain/', f'-L{toolchain_str}/')
                # Handle segments starting with virtual paths
                elif segment.startswith('/shared-staging/') or segment == '/shared-staging':
                    segment = shared_staging_str + segment[15:]  # len('/shared-staging') = 15
                elif segment.startswith('/toolchain/') or segment == '/toolchain':
                    segment = toolchain_str + segment[10:]  # len('/toolchain') = 10
                elif segment.startswith('/staging/') or segment == '/staging':
                    segment = staging_str + segment[8:]  # len('/staging') = 8
                elif segment.startswith('/ipkg-install/') or segment == '/ipkg-install':
                    segment = install_str + segment[13:]  # len('/ipkg-install') = 13
                elif segment.startswith('/src/') or segment == '/src':
                    segment = source_str + segment[4:]  # len('/src') = 4
                elif segment.startswith('/kernel/') or segment == '/kernel':
                    kernel_str = env.get('KERNEL_BUILD_DIR', '/build/kernel')
                    segment = kernel_str + segment[7:]  # len('/kernel') = 7
                result.append(segment)

            return ''.join(result)

        cmd = [substitute_path(arg) for arg in command]

        if self.verbose:
            print(f"      $ {' '.join(cmd)}")

        result = subprocess.run(
            cmd,
            cwd=str(build_dir),
            env=env,
            capture_output=not self.verbose,
            text=True,
        )

        if self.verbose and result.stdout:
            print(result.stdout)

        if result.returncode != 0:
            print(f"      Command failed: {' '.join(cmd)}")
            if result.stderr:
                print(f"      stderr: {result.stderr}")
            raise subprocess.CalledProcessError(result.returncode, cmd)


def get_package_isolation(
    toolchain_dir: Path,
    repo_dir: Path,
    arch: str,
    apk_binary: Path,
    verbose: bool = False,
) -> Optional[PackageIsolation]:
    """
    Get package isolation backend if APK binary is available.

    Returns:
        PackageIsolation instance or None if not available
    """
    if not apk_binary or not apk_binary.exists():
        if verbose:
            print(f"      Warning: apk binary not found at {apk_binary}")
        return None

    if not toolchain_dir or not toolchain_dir.exists():
        if verbose:
            print(f"      Warning: toolchain not found at {toolchain_dir}")
        return None

    return PackageIsolation(
        toolchain_dir=toolchain_dir,
        repo_dir=repo_dir,
        arch=arch,
        apk_binary=apk_binary,
        verbose=verbose,
    )


# Backwards compatibility alias
get_fakechroot_isolation = get_package_isolation
FakechrootIsolation = PackageIsolation
