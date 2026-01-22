"""
Docker container wrapper for owrt CLI.

Allows the owrt CLI to automatically launch itself inside a Docker container
when needed, providing a seamless build experience without requiring the
separate build.sh wrapper script.
"""

import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

from owrt.container import is_inside_docker


def get_project_root() -> Path:
    """Get the project root directory (where owrt/ lives)."""
    return Path(__file__).parent.parent


def get_base_hash() -> str:
    """Compute hash of the base Docker image inputs (Dockerfile)."""
    dockerfile = get_project_root() / 'docker' / 'Dockerfile'
    if not dockerfile.exists():
        return "unknown"
    h = hashlib.sha256()
    h.update(dockerfile.read_bytes())
    return h.hexdigest()[:12]


def get_base_image_tag() -> str:
    """Get the base Docker image tag."""
    return f"openwrt-poc-base:{get_base_hash()}"


def get_toolchain_hash(target: str) -> str:
    """Compute hash of toolchain inputs for a target.
    
    Includes: Dockerfile, toolchain.py, target config, patches.
    """
    project_root = get_project_root()
    h = hashlib.sha256()
    
    # Files that affect toolchain build
    files_to_hash = [
        project_root / 'docker' / 'Dockerfile',
        project_root / 'owrt' / 'toolchain.py',
    ]
    
    # Target config
    target_yaml = project_root / 'targets' / target.replace('-', '/') / 'target.yaml'
    if target_yaml.exists():
        files_to_hash.append(target_yaml)
    
    # Hash all files
    for f in files_to_hash:
        if f.exists():
            h.update(str(f.relative_to(project_root)).encode())
            h.update(f.read_bytes())
    
    # Hash toolchain patches
    toolchain_dir = project_root / 'toolchain'
    if toolchain_dir.exists():
        for patch_dir in ['binutils/patches', 'gcc/patches', 'musl/patches']:
            patch_path = toolchain_dir / patch_dir
            if patch_path.exists():
                for patch in sorted(patch_path.rglob('*.patch')):
                    h.update(str(patch.relative_to(project_root)).encode())
                    h.update(patch.read_bytes())
    
    return h.hexdigest()[:16]


def get_toolchain_image_tag(target: str) -> str:
    """Get the toolchain Docker image tag for a target."""
    return f"openwrt-toolchain:{target}-{get_toolchain_hash(target)}"


def docker_available() -> bool:
    """Check if Docker is available."""
    return shutil.which('docker') is not None


def image_exists(image: str) -> bool:
    """Check if a Docker image exists locally."""
    result = subprocess.run(
        ['docker', 'image', 'inspect', image],
        capture_output=True,
    )
    return result.returncode == 0


def build_base_image(verbose: bool = False) -> None:
    """Build the base Docker image."""
    project_root = get_project_root()
    dockerfile = project_root / 'docker' / 'Dockerfile'
    tag = get_base_image_tag()
    
    print(f"Building base image: {tag}")
    
    cmd = [
        'docker', 'build',
        '-f', str(dockerfile),
        '-t', tag,
        str(project_root / 'docker'),
    ]
    
    result = subprocess.run(
        cmd,
        capture_output=not verbose,
        text=True,
    )
    
    if result.returncode != 0:
        if not verbose and result.stderr:
            print(result.stderr, file=sys.stderr)
        raise RuntimeError(f"Failed to build base image: {tag}")
    
    print(f"Base image built: {tag}")


def build_toolchain_image(target: str, verbose: bool = False) -> None:
    """Build the toolchain Docker image for a target.
    
    Requires the toolchain to already be built in build/toolchain/{target}/.
    """
    project_root = get_project_root()
    base_image = get_base_image_tag()
    tag = get_toolchain_image_tag(target)
    
    # Check if toolchain exists
    toolchain_dir = project_root / 'build' / 'toolchain' / target
    if not toolchain_dir.exists():
        raise RuntimeError(
            f"Toolchain not found at {toolchain_dir}. "
            f"Build it first with: uv run owrt toolchain build {target}"
        )
    
    print(f"Building toolchain image: {tag}")
    
    # Create temporary Dockerfile
    dockerfile_content = f"""\
ARG BASE_IMAGE
FROM ${{BASE_IMAGE}}

ARG TARGET
COPY --chown=builder:builder build/toolchain/${{TARGET}} /build/toolchain/${{TARGET}}

ENV PATH="/build/toolchain/${{TARGET}}/bin:${{PATH}}"
"""
    
    import tempfile
    with tempfile.NamedTemporaryFile(mode='w', suffix='.Dockerfile', delete=False) as f:
        f.write(dockerfile_content)
        temp_dockerfile = f.name
    
    try:
        cmd = [
            'docker', 'build',
            '--build-arg', f'BASE_IMAGE={base_image}',
            '--build-arg', f'TARGET={target}',
            '-f', temp_dockerfile,
            '-t', tag,
            str(project_root),
        ]
        
        result = subprocess.run(
            cmd,
            capture_output=not verbose,
            text=True,
        )
        
        if result.returncode != 0:
            if not verbose and result.stderr:
                print(result.stderr, file=sys.stderr)
            raise RuntimeError(f"Failed to build toolchain image: {tag}")
    finally:
        os.unlink(temp_dockerfile)
    
    print(f"Toolchain image built: {tag}")


