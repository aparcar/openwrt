#!/usr/bin/env python3
"""
OpenWrt Modern Build System - CLI Entry Point
"""

import click
import os
import sys
from pathlib import Path

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from owrt.config import Config, PackageConfig, SubpackageConfig, compute_package_content_hash
from owrt.build_config import BuildConfig
from owrt.toolchain import ToolchainBuilder
from owrt.kernel import KernelBuilder
from owrt.package import PackageBuilder
from owrt.image import ImageBuilder
from owrt.resolver import DependencyResolver
from owrt.ninja_gen import NinjaGenerator, NinjaRunner
from owrt.tool import ToolBuilder, ToolConfig


@click.group()
@click.option('--verbose', '-v', is_flag=True, help='Enable verbose output')
@click.option('--jobs', '-j', default=None, type=int, help='Number of parallel jobs')
@click.option('--ccache/--no-ccache', default=False, help='Use ccache for compilation')
@click.option('--docker/--no-docker', default=None,
              help='Run inside Docker container (auto-detected by default)')
@click.pass_context
def cli(ctx, verbose, jobs, ccache, docker):
    """OpenWrt Modern Build System - Proof of Concept"""
    ctx.ensure_object(dict)
    ctx.obj['verbose'] = verbose
    ctx.obj['jobs'] = jobs or os.cpu_count()
    ctx.obj['ccache'] = ccache
    ctx.obj['docker'] = docker


@cli.group()
def toolchain():
    """Toolchain management commands"""
    pass


@cli.group()
def base():
    """Base container management commands"""
    pass


@base.command('hash')
@click.option('--format', '-f', 'fmt', default='hash',
              type=click.Choice(['hash', 'json']),
              help='Output format: hash (12 char), json (full details)')
def base_hash(fmt):
    """Compute base container cache key.

    The hash includes the Dockerfile that defines the build environment.
    This can be used to determine if the base Docker image needs rebuilding.
    """
    import hashlib
    import json

    poc_dir = Path(__file__).parent.parent
    dockerfile = poc_dir / 'docker' / 'Dockerfile'

    if not dockerfile.exists():
        click.echo("Error: Dockerfile not found", err=True)
        sys.exit(1)

    # Compute hash of Dockerfile
    h = hashlib.sha256()
    h.update(dockerfile.read_bytes())
    hash_value = h.hexdigest()[:12]

    if fmt == 'hash':
        click.echo(hash_value)
    elif fmt == 'json':
        result = {
            'hash': hash_value,
            'file': str(dockerfile.relative_to(poc_dir)),
        }
        click.echo(json.dumps(result, indent=2))


def compute_base_hash() -> str:
    """Compute base container hash (shared helper function)."""
    import hashlib
    poc_dir = Path(__file__).parent.parent
    dockerfile = poc_dir / 'docker' / 'Dockerfile'
    if dockerfile.exists():
        h = hashlib.sha256()
        h.update(dockerfile.read_bytes())
        return h.hexdigest()[:12]
    return "unknown"


@toolchain.command('info')
@click.argument('target')
@click.pass_context
def toolchain_info(ctx, target):
    """Show toolchain configuration for TARGET"""
    config = Config.load_target(target)
    builder = ToolchainBuilder(config, verbose=ctx.obj['verbose'], jobs=ctx.obj['jobs'])

    click.echo(f"Toolchain configuration for {target}")
    click.echo(f"  Target tuple: {config.target_tuple}")
    click.echo(f"  Architecture: {config.arch}")
    click.echo(f"  C library:    {config.toolchain.get('libc', 'musl')}")
    click.echo()
    click.echo("Component versions:")
    click.echo(f"  Binutils: {builder.binutils_version}")
    click.echo(f"  GCC:      {builder.gcc_version}")
    click.echo(f"  Musl:     {builder.MUSL_VERSION}")
    click.echo(f"  Linux:    {builder.LINUX_VERSION} (headers)")
    click.echo()
    click.echo("Build directories:")
    click.echo(f"  Toolchain: {builder.toolchain_dir}")
    click.echo(f"  Build:     {builder.build_dir}")
    click.echo(f"  Downloads: {builder.dl_dir}")
    click.echo()
    click.echo("Build stages:")
    click.echo("  [1/5] binutils     - Cross assembler and linker")
    click.echo("  [2/5] gcc-initial  - Minimal GCC for compiling musl")
    click.echo("  [3/5] kernel-headers - Linux kernel API headers")
    click.echo("  [4/5] musl         - C library")
    click.echo("  [5/5] gcc-final    - Full GCC with C++ support")
    click.echo()
    if builder.is_built():
        click.echo("Status: BUILT")
    else:
        click.echo("Status: NOT BUILT")


