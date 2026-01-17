"""
Utility functions for the build system.
"""

import hashlib
import os
import subprocess
import tarfile
import urllib.request
from pathlib import Path
from typing import List, Optional, Dict


def run_command(
    cmd: List[str],
    cwd: Optional[Path] = None,
    env: Optional[Dict[str, str]] = None,
    verbose: bool = False,
    capture: bool = False,
) -> Optional[str]:
    """
    Run a command and handle errors.

    Args:
        cmd: Command and arguments
        cwd: Working directory
        env: Environment variables
        verbose: Print command and output
        capture: Return stdout instead of printing

    Returns:
        stdout if capture=True, None otherwise

    Raises:
        subprocess.CalledProcessError: If command fails
    """
    cmd_str = ' '.join(str(c) for c in cmd)
    if verbose:
        print(f"  $ {cmd_str}", flush=True)

    if capture:
        # Capture mode - return stdout, show stderr on error
        result = subprocess.run(
            cmd,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(f"Command failed: {cmd_str}", flush=True)
            if result.stderr:
                print(f"stderr:\n{result.stderr}", flush=True)
            raise subprocess.CalledProcessError(
                result.returncode, cmd, result.stdout, result.stderr
            )
        return result.stdout

    elif verbose:
        # Verbose mode - stream output in real-time
        result = subprocess.run(
            cmd,
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        # Print output
        if result.stdout:
            print(result.stdout, flush=True)
        if result.returncode != 0:
            raise subprocess.CalledProcessError(result.returncode, cmd)
        return None

    else:
        # Quiet mode - suppress output but capture for errors
        result = subprocess.run(
            cmd,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(f"Command failed: {cmd_str}", flush=True)
            if result.stdout:
                print(f"stdout:\n{result.stdout}", flush=True)
            if result.stderr:
                print(f"stderr:\n{result.stderr}", flush=True)
            raise subprocess.CalledProcessError(
                result.returncode, cmd, result.stdout, result.stderr
            )
        return None


def download_file(url: str, dest: Path, expected_hash: Optional[str] = None):
    """
    Download a file and optionally verify its hash.

    Args:
        url: URL to download
        dest: Destination path
        expected_hash: Expected SHA256 hash
    """
    # Create parent directory
    dest.parent.mkdir(parents=True, exist_ok=True)

    # Download to temporary file
    temp_dest = dest.with_suffix('.part')

    try:
        urllib.request.urlretrieve(url, temp_dest)

        # Verify hash
        if expected_hash:
            actual_hash = sha256_file(temp_dest)
            if actual_hash != expected_hash:
                raise ValueError(
                    f"Hash mismatch for {dest.name}: "
                    f"expected {expected_hash}, got {actual_hash}"
                )

        # Move to final location
        temp_dest.rename(dest)

    except Exception:
        if temp_dest.exists():
            temp_dest.unlink()
        raise


def sha256_file(path: Path) -> str:
    """Calculate SHA256 hash of a file."""
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        while chunk := f.read(8192):
            h.update(chunk)
    return h.hexdigest()


def extract_archive(archive: Path, dest_dir: Path):
    """
    Extract a tar archive.

    Args:
        archive: Path to archive
        dest_dir: Directory to extract to
    """
    dest_dir.mkdir(parents=True, exist_ok=True)

    # Handle zstd compression (not natively supported by tarfile)
    if archive.suffix == '.zst' or archive.name.endswith('.tar.zst'):
        # Use tar with zstd decompression via subprocess
        result = subprocess.run(
            ['tar', '-I', 'zstd', '-xf', str(archive), '-C', str(dest_dir)],
            capture_output=True,
            text=True
        )
        if result.returncode != 0:
            raise RuntimeError(f"Failed to extract zstd archive: {result.stderr}")
        return

    # Determine compression for other formats
    if archive.suffix == '.xz' or archive.name.endswith('.tar.xz'):
        mode = 'r:xz'
    elif archive.suffix == '.gz' or archive.name.endswith('.tar.gz'):
        mode = 'r:gz'
    elif archive.suffix == '.bz2' or archive.name.endswith('.tar.bz2'):
        mode = 'r:bz2'
    else:
        mode = 'r'

    with tarfile.open(archive, mode) as tar:
        tar.extractall(dest_dir)


def apply_patches(src_dir: Path, patches_dir: Path, verbose: bool = False):
    """
    Apply patches from a directory to source.

    Patches are applied in sorted order by filename.
    Uses -p1 strip level (standard for git-format patches).

    Args:
        src_dir: Source directory to patch
        patches_dir: Directory containing .patch files
        verbose: Print patch names
    """
    if not patches_dir.exists():
        return

    # Find all patch files
    patches = sorted(patches_dir.glob('*.patch'))

    if not patches:
        return

    if verbose:
        print(f"  Applying {len(patches)} patches from {patches_dir.name}/")

    for patch in patches:
        if verbose:
            print(f"    {patch.name}")

        # First check if patch is already applied (dry-run reverse)
        result = subprocess.run(
            ['patch', '-p1', '--dry-run', '-R', '-i', str(patch)],
            cwd=src_dir,
            capture_output=True,
        )
        if result.returncode == 0:
            # Patch already applied, skip
            if verbose:
                print(f"      (already applied, skipping)")
            continue

        # Try to apply with -p1 (standard for git patches)
        result = subprocess.run(
            ['patch', '-p1', '--forward', '-i', str(patch)],
            cwd=src_dir,
            capture_output=True,
        )

        if result.returncode != 0:
            # Check if it's just "already applied" message
            stderr = result.stderr.decode('utf-8', errors='replace')
            stdout = result.stdout.decode('utf-8', errors='replace')
            if 'Reversed (or previously applied)' in stdout or 'already applied' in stderr.lower():
                if verbose:
                    print(f"      (already applied, skipping)")
                continue

            # Real failure - report it
            print(f"    Warning: Failed to apply {patch.name}")
            if verbose:
                print(f"      stdout: {stdout[:200]}")
                print(f"      stderr: {stderr[:200]}")
            # Continue with other patches instead of failing completely
            continue


def merge_kconfig(config_files: List[Path], output: Path):
    """
    Merge multiple kernel config fragments.

    Uses mod_plus logic matching OpenWrt's kconfig.pl:
    - =y takes precedence over =m (built-in beats module)
    - =m won't override existing =y
    - Other values (=n, "is not set") can be overridden normally

    This ensures boot-essential options like CONFIG_EXT4_FS=y stay
    built-in even if kmod configs request them as modules.

    Args:
        config_files: List of config fragment files
        output: Output merged config file
    """
    merged = {}

    def get_value(line: str) -> str:
        """Extract the value from a config line."""
        if line.endswith(' is not set'):
            return 'n'
        if '=' in line:
            return line.split('=', 1)[1]
        return ''

    def should_override(existing: str, new_line: str) -> bool:
        """Determine if new value should override existing (mod_plus logic).

        Rules from OpenWrt's kconfig.pl:
        - If no existing value, always set
        - If existing is 'not set' (#undef), always override
        - If new value is =y, always override (y takes precedence)
        - If existing is =y and new is =m, don't override (y stays)
        """
        if existing is None:
            return True

        existing_val = get_value(existing)
        new_val = get_value(new_line)

        # "is not set" can always be overridden
        if existing_val == 'n':
            return True

        # =y always wins (new value is y, override)
        if new_val == 'y':
            return True

        # If existing is =y and new is =m, don't override
        if existing_val == 'y' and new_val == 'm':
            return False

        # Otherwise, new value overrides
        return True

    for config_file in config_files:
        if not config_file.exists():
            continue

        with open(config_file) as f:
            for line in f:
                line = line.strip()

                # Skip comments and empty lines (but preserve them for readability)
                if not line or line.startswith('#'):
                    # Check for "# CONFIG_FOO is not set" pattern
                    if line.startswith('# CONFIG_') and line.endswith(' is not set'):
                        key = line.split()[1]
                        if should_override(merged.get(key), line):
                            merged[key] = line
                    continue

                # Parse CONFIG_FOO=value
                if '=' in line and line.startswith('CONFIG_'):
                    key = line.split('=')[0]
                    if should_override(merged.get(key), line):
                        merged[key] = line

    # Write merged config
    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, 'w') as f:
        for value in sorted(merged.values()):
            f.write(value + '\n')


def generate_ninja_file(output: Path, rules: List[dict], builds: List[dict]):
    """
    Generate a Ninja build file.

    Args:
        output: Output file path
        rules: List of rule definitions
        builds: List of build statements
    """
    with open(output, 'w') as f:
        f.write("# Auto-generated Ninja build file\n\n")

        # Write rules
        for rule in rules:
            f.write(f"rule {rule['name']}\n")
            f.write(f"  command = {rule['command']}\n")
            if 'description' in rule:
                f.write(f"  description = {rule['description']}\n")
            f.write("\n")

        # Write builds
        for build in builds:
            deps = ' '.join(build.get('deps', []))
            f.write(f"build {build['output']}: {build['rule']} {deps}\n")
            for var, value in build.get('vars', {}).items():
                f.write(f"  {var} = {value}\n")
            f.write("\n")