def ensure_base_image(verbose: bool = False) -> str:
    """Ensure base image exists, building if necessary. Returns image tag."""
    tag = get_base_image_tag()
    if not image_exists(tag):
        build_base_image(verbose)
    return tag


def ensure_toolchain_image(target: str, verbose: bool = False) -> str:
    """Ensure toolchain image exists, building if necessary. Returns image tag."""
    # First ensure base image
    ensure_base_image(verbose)
    
    tag = get_toolchain_image_tag(target)
    if not image_exists(tag):
        build_toolchain_image(target, verbose)
    return tag


def run_in_docker(
    args: List[str],
    target: Optional[str] = None,
    use_toolchain: bool = False,
    verbose: bool = False,
    jobs: Optional[int] = None,
    ccache: bool = False,
) -> int:
    """Run owrt CLI command inside a Docker container.
    
    Args:
        args: CLI arguments to pass (e.g., ['build', 'armsr-armv8'])
        target: Target name (for toolchain image selection)
        use_toolchain: Whether to use toolchain image (vs base image)
        verbose: Enable verbose output
        jobs: Number of parallel jobs
        ccache: Enable ccache
    
    Returns:
        Exit code from the container
    """
    project_root = get_project_root()
    build_dir = project_root / 'build'
    
    # Ensure build directory exists
    build_dir.mkdir(parents=True, exist_ok=True)
    
    # Select image
    if use_toolchain and target:
        image = ensure_toolchain_image(target, verbose)
    else:
        image = ensure_base_image(verbose)
    
    # Build docker run command
    cmd = [
        'docker', 'run', '--rm',
        '-u', f'{os.getuid()}:{os.getgid()}',
        '-v', f'{project_root}:/openwrt',
        '-v', f'{build_dir}:/build',
        '-w', '/openwrt',
        '-e', 'BUILD_DIR=/build',
        '-e', 'DL_DIR=/build/dl',
    ]
    
    # Add ccache support
    if ccache:
        ccache_dir = build_dir / '.ccache'
        ccache_dir.mkdir(parents=True, exist_ok=True)
        cmd.extend([
            '-v', f'{ccache_dir}:/ccache',
            '-e', 'CCACHE_DIR=/ccache',
            '-e', 'CCACHE_BASEDIR=/openwrt',
        ])
    
    # Add image
    cmd.append(image)
    
    # Build the owrt command (use python3 -m owrt inside container)
    owrt_cmd = ['python3', '-m', 'owrt']
    
    # Add global options (--no-docker to prevent re-wrapping)
    owrt_cmd.append('--no-docker')
    if verbose:
        owrt_cmd.append('-v')
    if jobs:
        owrt_cmd.extend(['-j', str(jobs)])
    if ccache:
        owrt_cmd.append('--ccache')
    
    # Add the actual command and args
    owrt_cmd.extend(args)
    
    cmd.extend(owrt_cmd)
    
    if verbose:
        print(f"Running: {' '.join(cmd)}")
    
    # Run the container
    result = subprocess.run(cmd)
    return result.returncode


def should_use_docker(ctx_obj: dict, force_docker: Optional[bool] = None) -> bool:
    """Determine if we should use Docker for this command.
    
    Args:
        ctx_obj: Click context object with 'docker' key
        force_docker: Override from --docker/--no-docker flag
    
    Returns:
        True if should launch in Docker, False otherwise
    """
    # Check explicit flag
    docker_flag = force_docker if force_docker is not None else ctx_obj.get('docker')
    
    if docker_flag is not None:
        # Explicit flag set
        if docker_flag and is_inside_docker():
            print("Warning: --docker specified but already inside container, ignoring")
            return False
        return docker_flag
    
    # Auto-detect: use Docker if available and not already inside
    if is_inside_docker():
        return False
    
    return docker_available()