@toolchain.command('build')
@click.argument('target')
@click.option('--force', '-f', is_flag=True, help='Force rebuild')
@click.option('--dry-run', '-n', is_flag=True, help='Show what would be built without building')
@click.pass_context
def toolchain_build(ctx, target, force, dry_run):
    """Build the cross-compilation toolchain for TARGET"""
    from owrt.docker_wrapper import run_in_docker
    from owrt.container import is_inside_docker

    # All builds must run in containers - launch Docker if not already inside (skip for dry-run)
    if not is_inside_docker() and not dry_run:
        args = ['toolchain', 'build', target]
        if force:
            args.append('-f')
        if dry_run:
            args.append('-n')
        sys.exit(run_in_docker(
            args=args,
            target=target,
            use_toolchain=False,  # Use base image for building toolchain
            verbose=ctx.obj['verbose'],
            jobs=ctx.obj['jobs'],
            ccache=ctx.obj['ccache'],
        ))

    config = Config.load_target(target)
    builder = ToolchainBuilder(config, verbose=ctx.obj['verbose'], jobs=ctx.obj['jobs'])

    if dry_run:
        click.echo(f"Toolchain build plan for {target}")
        click.echo(f"  Target: {config.target_tuple}")
        click.echo(f"  GCC: {builder.gcc_version}")
        click.echo(f"  Binutils: {builder.binutils_version}")
        click.echo(f"  Musl: {builder.MUSL_VERSION}")
        click.echo()
        click.echo("Build stages:")
        stages = [
            ("binutils", builder.stamp_dir / 'binutils_installed'),
            ("gcc-initial", builder.stamp_dir / 'gcc_initial_installed'),
            ("kernel-headers", builder.stamp_dir / 'kernel_headers_installed'),
            ("musl", builder.stamp_dir / 'musl_installed'),
            ("gcc-final", builder.stamp_dir / 'gcc_final_installed'),
        ]
        for name, stamp in stages:
            status = "DONE" if stamp.exists() else "PENDING"
            click.echo(f"  [{status:7}] {name}")
        click.echo()
        click.echo("Source URLs:")
        click.echo(f"  {builder.BINUTILS_URL}")
        click.echo(f"  {builder.GCC_URL}")
        click.echo(f"  {builder.MUSL_URL}")
        click.echo(f"  {builder.LINUX_URL}")
        return

    click.echo(f"Building toolchain for {target}...")

    if force:
        builder.clean()

    builder.build()

    # Create ninja stamp file for toolchain
    ninja_stamp_dir = config.build_dir / config.name / 'stamp'
    ninja_stamp_dir.mkdir(parents=True, exist_ok=True)
    (ninja_stamp_dir / 'toolchain.stamp').touch()

    click.echo(f"Toolchain built successfully: {builder.toolchain_dir}")


@toolchain.command('clean')
@click.argument('target')
@click.pass_context
def toolchain_clean(ctx, target):
    """Clean the toolchain for TARGET"""
    config = Config.load_target(target)
    builder = ToolchainBuilder(config, verbose=ctx.obj['verbose'])
    builder.clean()
    click.echo(f"Toolchain cleaned for {target}")


@toolchain.command('hash')
@click.argument('target')
@click.option('--format', '-f', 'fmt', default='tag',
              type=click.Choice(['tag', 'hash', 'json']),
              help='Output format: tag (target-hash), hash (just hash), json (full details)')
@click.pass_context
def toolchain_hash(ctx, target, fmt):
    """Compute toolchain cache key for TARGET.

    The hash includes all files that affect the toolchain build:
    - Base container Dockerfile
    - Toolchain builder script
    - Target configuration
    - OpenWrt downstream patches for binutils, gcc, and musl

    This can be used by CI and developers to check if a cached
    toolchain already exists before rebuilding.
    """
    import hashlib
    import json

    config = Config.load_target(target)
    builder = ToolchainBuilder(config)

    # Collect all files that affect the toolchain build
    # Use tuples of (relative_path, absolute_path) for reproducibility
    files_to_hash = []
    poc_dir = config.poc_dir

    # 0. Base container Dockerfile (must be first - affects build environment)
    dockerfile = poc_dir / 'docker' / 'Dockerfile'
    if dockerfile.exists():
        rel_path = dockerfile.relative_to(poc_dir)
        files_to_hash.append((str(rel_path), dockerfile))

    # 1. Toolchain builder script
    toolchain_py = Path(__file__).parent / 'toolchain.py'
    if toolchain_py.exists():
        rel_path = toolchain_py.relative_to(poc_dir)
        files_to_hash.append((str(rel_path), toolchain_py))

    # 2. Target configuration
    target_yaml = poc_dir / 'targets' / target / 'target.yaml'
    if target_yaml.exists():
        rel_path = target_yaml.relative_to(poc_dir)
        files_to_hash.append((str(rel_path), target_yaml))

    # 3. OpenWrt downstream patches (from toolchain/)
    patches_dirs = [
        ('binutils', poc_dir / 'toolchain' / 'binutils' / 'patches' / builder.binutils_version),
        ('gcc', poc_dir / 'toolchain' / 'gcc' / f'patches-{builder.gcc_version.split(".")[0]}.x'),
        ('musl', poc_dir / 'toolchain' / 'musl' / 'patches'),
    ]

    patch_files = []
    for component, patches_dir in patches_dirs:
        if patches_dir.exists():
            for patch in sorted(patches_dir.glob('*.patch')):
                rel_path = patch.relative_to(poc_dir)
                files_to_hash.append((str(rel_path), patch))
                patch_files.append((component, patch.name))

    # Sort by relative path for reproducibility
    files_to_hash.sort(key=lambda x: x[0])

    # Compute combined hash from sorted file contents
    h = hashlib.sha256()
    for rel_path, abs_path in files_to_hash:
        # Include the relative path in the hash for extra safety
        h.update(rel_path.encode('utf-8'))
        h.update(abs_path.read_bytes())

    # Get base hash separately for reference
    base_hash = compute_base_hash()

    hash_value = h.hexdigest()[:16]
    tag = f"{target}-{hash_value}"

    if fmt == 'tag':
        click.echo(tag)
    elif fmt == 'hash':
        click.echo(hash_value)
    elif fmt == 'json':
        result = {
            'target': target,
            'hash': hash_value,
            'tag': tag,
            'base_hash': base_hash,
            'components': {
                'binutils': builder.binutils_version,
                'gcc': builder.gcc_version,
                'musl': builder.MUSL_VERSION,
            },
            'files': [rel_path for rel_path, _ in files_to_hash],
            'patch_count': len(patch_files),
        }
        click.echo(json.dumps(result, indent=2))


