"""
Ninja build file generator.

Generates build.ninja from a resolved BuildPlan for parallel execution.
"""

import os
from pathlib import Path
from typing import Optional

from .config import Config, PackageConfig, SubpackageConfig, compute_package_content_hash
from .resolver import BuildPlan, BuildTarget


class NinjaGenerator:
    """Generates Ninja build files from a BuildPlan."""

    def __init__(self, config: Config, build_dir: Optional[Path] = None):
        self.config = config
        self.build_dir = build_dir or config.build_dir / config.name
        self.ninja_file = self.build_dir / 'build.ninja'

        # Get path to the owrt-build CLI
        self.cli_path = Path(__file__).parent.parent / 'owrt'

    def generate(self, plan: BuildPlan) -> Path:
        """
        Generate a Ninja build file from the build plan.

        Args:
            plan: Resolved build plan

        Returns:
            Path to generated build.ninja
        """
        self.build_dir.mkdir(parents=True, exist_ok=True)

        # Clean stale stamps for packages whose inputs have changed
        self._clean_stale_stamps(plan)

        lines = self._generate_header()
        lines.extend(self._generate_variables())
        lines.extend(self._generate_rules())
        lines.extend(self._generate_builds(plan))
        lines.extend(self._generate_aliases(plan))

        content = '\n'.join(lines)
        self.ninja_file.write_text(content)

        return self.ninja_file

    def _clean_stale_stamps(self, plan: BuildPlan):
        """Remove ninja stamps for packages whose content hash has changed.

        This ensures ninja will re-invoke package builds when inputs change.
        Each stamp has a corresponding .key file storing the content hash.
        If the hash doesn't match, we delete the stamp to force rebuild.

        Processes packages in dependency order so that dependency hashes can
        be included - if a dependency changes, all dependents will rebuild.

        Note: Toolchain/kernel stamps are per-target (in build_dir/stamp),
        while package stamps are per-architecture (in packages_dir/stamp).
        """
        # Per-target stamp dir for toolchain/kernel
        target_stamp_dir = self.build_dir / 'stamp'
        target_stamp_dir.mkdir(parents=True, exist_ok=True)

        # Per-architecture stamp dir for packages
        pkg_stamp_dir = self.config.packages_dir / 'stamp'
        pkg_stamp_dir.mkdir(parents=True, exist_ok=True)

        toolchain_info = self.config.toolchain

        # Store computed effective hashes for dependency tracking
        effective_hashes: dict = {}

        # Process in build order so dependencies are computed before dependents
        for name in plan.build_order:
            target = plan.targets[name]

            # Select appropriate stamp directory
            if target.target_type in ('toolchain', 'kernel'):
                stamp_dir = target_stamp_dir
            else:
                stamp_dir = pkg_stamp_dir

            stamp_file = stamp_dir / f'{name}.stamp'
            key_file = stamp_dir / f'{name}.key'

            # For packages, compute effective hash (content + dependencies)
            if target.target_type == 'package' and target.config:
                pkg = target.config
                # For subpackages, get the source package
                if isinstance(pkg, SubpackageConfig):
                    pkg = pkg.parent

                # Compute content hash
                content_hash = compute_package_content_hash(pkg, toolchain_info)

                # Compute effective hash including dependency hashes
                import hashlib
                h = hashlib.sha256()
                h.update(content_hash.encode())

                # Include dependency hashes (in sorted order for determinism)
                for dep_name in sorted(target.deps):
                    if dep_name in effective_hashes:
                        h.update(f"dep:{dep_name}:{effective_hashes[dep_name]}".encode())

                effective_hash = h.hexdigest()[:12]
                effective_hashes[name] = effective_hash

                if stamp_file.exists():
                    if key_file.exists():
                        stored_hash = key_file.read_text().strip()
                        if stored_hash == effective_hash:
                            continue  # Hash matches, stamp is valid

                    # Hash doesn't match or doesn't exist - delete stamp to force rebuild
                    stamp_file.unlink()
                    print(f"  {name}: inputs changed, will rebuild")

                    # Also clean the key file if it exists
                    if key_file.exists():
                        key_file.unlink()
            else:
                # For non-packages (toolchain, kernel), use a stable hash from the key file
                # or compute one from target properties
                if key_file.exists():
                    effective_hashes[name] = key_file.read_text().strip()
                else:
                    # Compute a hash for toolchain/kernel based on config
                    import hashlib
                    h = hashlib.sha256()
                    h.update(f"{name}:{target.target_type}".encode())
                    if target.target_type == 'toolchain':
                        h.update(self.config.toolchain.get('gcc_version', '').encode())
                    elif target.target_type == 'kernel':
                        h.update(self.config.kernel.get('full_version', '').encode())
                    effective_hashes[name] = h.hexdigest()[:12]

        # Store effective hashes for use in build generation
        self._effective_hashes = effective_hashes

    def _generate_header(self) -> list:
        """Generate Ninja file header."""
        return [
            '# Auto-generated Ninja build file for OpenWrt',
            f'# Target: {self.config.name}',
            f'# Architecture: {self.config.arch}',
            '',
            '# Minimum Ninja version required',
            'ninja_required_version = 1.10',
            '',
        ]

    def _generate_variables(self) -> list:
        """Generate Ninja variables."""
        poc_dir = Path(__file__).parent.parent
        # build_root is the parent of the target-specific build dir
        # This is used by rules to set BUILD_DIR environment variable
        build_root = self.config.build_dir
        # Package stamps are per-architecture (shared across targets with same arch)
        pkg_stamp_dir = self.config.packages_dir / 'stamp'
        return [
            '# Build directories',
            f'builddir = {self.build_dir}',
            f'build_root = {build_root}',
            f'poc_dir = {poc_dir}',
            f'target = {self.config.name}',
            f'arch = {self.config.arch}',
            f'pkg_stamp_dir = {pkg_stamp_dir}',
            '',
            '# Python interpreter',
            f'python = {os.sys.executable}',
            '',
        ]

    def _generate_rules(self) -> list:
        """Generate Ninja build rules."""
        # Note: -j option must come before the subcommand (it's on the main cli group)
        # All commands need BUILD_DIR set to ensure consistent paths between ninja and Docker

        # Calculate optimal parallel package builds
        # Many packages are small/fast, so allow high parallelism
        # Large packages (kernel, toolchain) are handled separately via console pool
        cpu_count = os.cpu_count() or 4
        # Allow more concurrent package builds - most packages are I/O bound or small
        # For 48 cores: 16 parallel packages, each with 3 jobs
        # For 8 cores: 4 parallel packages, each with 2 jobs
        parallel_packages = max(4, min(24, cpu_count // 3))
        jobs_per_package = max(2, cpu_count // parallel_packages)

        # Downloads are highly parallel (network I/O bound)
        # Allow many concurrent downloads
        parallel_downloads = max(8, min(32, cpu_count))

        return [
            '# Build rules',
            '',
            '# Pool for parallel downloads (network I/O bound)',
            'pool download_pool',
            f'  depth = {parallel_downloads}',
            '',
            '# Pool for parallel package builds',
            '# Limits concurrent packages so each gets enough CPU cores',
            'pool package_pool',
            f'  depth = {parallel_packages}',
            '',
            '# Download a package source',
            'rule download',
            '  command = PYTHONPATH=$poc_dir BUILD_DIR=$build_root $python -m owrt download $target -p $pkg && touch $out',
            '  description = Downloading $pkg',
            '  pool = download_pool',
            '',
            '# Build toolchain',
            '# Only touches stamp if hash changed (avoids triggering downstream rebuilds)',
            'rule toolchain',
            '  command = PYTHONPATH=$poc_dir BUILD_DIR=$build_root $python -m owrt -j $$(nproc) toolchain build $target && { [ "$$(cat $builddir/stamp/toolchain.key 2>/dev/null)" = "$keyhash" ] || { echo $keyhash > $builddir/stamp/toolchain.key && touch $out; }; }',
            '  description = Building toolchain for $target',
            '  pool = console',
            '',
            '# Build kernel and package modules',
            '# Single target: builds kernel, then packages modules using all cores',
            '# Only touches stamp if hash changed (avoids triggering downstream rebuilds)',
            'rule kernel',
            '  command = PYTHONPATH=$poc_dir BUILD_DIR=$build_root $python -m owrt -j $$(nproc) kernel build $target && PYTHONPATH=$poc_dir BUILD_DIR=$build_root $python -m owrt -j $$(nproc) kernel modules $target && { [ "$$(cat $builddir/stamp/kernel.key 2>/dev/null)" = "$keyhash" ] || { echo $keyhash > $builddir/stamp/kernel.key && touch $out; }; }',
            '  description = Building kernel and packaging modules for $target',
            '  pool = console',
            '',
            '# Build a package',
            '# After success, writes the effective hash to a .key file for dependency tracking',
            '# Package stamps are per-architecture (shared across targets with same arch)',
            'rule package',
            f'  command = PYTHONPATH=$poc_dir BUILD_DIR=$build_root $python -m owrt -j {jobs_per_package} package $target $pkg && echo $keyhash > $pkg_stamp_dir/$pkg.key',
            '  description = Building package $pkg',
            '  pool = package_pool',
            '',
            '# Generate APK repository index',
            'rule apk_index',
            '  command = PYTHONPATH=$poc_dir BUILD_DIR=$build_root $python -m owrt apk-index $target',
            '  description = Generating APK repository index',
            '  pool = console',
            '',
            '# Generate images',
            'rule image',
            '  command = PYTHONPATH=$poc_dir BUILD_DIR=$build_root $python -m owrt image $target --profile $profile',
            '  description = Generating images for $profile',
            '  pool = console',
            '',
            # Note: 'phony' is a built-in Ninja rule, don't define it
        ]

    def _generate_builds(self, plan: BuildPlan) -> list:
        """Generate Ninja build statements."""
        from .download import get_download_filename

        # Store plan targets for dependency type lookup
        self._plan_targets = plan.targets

        lines = ['# Download statements (run first, highly parallel)', '']

        # First, generate download targets for all packages
        # These run in parallel before any builds start
        # Downloads are per-architecture (shared across targets with same arch)
        download_stamps = []
        for name in plan.build_order:
            target = plan.targets[name]
            if target.target_type == 'package' and target.config:
                pkg = target.config
                # Get source package for subpackages
                if isinstance(pkg, SubpackageConfig):
                    pkg = pkg.parent
                filename = get_download_filename(pkg)
                if filename:
                    dl_stamp = f'$pkg_stamp_dir/dl-{name}.stamp'
                    download_stamps.append(dl_stamp)
                    lines.append(f'build {dl_stamp}: download')
                    lines.append(f'  pkg = {name}')
                    lines.append('')

        # Add 'downloads' alias target
        if download_stamps:
            lines.append(f'build downloads: phony {" ".join(download_stamps)}')
            lines.append('')

        lines.append('# Build statements')
        lines.append('')

        for name in plan.build_order:
            target = plan.targets[name]

            # Skip cached targets unless dependencies changed
            if name in plan.cached:
                lines.append(f'# {name}: cached (key={target.cache_key[:8]})')
                lines.append('')
                continue

            lines.extend(self._generate_target_build(target, download_stamps))

        return lines

    def _generate_target_build(self, target: BuildTarget, download_stamps: list = None) -> list:
        """Generate build statement for a single target."""
        from .download import get_download_filename

        lines = []

        # Toolchain and kernel stamps are per-target, package stamps are per-architecture
        if target.target_type in ('toolchain', 'kernel'):
            stamp = f'$builddir/stamp/{target.name}.stamp'
        else:
            stamp = f'$pkg_stamp_dir/{target.name}.stamp'

        # Compute dependencies - packages depend on per-arch stamps, toolchain/kernel on per-target
        deps = []
        for dep_name in target.deps:
            dep_target = getattr(self, '_plan_targets', {}).get(dep_name)
            if dep_target and dep_target.target_type in ('toolchain', 'kernel'):
                deps.append(f'$builddir/stamp/{dep_name}.stamp')
            else:
                deps.append(f'$pkg_stamp_dir/{dep_name}.stamp')

        dep_str = ' '.join(deps) if deps else ''

        if target.target_type == 'toolchain':
            lines.append(f'build {stamp}: toolchain | {dep_str}'.strip())
            keyhash = getattr(self, '_effective_hashes', {}).get(target.name, 'unknown')
            lines.append(f'  keyhash = {keyhash}')

        elif target.target_type == 'kernel':
            lines.append(f'build {stamp}: kernel | {dep_str}'.strip())
            keyhash = getattr(self, '_effective_hashes', {}).get(target.name, 'unknown')
            lines.append(f'  keyhash = {keyhash}')

        elif target.target_type == 'package':
            # Package builds depend on their download completing
            pkg = target.config
            if isinstance(pkg, SubpackageConfig):
                pkg = pkg.parent
            has_download = pkg and get_download_filename(pkg)

            if has_download:
                # Add download stamp as explicit dependency (downloads are per-arch too)
                dl_stamp = f'$pkg_stamp_dir/dl-{target.name}.stamp'
                if dep_str:
                    dep_str = f'{dl_stamp} {dep_str}'
                else:
                    dep_str = dl_stamp

            lines.append(f'build {stamp}: package | {dep_str}'.strip())
            lines.append(f'  pkg = {target.name}')
            # Include effective hash for dependency tracking
            keyhash = getattr(self, '_effective_hashes', {}).get(target.name, 'unknown')
            lines.append(f'  keyhash = {keyhash}')

        lines.append('')
        return lines

    def _generate_aliases(self, plan: BuildPlan) -> list:
        """Generate alias targets for convenience."""
        lines = ['# Alias targets', '']

        # Collect package stamps (per-architecture)
        pkg_stamps = []
        for name, target in plan.targets.items():
            if target.target_type == 'package':
                pkg_stamps.append(f'$pkg_stamp_dir/{name}.stamp')

        # All packages alias
        if pkg_stamps:
            lines.append(f'build packages: phony {" ".join(pkg_stamps)}')
            lines.append('')

            # APK repository index - depends on all packages
            # Note: apk-index is per-target since different targets may have different package selections
            lines.append(f'build $builddir/stamp/apk-index.stamp: apk_index | {" ".join(pkg_stamps)}')
            lines.append('')

            # APK index alias
            lines.append('build apk-index: phony $builddir/stamp/apk-index.stamp')
            lines.append('')

        # Kernel alias (per-target) - includes module packaging
        lines.append('build kernel-target: phony $builddir/stamp/kernel.stamp')
        lines.append('')

        # Image generation - depends on kernel (which includes kmod) and apk-index
        image_deps = ['$builddir/stamp/kernel.stamp']
        if pkg_stamps:
            image_deps.append('$builddir/stamp/apk-index.stamp')
        lines.append(f'build $builddir/stamp/image.stamp: image | {" ".join(image_deps)}')
        default_profile = self.config.get_default_profile_name()
        lines.append(f'  profile = {default_profile}')
        lines.append('')

        # Image alias
        lines.append('build images: phony $builddir/stamp/image.stamp')
        lines.append('')

        # Full build alias - includes images if packages exist
        # Toolchain and kernel stamps are per-target, package stamps are per-architecture
        # Note: kernel.stamp now includes module packaging
        all_stamps = []
        for name in plan.build_order:
            target = plan.targets[name]
            if target.target_type in ('toolchain', 'kernel'):
                all_stamps.append(f'$builddir/stamp/{name}.stamp')
            else:
                all_stamps.append(f'$pkg_stamp_dir/{name}.stamp')
        if pkg_stamps:
            all_stamps.append('$builddir/stamp/apk-index.stamp')
            all_stamps.append('$builddir/stamp/image.stamp')
        lines.append(f'build all: phony {" ".join(all_stamps)}')
        lines.append('')

        # Default target
        lines.append('default all')
        lines.append('')

        return lines


class NinjaRunner:
    """Runs Ninja builds."""

    def __init__(self, ninja_file: Path, verbose: bool = False, jobs: Optional[int] = None):
        self.ninja_file = ninja_file
        self.verbose = verbose
        self.jobs = jobs or os.cpu_count()

    def run(self, targets: Optional[list] = None) -> bool:
        """
        Run Ninja build.

        Args:
            targets: Specific targets to build (None for default)

        Returns:
            True if build succeeded
        """
        import subprocess

        cmd = ['ninja', '-f', str(self.ninja_file)]

        if self.jobs:
            cmd.extend(['-j', str(self.jobs)])

        if self.verbose:
            cmd.append('-v')

        if targets:
            cmd.extend(targets)

        print(f"Running: {' '.join(cmd)}")

        result = subprocess.run(
            cmd,
            cwd=self.ninja_file.parent,
        )

        return result.returncode == 0

    def clean(self) -> bool:
        """Clean all build outputs."""
        import subprocess

        cmd = ['ninja', '-f', str(self.ninja_file), '-t', 'clean']

        result = subprocess.run(
            cmd,
            cwd=self.ninja_file.parent,
        )

        return result.returncode == 0

    def graph(self, output: Path) -> bool:
        """Generate a graphviz graph of the build."""
        import subprocess

        cmd = ['ninja', '-f', str(self.ninja_file), '-t', 'graph']

        result = subprocess.run(
            cmd,
            cwd=self.ninja_file.parent,
            capture_output=True,
            text=True,
        )

        if result.returncode == 0:
            output.write_text(result.stdout)
            return True

        return False
