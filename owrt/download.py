"""
Download manager for package sources.

Handles downloading package sources with:
- OpenWrt-compatible naming for sources.openwrt.org mirror support
- Parallel downloads via ninja
- Hash verification
- Git source tarball creation
"""

import hashlib
import os
import shutil
import subprocess
import tempfile
import urllib.request
from pathlib import Path
from typing import Optional, List, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

from .config import Config, PackageConfig
from .utils import sha256_file


# OpenWrt mirror for fallback downloads
OPENWRT_MIRROR = "https://sources.openwrt.org"


def get_download_filename(pkg: PackageConfig) -> Optional[str]:
    """Get OpenWrt-compatible download filename for a package.

    For tarballs: uses original filename from URL
    For git: creates {name}-{YYYY.MM.DD}~{commit8}.tar.zst (OpenWrt mirror format)

    Returns None if package has no downloadable source.
    """
    source = pkg.source
    if not source:
        return None

    src_type = source.get('type', 'tarball')

    if src_type == 'none' or src_type == 'local':
        return None

    if src_type == 'tarball':
        url = source.get('url', '')
        if not url:
            return None
        # Use original filename from URL
        return url.split('/')[-1]

    if src_type == 'git':
        # OpenWrt mirror naming: {name}-{YYYY.MM.DD}~{8-char-commit}.tar.zst
        # pkg.version contains the date (e.g., "2025.12.08")
        # source.version contains the git commit hash
        git_version = source.get('version', 'HEAD')
        if git_version == 'HEAD':
            return f"{pkg.name}-git.tar.zst"
        # Use package version (date) and 8-char commit abbreviation
        commit_abbrev = git_version[:8] if len(git_version) > 8 else git_version
        return f"{pkg.name}-{pkg.version}~{commit_abbrev}.tar.zst"

    return None


def get_download_url(pkg: PackageConfig) -> Optional[str]:
    """Get primary download URL for a package."""
    source = pkg.source
    if not source:
        return None

    src_type = source.get('type', 'tarball')

    if src_type == 'tarball':
        return source.get('url')

    if src_type == 'git':
        return source.get('url')

    return None


def get_expected_hash(pkg: PackageConfig) -> Optional[str]:
    """Get expected hash for a package download."""
    source = pkg.source
    if not source:
        return None
    return source.get('sha256') or source.get('hash')


def download_package_source(
    pkg: PackageConfig,
    dl_dir: Path,
    verbose: bool = False,
    use_mirror: bool = True,
) -> Optional[Path]:
    """Download source for a single package.

    Args:
        pkg: Package configuration
        dl_dir: Download directory
        verbose: Print verbose output
        use_mirror: Try sources.openwrt.org as fallback

    Returns:
        Path to downloaded file, or None if no download needed
    """
    filename = get_download_filename(pkg)
    if not filename:
        return None

    dest = dl_dir / filename

    # Already downloaded? Trust existing files.
    # The package builder has been using these successfully, so don't
    # re-verify hashes (which may be outdated in package.yaml)
    if dest.exists():
        if verbose:
            print(f"  {pkg.name}: using cached {filename}")
        return dest

    source = pkg.source
    src_type = source.get('type', 'tarball')

    if src_type == 'tarball':
        url = source.get('url')
        expected_hash = get_expected_hash(pkg)

        # Try primary URL first
        urls_to_try = [url]
        if use_mirror:
            # Add OpenWrt mirror as fallback
            urls_to_try.append(f"{OPENWRT_MIRROR}/{filename}")

        for try_url in urls_to_try:
            try:
                if verbose:
                    print(f"  {pkg.name}: downloading from {try_url}")
                _download_file(try_url, dest, expected_hash)
                return dest
            except Exception as e:
                if verbose:
                    print(f"  {pkg.name}: failed from {try_url}: {e}")
                if dest.exists():
                    dest.unlink()
                continue

        raise RuntimeError(f"Failed to download {pkg.name} from any source")

    elif src_type == 'git':
        url = source.get('url')
        version = source.get('version', 'HEAD')
        expected_hash = get_expected_hash(pkg)

        # Try OpenWrt mirror first if hash is known (faster)
        if use_mirror and expected_hash:
            mirror_url = f"{OPENWRT_MIRROR}/{filename}"
            try:
                if verbose:
                    print(f"  {pkg.name}: trying mirror {mirror_url}")
                _download_file(mirror_url, dest, expected_hash)
                return dest
            except Exception as e:
                if verbose:
                    print(f"  {pkg.name}: mirror failed, cloning from git")
                if dest.exists():
                    dest.unlink()

        # Clone and create tarball
        try:
            if verbose:
                print(f"  {pkg.name}: cloning {url}")
            _git_clone_to_tarball(url, version, dest, source.get('submodules', False))
            return dest
        except Exception as e:
            # Always show git clone failures - they're important
            print(f"  {pkg.name}: git clone failed: {e}")
            if dest.exists():
                dest.unlink()

            # Fallback to OpenWrt mirror if git clone failed
            if expected_hash:
                mirror_url = f"{OPENWRT_MIRROR}/{filename}"
                print(f"  {pkg.name}: falling back to mirror {mirror_url}")
                _download_file(mirror_url, dest, expected_hash)
                return dest
            else:
                raise RuntimeError(f"Git clone failed for {pkg.name} and no hash available for mirror fallback: {e}") from e

    return None