@cli.group()
def tool():
    """Host tool management commands"""
    pass


@tool.command('list')
def tool_list():
    """List available host tools"""
    tools = ToolConfig.list_tools()
    if tools:
        click.echo("Available host tools:")
        for name in tools:
            t = ToolConfig.find_tool(name)
            if t:
                title = t.metadata.get('title', t.name)
                click.echo(f"  {name:15} - {title}")
    else:
        click.echo("No host tools found in owrt/tools/")


@tool.command('info')
@click.argument('tool_name')
def tool_info(tool_name):
    """Show information about a host tool"""
    t = ToolConfig.find_tool(tool_name)
    if not t:
        click.echo(f"Tool not found: {tool_name}", err=True)
        sys.exit(1)

    click.echo(f"Tool: {t.name}")
    click.echo(f"  Version: {t.version}")
    click.echo(f"  License: {t.license}")
    click.echo(f"  Build system: {t.build_system}")
    if t.dependencies:
        click.echo(f"  Dependencies: {', '.join(t.dependencies)}")
    click.echo()
    if t.metadata.get('description'):
        click.echo(f"Description:")
        click.echo(f"  {t.metadata['description'].strip()}")


@tool.command('build')
@click.argument('tool_name')
@click.option('--force', '-f', is_flag=True, help='Force rebuild')
@click.option('--build-dir', '-b', type=click.Path(), help='Build directory')
@click.pass_context
def tool_build(ctx, tool_name, force, build_dir):
    """Build a host tool"""
    # Use default build directory if not specified
    if build_dir:
        bd = Path(build_dir)
    else:
        bd = Path('/build') if Path('/build').exists() else Path.cwd() / 'build'

    click.echo(f"Building host tool: {tool_name}")
    builder = ToolBuilder(bd, verbose=ctx.obj['verbose'], jobs=ctx.obj['jobs'])

    if force:
        # Remove stamp for this tool
        stamp = builder.stamp_dir / f'{tool_name}.built'
        if stamp.exists():
            stamp.unlink()

    builder.build_tools([tool_name])
    click.echo(f"Tool {tool_name} built successfully")
    click.echo(f"Staging directory: {builder.staging_dir}")


@tool.command('build-all')
@click.option('--force', '-f', is_flag=True, help='Force rebuild')
@click.option('--build-dir', '-b', type=click.Path(), help='Build directory')
@click.pass_context
def tool_build_all(ctx, force, build_dir):
    """Build all host tools"""
    if build_dir:
        bd = Path(build_dir)
    else:
        bd = Path('/build') if Path('/build').exists() else Path.cwd() / 'build'

    tools = ToolConfig.list_tools()
    if not tools:
        click.echo("No host tools found")
        return

    click.echo(f"Building {len(tools)} host tools...")
    builder = ToolBuilder(bd, verbose=ctx.obj['verbose'], jobs=ctx.obj['jobs'])

    if force:
        builder.clean()

    builder.build_tools(tools)
    click.echo(f"All tools built successfully")
    click.echo(f"Staging directory: {builder.staging_dir}")


@cli.group()
def kernel():
    """Kernel build commands"""
    pass


@kernel.command('build')
@click.argument('target')
@click.option('--force', '-f', is_flag=True, help='Force rebuild')
@click.pass_context
def kernel_build(ctx, target, force):
    """Build the Linux kernel for TARGET"""
    from owrt.docker_wrapper import run_in_docker
    from owrt.container import is_inside_docker

    # All builds must run in containers - launch Docker if not already inside
    if not is_inside_docker():
        args = ['kernel', 'build', target]
        if force:
            args.append('-f')
        sys.exit(run_in_docker(
            args=args,
            target=target,
            use_toolchain=True,  # Use toolchain image for kernel build
            verbose=ctx.obj['verbose'],
            jobs=ctx.obj['jobs'],
            ccache=ctx.obj['ccache'],
        ))

    click.echo(f"Building kernel for {target}...")

    config = Config.load_target(target)
    builder = KernelBuilder(config, verbose=ctx.obj['verbose'], jobs=ctx.obj['jobs'], use_ccache=ctx.obj['ccache'])

    if force:
        builder.clean()

    builder.build()

    # Create ninja stamp file for kernel
    ninja_stamp_dir = config.build_dir / config.name / 'stamp'
    ninja_stamp_dir.mkdir(parents=True, exist_ok=True)
    (ninja_stamp_dir / 'kernel.stamp').touch()

    click.echo(f"Kernel built successfully")


@kernel.command('menuconfig')
@click.argument('target')
@click.pass_context
def kernel_menuconfig(ctx, target):
    """Run kernel menuconfig for TARGET"""
    config = Config.load_target(target)
    builder = KernelBuilder(config, verbose=ctx.obj['verbose'])
    builder.menuconfig()


@kernel.command('dtbs')
@click.argument('target')
@click.pass_context
def kernel_dtbs(ctx, target):
    """Build Device Tree Blobs for TARGET"""
    config = Config.load_target(target)
    builder = KernelBuilder(config, verbose=ctx.obj['verbose'])
    builder.build_dtbs()


@kernel.command('modules')
@click.argument('target')
@click.pass_context
def kernel_modules(ctx, target):
    """Package kernel modules as APKs for TARGET"""
    from .kmod import KernelModulePackager

    click.echo(f"Packaging kernel modules for {target}...")
    config = Config.load_target(target)
    packager = KernelModulePackager(config, verbose=ctx.obj['verbose'])

    packages = packager.build_module_packages()
    click.echo(f"Created {len(packages)} kernel module packages")


@cli.command('build')
@click.argument('target')
@click.option('--profile', '-p', default=None, help='Device profile (default: first available)')
@click.option('--all-profiles', '-A', is_flag=True, help='Build all profiles for this target')
@click.option('--packages', '-P', multiple=True, help='Additional packages')
@click.option('--all-packages', '-a', is_flag=True, help='Build all available packages (buildbot mode)')
@click.option('--continue-on-error', '-c', is_flag=True, help='Continue building other packages on failure (buildbot mode)')
@click.option('--force', '-f', is_flag=True, help='Force rebuild')
@click.pass_context
def build(ctx, target, profile, all_profiles, packages, force, all_packages, continue_on_error):
    """Build complete firmware for TARGET"""
    from owrt.docker_wrapper import run_in_docker
    from owrt.container import is_inside_docker

    # All builds must run in containers - launch Docker if not already inside
    if not is_inside_docker():
        args = ['build', target]
        if profile:
            args.extend(['-p', profile])
        if all_profiles:
            args.append('-A')
        for pkg in packages:
            args.extend(['-P', pkg])
        if all_packages:
            args.append('-a')
        if continue_on_error:
            args.append('-c')
        if force:
            args.append('-f')
        sys.exit(run_in_docker(
            args=args,
            target=target,
            use_toolchain=True,
            verbose=ctx.obj['verbose'],
            jobs=ctx.obj['jobs'],
            ccache=ctx.obj['ccache'],
        ))

    config = Config.load_target(target)
    jobs = ctx.obj['jobs']
    verbose = ctx.obj['verbose']

    # Determine which profiles to build
    if all_profiles:
        profiles_to_build = [p['name'] for p in config.profiles]
        click.echo(f"Building firmware for {target} (all {len(profiles_to_build)} profiles: {', '.join(profiles_to_build)})...")
    else:
        # Use specified profile or default
        if profile is None:
            profile = config.get_default_profile_name()
        profiles_to_build = [profile]
        click.echo(f"Building firmware for {target} (profile: {profile})...")

    # Step 1: Toolchain
    click.echo("\n[1/4] Building toolchain...")
    tc_builder = ToolchainBuilder(config, verbose=verbose, jobs=jobs)
    if force or not tc_builder.is_built():
        tc_builder.build()
    else:
        click.echo("  Toolchain already built, skipping.")

    # Step 2: Kernel
    click.echo("\n[2/4] Building kernel...")
    use_ccache = ctx.obj['ccache']
    k_builder = KernelBuilder(config, verbose=verbose, jobs=jobs, use_ccache=use_ccache)
    if force or not k_builder.is_built():
        k_builder.build()
    else:
        click.echo("  Kernel already built, skipping.")

    # Step 3: Packages
    click.echo("\n[3/4] Building packages...")
    pkg_builder = PackageBuilder(config, verbose=verbose, jobs=jobs, use_ccache=use_ccache)
    
    if all_packages:
        # Buildbot mode: build all available packages
        all_pkgs = PackageConfig.find_all_packages()
        # Get all source package names - we can check availability of dependencies
        all_pkg_names = set()
        for pkg in all_pkgs:
            all_pkg_names.add(pkg.name)
            for subpkg_name in pkg.subpackages:
                all_pkg_names.add(subpkg_name)
            if pkg.has_variants:
                for variant in pkg.variants.values():
                    all_pkg_names.add(variant.package_name)
        
        # Get all package names including subpackages and variants
        package_list = []
        skipped_variants = []
        for pkg in all_pkgs:
            if pkg.has_variants:
                # For packages with variants, check which variants have their deps available
                for variant_name, variant in pkg.variants.items():
                    # Check if all build dependencies are available
                    build_deps = variant._dependencies.get('build', [])
                    missing_deps = [dep for dep in build_deps if dep not in all_pkg_names]
                    if missing_deps:
                        skipped_variants.append(f"{variant.package_name} (missing: {', '.join(missing_deps)})")
                        continue
                    package_list.append(variant.package_name)
            else:
                # Regular package
                package_list.append(pkg.name)
            # Add subpackages
            for subpkg_name in pkg.subpackages:
                package_list.append(subpkg_name)
        
        click.echo(f"  Buildbot mode: building {len(package_list)} packages")
        if skipped_variants:
            click.echo(f"  Skipping {len(skipped_variants)} variants with missing dependencies:")
            for sv in skipped_variants[:5]:
                click.echo(f"    - {sv}")
            if len(skipped_variants) > 5:
                click.echo(f"    ... and {len(skipped_variants) - 5} more")
    else:
        # Collect packages from all profiles being built
        package_set = set(config.default_packages)
        for prof_name in profiles_to_build:
            try:
                prof_data = config.get_profile(prof_name)
                prof_packages = prof_data.get('packages') or []
                package_set.update(prof_packages)
            except ValueError:
                pass
        package_set.update(packages)
        package_list = list(package_set)
    
    pkg_builder.build_packages(package_list, force=force, continue_on_error=continue_on_error)

    # Step 4: Images - build for each profile
    click.echo("\n[4/4] Generating images...")
    img_builder = ImageBuilder(config, verbose=verbose)
    
    failed_profiles = []
    for prof_name in profiles_to_build:
        try:
            if len(profiles_to_build) > 1:
                click.echo(f"\n  Building images for profile: {prof_name}")
            img_builder.build(prof_name)
        except Exception as e:
            if continue_on_error:
                click.echo(f"  Profile {prof_name}: FAILED - {e}")
                failed_profiles.append(prof_name)
            else:
                raise

    if failed_profiles:
        click.echo(f"\nBuild completed with {len(failed_profiles)} profile failures: {', '.join(failed_profiles)}")
    else:
        click.echo(f"\nBuild complete! Images in: {config.output_dir}/images/")