def _download_file(url: str, dest: Path, expected_hash: Optional[str] = None):
    """Download a file with optional hash verification."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    temp_dest = dest.with_suffix('.part')

    try:
        # Use curl or wget for better progress and retry handling
        if shutil.which('curl'):
            subprocess.run(
                ['curl', '-fSL', '--retry', '3', '-o', str(temp_dest), url],
                check=True,
                capture_output=True,
            )
        elif shutil.which('wget'):
            subprocess.run(
                ['wget', '-q', '--tries=3', '-O', str(temp_dest), url],
                check=True,
                capture_output=True,
            )
        else:
            urllib.request.urlretrieve(url, temp_dest)

        # Verify hash
        if expected_hash:
            actual_hash = sha256_file(temp_dest)
            if actual_hash != expected_hash:
                temp_dest.unlink()
                raise RuntimeError(
                    f"Hash mismatch: expected {expected_hash}, got {actual_hash}"
                )

        temp_dest.rename(dest)

    except Exception as e:
        if temp_dest.exists():
            temp_dest.unlink()
        raise


def _git_clone_to_tarball(
    url: str,
    version: str,
    dest: Path,
    submodules: bool = False,
):
    """Clone a git repository and create a tarball."""
    dest.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmpdir:
        clone_dir = Path(tmpdir) / 'src'

        def run_git(cmd, **kwargs):
            """Run git command with proper error handling."""
            result = subprocess.run(cmd, capture_output=True, text=True, **kwargs)
            if result.returncode != 0:
                error_msg = result.stderr.strip() or result.stdout.strip() or "Unknown error"
                raise subprocess.CalledProcessError(
                    result.returncode, cmd, output=result.stdout, stderr=error_msg
                )
            return result

        # Clone with specific commit
        try:
            if version and version != 'HEAD':
                # For specific commits, we need to fetch after clone
                run_git(['git', 'clone', '--depth=1', url, str(clone_dir)])
                run_git(['git', 'fetch', '--depth=1', 'origin', version], cwd=clone_dir)
                run_git(['git', 'checkout', version], cwd=clone_dir)
            else:
                run_git(['git', 'clone', '--depth=1', url, str(clone_dir)])
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Git operation failed for {url}: {e.stderr}") from e

        # Initialize submodules if requested
        if submodules and (clone_dir / '.gitmodules').exists():
            subprocess.run(
                ['git', 'submodule', 'update', '--init', '--recursive', '--depth=1'],
                cwd=clone_dir,
                check=True,
                capture_output=True,
            )

        # Remove .git directory to reduce size
        git_dir = clone_dir / '.git'
        if git_dir.exists():
            shutil.rmtree(git_dir)

        # Create tarball with zstd compression (matches OpenWrt mirror format)
        # Use tar with transform to set archive root directory name
        archive_name = dest.stem.replace('.tar', '')

        # Use zstd compression to match OpenWrt sources mirror
        subprocess.run(
            ['tar', '-C', tmpdir, '--transform', f's,^src,{archive_name},',
             '--zstd', '-cf', str(dest), 'src'],
            check=True,
        )


class DownloadManager:
    """Manages parallel downloads for all packages."""

    def __init__(self, config: Config, verbose: bool = False):
        self.config = config
        self.verbose = verbose
        self.dl_dir = config.dl_dir

    def get_all_downloads(self) -> List[Tuple[PackageConfig, str]]:
        """Get list of all packages that need downloads.

        Returns list of (package, filename) tuples.
        """
        downloads = []

        for pkg in PackageConfig.find_all_packages():
            filename = get_download_filename(pkg)
            if filename:
                downloads.append((pkg, filename))

        return downloads

    def download_all(self, max_workers: int = 16) -> List[Path]:
        """Download all package sources in parallel.

        Args:
            max_workers: Maximum parallel downloads

        Returns:
            List of downloaded file paths
        """
        downloads = self.get_all_downloads()

        if not downloads:
            print("No downloads needed")
            return []

        print(f"Downloading {len(downloads)} sources with {max_workers} parallel downloads...")

        results = []
        failed = []

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(
                    download_package_source,
                    pkg,
                    self.dl_dir,
                    self.verbose,
                ): pkg.name
                for pkg, _ in downloads
            }

            for future in as_completed(futures):
                pkg_name = futures[future]
                try:
                    result = future.result()
                    if result:
                        results.append(result)
                        if self.verbose:
                            print(f"  Downloaded: {pkg_name}")
                except Exception as e:
                    failed.append((pkg_name, str(e)))
                    print(f"  FAILED: {pkg_name}: {e}")

        if failed:
            print(f"\n{len(failed)} downloads failed:")
            for name, error in failed:
                print(f"  - {name}: {error}")

        print(f"Downloaded {len(results)} sources")
        return results

    def download_package(self, package_name: str) -> Optional[Path]:
        """Download source for a single package by name."""
        pkg = PackageConfig.find_package(package_name)
        if not pkg:
            raise ValueError(f"Package not found: {package_name}")

        return download_package_source(pkg, self.dl_dir, self.verbose)