@cli.group()
def config():
    """Build configuration management"""
    pass


@config.command('show')
@click.option('--config-file', '-c', type=click.Path(exists=True),
              help='Path to config.yaml')
def config_show(config_file):
    """Show current build configuration"""
    try:
        if config_file:
            build_config = BuildConfig.load(Path(config_file))
        else:
            build_config = BuildConfig.load()

        click.echo(f"Configuration loaded from: {config_file or 'config.yaml'}")
        click.echo("")
        click.echo(f"Target: {build_config.target_name}")
        click.echo(f"Profile: {build_config.profile_name}")
        click.echo("")

        packages = build_config.get_all_packages()
        click.echo(f"Packages ({len(packages)} total):")
        for pkg in packages[:20]:
            click.echo(f"  - {pkg}")
        if len(packages) > 20:
            click.echo(f"  ... and {len(packages) - 20} more")

        if build_config.kernel_config:
            click.echo("")
            click.echo(f"Kernel config overrides ({len(build_config.kernel_config)}):")
            for key, value in sorted(build_config.kernel_config.items()):
                click.echo(f"  {key}={value}")

        kmods = build_config.get_kernel_modules()
        if kmods:
            click.echo("")
            click.echo(f"Kernel modules ({len(kmods)}):")
            for kmod in kmods[:10]:
                click.echo(f"  - {kmod}")
            if len(kmods) > 10:
                click.echo(f"  ... and {len(kmods) - 10} more")

    except FileNotFoundError as e:
        click.echo(f"Error: {e}", err=True)
        click.echo("Create config.yaml from config.yaml.example", err=True)
        sys.exit(1)


@config.command('init')
@click.argument('target')
@click.option('--profile', '-p', default='generic', help='Device profile')
@click.option('--output', '-o', default='config.yaml', help='Output file')
def config_init(target, profile, output):
    """Initialize a new config.yaml for TARGET"""
    # Create a basic config
    build_config = BuildConfig.from_args(target=target, profile=profile)

    output_path = Path(output)
    build_config.save(output_path)

    click.echo(f"Created {output}")
    click.echo(f"Edit to customize packages and kernel options.")


@cli.command('firmware')
@click.option('--config-file', '-c', type=click.Path(exists=True),
              help='Path to config.yaml (default: auto-detect)')
@click.option('--force', '-f', is_flag=True, help='Force rebuild')
@click.pass_context
def firmware(ctx, config_file, force):
    """Build firmware using config.yaml

    Reads target, profile, packages, and kernel config from config.yaml.
    This is the recommended way to build firmware with custom settings.

    Example:
        # Create config
        python -m owrt config init armsr-armv8

        # Edit config.yaml to add packages, enable IPv6, etc.

        # Build firmware
        python -m owrt firmware
    """
    try:
        if config_file:
            build_config = BuildConfig.load(Path(config_file))
        else:
            build_config = BuildConfig.load()
    except FileNotFoundError:
        click.echo("Error: No config.yaml found.", err=True)
        click.echo("Create one with: python -m owrt config init <target>", err=True)
        sys.exit(1)

    click.echo(f"Building firmware from config.yaml")
    click.echo(f"  Target: {build_config.target_name}")
    click.echo(f"  Profile: {build_config.profile_name}")

    target_config = build_config.load_target()
    jobs = ctx.obj['jobs']
    verbose = ctx.obj['verbose'] or build_config.build.verbose

    # Step 1: Toolchain
    click.echo("\n[1/4] Building toolchain...")
    tc_builder = ToolchainBuilder(target_config, verbose=verbose, jobs=jobs)
    if force or not tc_builder.is_built():
        tc_builder.build()
    else:
        click.echo("  Toolchain already built, skipping.")

    # Step 2: Kernel (with config overrides)
    click.echo("\n[2/4] Building kernel...")
    use_ccache = ctx.obj['ccache']
    k_builder = KernelBuilder(target_config, verbose=verbose, jobs=jobs, use_ccache=use_ccache)
    if force or not k_builder.is_built():
        kernel_overrides = build_config.get_kernel_config_overrides()
        if kernel_overrides:
            click.echo(f"  Applying {len(kernel_overrides)} kernel config overrides")
        k_builder.build(
            profile_name=build_config.profile_name,
            kernel_config_overrides=kernel_overrides,
        )
    else:
        click.echo("  Kernel already built, skipping.")

    # Step 3: Packages
    click.echo("\n[3/4] Building packages...")
    pkg_builder = PackageBuilder(target_config, verbose=verbose, jobs=jobs, use_ccache=use_ccache)
    package_list = build_config.get_all_packages()
    click.echo(f"  Building {len(package_list)} packages...")
    pkg_builder.build_packages(package_list, force=force)

    # Step 4: Image
    click.echo("\n[4/4] Generating images...")
    img_builder = ImageBuilder(target_config, verbose=verbose)
    img_builder.build(build_config.profile_name)

    click.echo(f"\nBuild complete! Images in: {target_config.output_dir}/images/")


@cli.command('package')
@click.argument('target')
@click.argument('package_name')
@click.option('--force', '-f', is_flag=True, help='Force rebuild')
@click.option('--no-apk', is_flag=True, help='Skip APK package creation')
@click.pass_context
def package(ctx, target, package_name, force, no_apk):
    """Build a single package for TARGET.

    All packages are built with fakechroot isolation for maximum security.
    Each package runs in an isolated filesystem with dependencies installed
    via APK. Packages cannot see or modify other packages' build artifacts.
    """
    from owrt.docker_wrapper import run_in_docker
    from owrt.container import is_inside_docker

    # All builds must run in containers - launch Docker if not already inside
    if not is_inside_docker():
        args = ['package', target, package_name]
        if force:
            args.append('-f')
        if no_apk:
            args.append('--no-apk')
        sys.exit(run_in_docker(
            args=args,
            target=target,
            use_toolchain=True,
            verbose=ctx.obj['verbose'],
            jobs=ctx.obj['jobs'],
            ccache=ctx.obj['ccache'],
        ))

    click.echo(f"Building package {package_name} for {target}...")

    config = Config.load_target(target)
    builder = PackageBuilder(
        config,
        verbose=ctx.obj['verbose'],
        jobs=ctx.obj['jobs'],
        use_ccache=ctx.obj['ccache'],
    )
    # single_package=True: skip dep resolution (ninja handles it)
    builder.build_packages([package_name], force=force, create_apk=not no_apk, single_package=True)

    # Create ninja stamp file and cache key for this package
    # Ninja expects stamps in build/{target}/stamp/{name}.stamp
    # The .key file stores the content hash so we can detect changes
    ninja_stamp_dir = config.build_dir / config.name / 'stamp'
    ninja_stamp_dir.mkdir(parents=True, exist_ok=True)
    ninja_stamp = ninja_stamp_dir / f'{package_name}.stamp'
    ninja_key = ninja_stamp_dir / f'{package_name}.key'
    ninja_stamp.touch()

    # Compute and save package content hash as cache key
    pkg = PackageConfig.find_package(package_name)
    if pkg:
        # For subpackages, get the source package
        if isinstance(pkg, SubpackageConfig):
            pkg = pkg.parent
        pkg_hash = compute_package_content_hash(pkg, config.toolchain)
        ninja_key.write_text(pkg_hash)

    click.echo(f"Package {package_name} built successfully")
    if not no_apk:
        click.echo(f"APK packages: {builder.get_apk_dir()}")
        click.echo(f"Repository: {builder.get_repo_dir()}")


@cli.command('apk-index')
@click.argument('target')
@click.pass_context
def apk_index(ctx, target):
    """Generate APK repository index for TARGET.

    This command generates the APKINDEX.tar.gz file for the package repository.
    It should be run after all packages are built to create a usable repository.
    """
    from .apk import APKRepository

    config = Config.load_target(target)
    # Use arch-based path - packages are shared across targets with same arch
    # Structure: apk-repo/{arch}/{arch}/*.apk (APKRepository creates the nested arch dir)
    repo_dir = config.build_dir / 'apk-repo' / config.arch
    arch_dir = repo_dir / config.arch  # APK v3 uses arch-specific subdirs

    if not arch_dir.exists():
        click.echo(f"Repository directory not found: {arch_dir}", err=True)
        click.echo("Build packages first with: ./build.sh firmware", err=True)
        sys.exit(1)

    apk_files = list(arch_dir.glob('*.apk'))
    if not apk_files:
        click.echo(f"No APK packages found in: {arch_dir}", err=True)
        sys.exit(1)

    click.echo(f"Generating APK repository index for {target}...")
    click.echo(f"  Repository: {repo_dir}")
    click.echo(f"  Architecture: {config.arch}")
    click.echo(f"  Packages: {len(apk_files)}")

    repo = APKRepository(
        repo_dir,
        build_dir=config.build_dir,
        arch=config.arch,
        verbose=ctx.obj['verbose']
    )

    if not repo.have_apk():
        click.echo("Error: apk binary not found. Build host tools first: ./build.sh tools", err=True)
        sys.exit(1)

    repo.generate_index(f"OpenWrt {config.name} packages")
    click.echo(f"Repository index generated: {arch_dir / 'packages.adb'}")


@cli.command('download')
@click.argument('target')
@click.option('--package', '-p', help='Download specific package only')
@click.option('--jobs', '-j', default=16, help='Parallel downloads (default: 16)')
@click.pass_context
def download(ctx, target, package, jobs):
    """Download all package sources for TARGET.

    Downloads sources in parallel with OpenWrt-compatible naming
    for sources.openwrt.org mirror fallback support.
    """
    from .download import DownloadManager, download_package_source

    config = Config.load_target(target)

    if package:
        # Download single package
        click.echo(f"Downloading source for {package}...")
        pkg = PackageConfig.find_package(package)
        if not pkg:
            click.echo(f"Package not found: {package}", err=True)
            sys.exit(1)
        result = download_package_source(pkg, config.dl_dir, ctx.obj['verbose'])
        if result:
            click.echo(f"Downloaded: {result}")
        else:
            click.echo(f"No download needed for {package}")
    else:
        # Download all packages
        click.echo(f"Downloading all sources for {target}...")
        manager = DownloadManager(config, verbose=ctx.obj['verbose'])
        results = manager.download_all(max_workers=jobs)
        click.echo(f"Downloads complete: {len(results)} files")


@cli.command('image')
@click.argument('target')
@click.option('--profile', '-p', default='generic', help='Device profile')
@click.pass_context
def image(ctx, target, profile):
    """Generate firmware images for TARGET"""
    from owrt.docker_wrapper import run_in_docker
    from owrt.container import is_inside_docker

    # All builds must run in containers - launch Docker if not already inside
    if not is_inside_docker():
        args = ['image', target, '-p', profile]
        sys.exit(run_in_docker(
            args=args,
            target=target,
            use_toolchain=True,
            verbose=ctx.obj['verbose'],
            jobs=ctx.obj['jobs'],
            ccache=ctx.obj['ccache'],
        ))

    click.echo(f"Generating images for {target} (profile: {profile})...")

    config = Config.load_target(target)
    builder = ImageBuilder(config, verbose=ctx.obj['verbose'])
    builder.build(profile)

    click.echo(f"Images generated in: {config.output_dir}/images/")


@cli.command('clean')
@click.argument('target')
@click.option('--all', 'clean_all', is_flag=True, help='Clean everything including toolchain')
@click.pass_context
def clean(ctx, target, clean_all):
    """Clean build artifacts for TARGET"""
    config = Config.load_target(target)

    if clean_all:
        click.echo(f"Cleaning all build artifacts for {target}...")
        ToolchainBuilder(config).clean()

    KernelBuilder(config).clean()
    PackageBuilder(config).clean()

    click.echo(f"Clean complete for {target}")


# ============================================================================
# Ninja-based build commands
# ============================================================================

@cli.group()
def ninja():
    """Ninja-based parallel build commands"""
    pass


@ninja.command('generate')
@click.argument('target')
@click.option('--profile', '-p', default='generic', help='Device profile')
@click.option('--packages', '-P', multiple=True, help='Additional packages')
@click.pass_context
def ninja_generate(ctx, target, profile, packages):
    """Generate Ninja build file for TARGET"""
    click.echo(f"Generating Ninja build file for {target}...")

    config = Config.load_target(target)

    # Get profile packages
    try:
        profile_data = config.get_profile(profile)
        profile_packages = profile_data.get('packages', [])
    except ValueError:
        profile_packages = []

    # Combine all packages
    all_packages = list(config.default_packages) + profile_packages + list(packages)

    # Resolve dependencies
    click.echo(f"\n[1/2] Resolving dependencies...")
    resolver = DependencyResolver(config)
    plan = resolver.resolve(all_packages)

    resolver.print_plan(plan)

    # Generate Ninja file
    click.echo(f"\n[2/2] Generating build.ninja...")
    generator = NinjaGenerator(config)
    ninja_file = generator.generate(plan)

    click.echo(f"\nNinja file generated: {ninja_file}")
    click.echo(f"\nTo build, run:")
    click.echo(f"  ninja -f {ninja_file}")
    click.echo(f"  # or: python -m owrt ninja run {target}")


@ninja.command('run')
@click.argument('target')
@click.option('--profile', '-p', default='generic', help='Device profile')
@click.option('--packages', '-P', multiple=True, help='Additional packages')
@click.option('--targets', '-t', multiple=True, help='Specific Ninja targets')
@click.option('--force', '-f', is_flag=True, help='Regenerate Ninja file')
@click.pass_context
def ninja_run(ctx, target, profile, packages, targets, force):
    """Run Ninja build for TARGET"""
    from owrt.docker_wrapper import run_in_docker
    from owrt.container import is_inside_docker

    # All builds must run in containers - launch Docker if not already inside
    if not is_inside_docker():
        args = ['ninja', 'run', target, '-p', profile]
        for pkg in packages:
            args.extend(['-P', pkg])
        for t in targets:
            args.extend(['-t', t])
        if force:
            args.append('-f')
        sys.exit(run_in_docker(
            args=args,
            target=target,
            use_toolchain=True,
            verbose=ctx.obj['verbose'],
            jobs=ctx.obj['jobs'],
            ccache=ctx.obj['ccache'],
        ))

    config = Config.load_target(target)
    verbose = ctx.obj['verbose']
    jobs = ctx.obj['jobs']

    ninja_file = config.build_dir / config.name / 'build.ninja'

    # Generate if needed
    if force or not ninja_file.exists():
        click.echo(f"Generating Ninja build file...")

        # Get profile packages
        try:
            profile_data = config.get_profile(profile)
            profile_packages = profile_data.get('packages', [])
        except ValueError:
            profile_packages = []

        all_packages = list(config.default_packages) + profile_packages + list(packages)

        resolver = DependencyResolver(config)
        plan = resolver.resolve(all_packages)

        generator = NinjaGenerator(config)
        ninja_file = generator.generate(plan)

        click.echo(f"Build plan: {plan.to_build_count} to build, {plan.cached_count} cached")

    # Run Ninja
    click.echo(f"\nRunning Ninja build...")
    runner = NinjaRunner(ninja_file, verbose=verbose, jobs=jobs)

    target_list = list(targets) if targets else None
    success = runner.run(target_list)

    if success:
        click.echo("\nBuild completed successfully!")
    else:
        click.echo("\nBuild failed!", err=True)
        sys.exit(1)


@ninja.command('graph')
@click.argument('target')
@click.option('--output', '-o', type=click.Path(), help='Output DOT file')
@click.pass_context
def ninja_graph(ctx, target, output):
    """Generate dependency graph for TARGET"""
    config = Config.load_target(target)

    ninja_file = config.build_dir / config.name / 'build.ninja'
    if not ninja_file.exists():
        click.echo("Ninja file not found. Run 'ninja generate' first.", err=True)
        sys.exit(1)

    output_path = Path(output) if output else config.build_dir / config.name / 'graph.dot'

    runner = NinjaRunner(ninja_file)
    if runner.graph(output_path):
        click.echo(f"Graph written to: {output_path}")
        click.echo(f"\nTo visualize: dot -Tpng {output_path} -o graph.png")
    else:
        click.echo("Failed to generate graph", err=True)
        sys.exit(1)


@ninja.command('plan')
@click.argument('target')
@click.option('--profile', '-p', default='generic', help='Device profile')
@click.option('--packages', '-P', multiple=True, help='Additional packages')
@click.pass_context
def ninja_plan(ctx, target, profile, packages):
    """Show build plan for TARGET (dry-run)"""
    config = Config.load_target(target)

    # Get profile packages
    try:
        profile_data = config.get_profile(profile)
        profile_packages = profile_data.get('packages', [])
    except ValueError:
        profile_packages = []

    all_packages = list(config.default_packages) + profile_packages + list(packages)

    # Resolve dependencies
    resolver = DependencyResolver(config)
    plan = resolver.resolve(all_packages)

    resolver.print_plan(plan)


@cli.command('info')
@click.argument('target')
def info(target):
    """Show information about TARGET"""
    config = Config.load_target(target)

    click.echo(f"Target: {config.name}")
    click.echo(f"  Board: {config.board}")
    click.echo(f"  Subtarget: {config.subtarget}")
    click.echo(f"  Architecture: {config.arch}")
    click.echo(f"  Toolchain: GCC {config.toolchain['gcc_version']}, {config.toolchain['libc']}")
    click.echo(f"  Kernel: {config.kernel['full_version']}")
    click.echo(f"  Default packages: {', '.join(config.default_packages)}")
    click.echo(f"  Profiles: {', '.join(p['name'] for p in config.profiles)}")


if __name__ == '__main__':
    cli()
