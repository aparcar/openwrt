"""
APK package builder - creates APK packages and manages rootfs.

Uses apk-tools to:
- Create .apk packages from built packages
- Install packages into rootfs using apk --root
- Generate package repositories (APKINDEX)

Based on OpenWrt's include/package-pack.mk.
"""

import hashlib
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path
from typing import Optional, List, Dict, Any

from .config import Config, PackageConfig
from .utils import run_command


class APKPackager:
    """Creates APK packages from built packages."""

    def __init__(
        self,
        config: Config,
        apk_binary: Optional[Path] = None,
        verbose: bool = False
    ):
        self.config = config
        self.verbose = verbose

        # Find apk binary
        if apk_binary and apk_binary.exists():
            self.apk_binary = apk_binary
        else:
            # Try to find apk in host-staging
            host_staging = config.build_dir / 'host-staging'
            for try_path in [
                host_staging / 'bin' / 'apk',
                host_staging / 'usr' / 'bin' / 'apk',
                Path('/usr/bin/apk'),
            ]:
                if try_path.exists():
                    self.apk_binary = try_path
                    break
            else:
                self.apk_binary = None

        # Directories - use per-architecture paths since packages are shared
        # across targets with the same architecture (e.g., armsr-armv8 and
        # mediatek-filogic both use aarch64)
        self.packages_dir = config.build_dir / 'packages' / config.arch
        self.staging_dir = config.staging_dir
        self.output_dir = config.build_dir / 'apk-packages' / config.arch
        self.repo_dir = config.build_dir / 'apk-repo' / config.arch

    def have_apk(self) -> bool:
        """Check if apk binary is available."""
        return self.apk_binary is not None and self.apk_binary.exists()

    def strip_binaries(self, directory: Path) -> int:
        """
        Strip ELF binaries and shared libraries in a directory tree.

        Uses the cross-compiler's strip tool to remove debug symbols and
        unnecessary sections, significantly reducing binary sizes.

        Args:
            directory: Root directory to recursively search for binaries

        Returns:
            Number of files stripped
        """
        if not directory.exists():
            return 0

        # Get cross-strip binary
        toolchain_bin = self.config.toolchain_dir / 'bin'
        strip_binary = toolchain_bin / f'{self.config.target_tuple}-strip'
        if not strip_binary.exists():
            if self.verbose:
                print(f"    Warning: strip not found at {strip_binary}, skipping")
            return 0

        stripped_count = 0

        # Find all regular files and check if they're ELF binaries
        for filepath in directory.rglob('*'):
            if not filepath.is_file() or filepath.is_symlink():
                continue

            # Check if file is an ELF binary by reading magic bytes
            try:
                with open(filepath, 'rb') as f:
                    magic = f.read(4)
                    if magic != b'\x7fELF':
                        continue
            except (IOError, PermissionError):
                continue

            # Strip the binary
            try:
                result = subprocess.run(
                    [str(strip_binary), '--strip-unneeded', str(filepath)],
                    capture_output=True,
                    text=True
                )
                if result.returncode == 0:
                    stripped_count += 1
                elif self.verbose:
                    print(f"    Warning: Failed to strip {filepath.name}: {result.stderr}")
            except Exception as e:
                if self.verbose:
                    print(f"    Warning: Error stripping {filepath.name}: {e}")

        return stripped_count

    def create_package(
        self,
        pkg: PackageConfig,
        pkg_dir: Path,
        staging_dir: Path,
    ) -> Optional[Path]:
        """Create an APK package from a built package.

        Args:
            pkg: Package configuration
            pkg_dir: Package build directory (contains src/, build/)
            staging_dir: Package staging directory (contains installed files)

        Returns:
            Path to created .apk file, or None on failure

        Raises:
            RuntimeError: If apk binary is not available
        """
        if not self.have_apk():
            raise RuntimeError(
                f"apk binary not found. Build host tools first: ./build.sh tools\n"
                f"Searched: {self.config.build_dir / 'host-staging' / 'bin' / 'apk'}"
            )

        self.output_dir.mkdir(parents=True, exist_ok=True)

        arch = self._get_apk_arch()
        pkg_name = pkg.name
        version = f"{pkg.version}-r{pkg.release}"

        # APK filename format: name-version.apk
        apk_file = self.output_dir / f"{pkg_name}-{version}.apk"

        # Build metadata key-value pairs for apk v3 mkpkg
        metadata = self._build_metadata(pkg, arch)

        # Strip binaries to reduce package size (removes debug symbols)
        if staging_dir.exists():
            stripped = self.strip_binaries(staging_dir)
            if stripped > 0 and self.verbose:
                print(f"    Stripped {stripped} binaries")

        try:
            # Build the command with --info KEY:VALUE pairs
            cmd = [str(self.apk_binary), 'mkpkg']

            # Add each metadata field as --info KEY:VALUE
            for key, value in metadata.items():
                if value:  # Skip empty values
                    cmd.extend(['--info', f'{key}:{value}'])

            # Add dependencies as space-separated list (APK v3 format)
            if pkg.runtime_deps:
                cmd.extend(['--info', f'depends:{" ".join(pkg.runtime_deps)}'])

            # Add provides as space-separated list
            provides = getattr(pkg, 'provides', [])
            if provides:
                cmd.extend(['--info', f'provides:{" ".join(provides)}'])

            # Add install scripts
            # Scripts are defined in package.yaml under 'scripts' section
            # Format: scripts: { postinst: "script content", preinst: "...", etc }
            #
            # All packages get a default postinst that calls default_postinst from
            # /lib/functions.sh - this enables init.d scripts, creates alternatives,
            # adds users/groups, etc. Custom postinst scripts can override this.
            scripts = getattr(pkg, 'scripts', None) or {}
            if not scripts:
                # Fallback to raw data lookup for PackageConfig
                raw_data = getattr(pkg, '_raw_data', None) or getattr(pkg, '_data', {})
                scripts = raw_data.get('scripts', {})
            
            # Add default postinst if not overridden
            # This calls OpenWrt's default_postinst which handles:
            # - Enabling init.d scripts via rc.common
            # - Creating alternatives symlinks
            # - Adding users/groups from .rusers files
            if 'postinst' not in scripts:
                scripts['postinst'] = '''#!/bin/sh
[ -f "$IPKG_INSTROOT/lib/functions.sh" ] && . "$IPKG_INSTROOT/lib/functions.sh"
type default_postinst >/dev/null 2>&1 && default_postinst "$0" "$@"
'''
            
            # APK script types: pre-install, post-install, pre-deinstall, post-deinstall, trigger
            script_type_map = {
                'preinst': 'pre-install',
                'postinst': 'post-install',
                'prerm': 'pre-deinstall',
                'postrm': 'post-deinstall',
                'trigger': 'trigger',
            }
            for script_name, script_content in scripts.items():
                apk_type = script_type_map.get(script_name, script_name)
                if script_content:
                    # Write script to temp file and reference it
                    with tempfile.NamedTemporaryFile(mode='w', suffix='.sh', delete=False) as f:
                        f.write(script_content)
                        script_file = f.name
                    cmd.extend(['--script', f'{apk_type}:{script_file}'])

            # Add alternatives file for busybox-style symlinks
            # Format: "PRIORITY:TARGET:SOURCE" per line (e.g., "100:/sbin/rmmod:/sbin/kmodloader")
            alternatives = getattr(pkg, 'alternatives', [])
            if alternatives:
                # Create /lib/apk/packages/<pkgname>.alternatives in staging dir
                apk_meta_dir = staging_dir / 'lib' / 'apk' / 'packages'
                apk_meta_dir.mkdir(parents=True, exist_ok=True)
                alt_file = apk_meta_dir / f'{pkg_name}.alternatives'
                alt_file.write_text(' '.join(alternatives))

            # Use --files with the staging directory (apk v3 expects a path)
            if staging_dir.exists() and any(staging_dir.iterdir()):
                cmd.extend(['--files', str(staging_dir)])

            cmd.extend(['--output', str(apk_file)])

            run_command(cmd, verbose=self.verbose)

            if apk_file.exists():
                return apk_file

        except Exception as e:
            print(f"    Error creating APK for {pkg_name}: {e}")

        return None

    def create_dev_package(
        self,
        name: str,
        config: Dict[str, Any],
        files_dir: Path,
        arch: str,
    ) -> Optional[Path]:
        """Create an auto-generated -dev APK package.

        Args:
            name: Package name (e.g., 'libubus-dev')
            config: Package configuration dict with version, description, etc.
            files_dir: Directory containing dev files to package
            arch: Target architecture

        Returns:
            Path to created .apk file, or None on failure
        """
        if not self.have_apk():
            return None

        self.output_dir.mkdir(parents=True, exist_ok=True)

        version = f"{config['version']}-r{config['release']}"
        apk_file = self.output_dir / f"{name}-{version}.apk"

        try:
            cmd = [str(self.apk_binary), 'mkpkg']

            # Add metadata (note: 'section' is not a valid APK field)
            cmd.extend(['--info', f"name:{name}"])
            cmd.extend(['--info', f"version:{version}"])
            cmd.extend(['--info', f"description:{config.get('description', f'{name} development files')}"])
            cmd.extend(['--info', f"arch:{self._get_apk_arch()}"])
            cmd.extend(['--info', f"license:{config.get('license', 'unknown')}"])
            cmd.extend(['--info', f"origin:{name.replace('-dev', '')}"])

            # Add dependencies as space-separated list (APK v3 format)
            deps = [d for d in config.get('dependencies', {}).get('runtime', []) if d]
            if deps:
                cmd.extend(['--info', f'depends:{" ".join(deps)}'])

            # Add files
            if files_dir.exists() and any(files_dir.iterdir()):
                cmd.extend(['--files', str(files_dir)])

            cmd.extend(['--output', str(apk_file)])

            run_command(cmd, verbose=self.verbose)

            if apk_file.exists():
                return apk_file

        except Exception as e:
            print(f"    Error creating dev APK for {name}: {e}")

        return None

    def _build_metadata(self, pkg: PackageConfig, arch: str) -> Dict[str, str]:
        """Build metadata dictionary for apk v3 mkpkg.

        Returns a dict where each key-value pair becomes --info KEY:VALUE

        APK v3 uses these field names:
        - name (not pkgname)
        - version (not pkgver)
        - description (not pkgdesc)
        - arch, license, origin, url, depend, provides
        """
        version = f"{pkg.version}-r{pkg.release}"

        # Get description, taking first line only
        desc = pkg.metadata.get('description', pkg.metadata.get('title', pkg.name))
        if desc:
            desc = desc.strip().split('\n')[0]

        metadata = {
            'name': pkg.name,
            'version': version,
            'description': desc,
            'url': pkg.metadata.get('url', ''),
            'arch': arch,
            'license': pkg.license or 'unknown',
            'origin': pkg.name,
        }

        # Note: dependencies and provides are handled separately in create_package
        # to allow multiple --info depend:xxx options

        return metadata

    def _get_dependencies(self, pkg: PackageConfig) -> List[str]:
        """Get list of runtime dependencies for APK."""
        return pkg.runtime_deps

    def _build_pkginfo(self, pkg: PackageConfig, arch: str) -> str:
        """Build .PKGINFO content for a package (legacy format)."""
        version = f"{pkg.version}-r{pkg.release}"

        lines = [
            f"pkgname = {pkg.name}",
            f"pkgver = {version}",
            f"pkgdesc = {pkg.metadata.get('description', pkg.metadata.get('title', pkg.name)).strip().split(chr(10))[0]}",
            f"url = {pkg.metadata.get('url', '')}",
            f"arch = {arch}",
            f"license = {pkg.license}",
            f"origin = {pkg.name}",
        ]

        # Add dependencies
        for dep in pkg.runtime_deps:
            lines.append(f"depend = {dep}")

        # Add provides (package name itself)
        lines.append(f"provides = {pkg.name}={version}")

        return '\n'.join(lines) + '\n'

    def _create_files_tar(self, staging_dir: Path, output: Path):
        """Create tarball of files from staging directory."""
        with tarfile.open(output, 'w') as tar:
            for item in staging_dir.rglob('*'):
                if item.is_file() or item.is_symlink():
                    arcname = str(item.relative_to(staging_dir))
                    tar.add(item, arcname=arcname)

    def _get_apk_arch(self) -> str:
        """Get APK architecture name."""
        arch_map = {
            'aarch64': 'aarch64',
            'arm': 'armv7',
            'x86_64': 'x86_64',
            'i386': 'x86',
            'mips': 'mips',
            'mipsel': 'mipsel',
            'mips64': 'mips64',
            'riscv64': 'riscv64',
        }
        return arch_map.get(self.config.arch, self.config.arch)


class APKRootfs:
    """Creates rootfs using APK package manager."""

    def __init__(
        self,
        config: Config,
        apk_binary: Optional[Path] = None,
        verbose: bool = False
    ):
        self.config = config
        self.verbose = verbose

        # Find apk binary
        if apk_binary and apk_binary.exists():
            self.apk_binary = apk_binary
        else:
            host_staging = config.build_dir / 'host-staging'
            for try_path in [
                host_staging / 'bin' / 'apk',
                host_staging / 'usr' / 'bin' / 'apk',
                Path('/usr/bin/apk'),
            ]:
                if try_path.exists():
                    self.apk_binary = try_path
                    break
            else:
                self.apk_binary = None

        self.rootfs_dir = config.rootfs_dir
        # Use per-architecture repo directory (packages are shared across targets with same arch)
        self.repo_dir = config.build_dir / 'apk-repo' / config.arch

    def have_apk(self) -> bool:
        """Check if apk binary is available."""
        return self.apk_binary is not None and self.apk_binary.exists()

    def create_rootfs(
        self,
        packages: List[str],
        repo_paths: Optional[List[Path]] = None,
    ) -> Path:
        """Create rootfs by installing packages using APK.

        Args:
            packages: List of package names to install
            repo_paths: List of repository paths (directories containing packages.adb)

        Returns:
            Path to rootfs directory
        """
        # Create fresh rootfs
        if self.rootfs_dir.exists():
            shutil.rmtree(self.rootfs_dir)
        self.rootfs_dir.mkdir(parents=True)

        # Create basic directory structure required by APK and proot
        for d in ['lib/apk/db', 'etc/apk', 'var/cache/apk', 'var/log',
                  'bin', 'sbin', 'usr/bin', 'usr/sbin', 'usr/lib', 'lib',
                  'etc', 'var', 'tmp', 'dev', 'proc', 'sys']:
            (self.rootfs_dir / d).mkdir(parents=True, exist_ok=True)

        if not self.have_apk():
            print("Warning: apk not found, using fallback rootfs assembly")
            return self._fallback_rootfs(packages)

        # Find repository index files (packages.adb in arch-specific subdirs)
        # APK v3 uses packages.adb as the index file format
        repo_indexes = []
        if repo_paths:
            for repo in repo_paths:
                # Check for packages.adb in the repo directory itself or arch subdir
                for index_path in [
                    repo / self.config.arch / 'packages.adb',
                    repo / 'packages.adb',
                ]:
                    if index_path.exists():
                        repo_indexes.append(index_path)
                        break
        else:
            # Use default repo location
            default_index = self.repo_dir / self.config.arch / 'packages.adb'
            if default_index.exists():
                repo_indexes.append(default_index)

        if not repo_indexes:
            print(f"  Warning: No packages.adb found in repository")
            return self._fallback_rootfs(packages)

        # Install packages using fakeroot for root permission simulation.
        # Use --no-scripts during APK install, then run postinst scripts
        # manually with host bash (same approach as OpenWrt's rootfs.mk).
        #
        # APK v3 requires:
        # - --repositories-file /dev/null to disable default repos
        # - file:// URLs pointing directly to packages.adb files
        arch = self.config.arch  # e.g., aarch64, arm, x86_64

        cmd = [
            'fakeroot',
            str(self.apk_binary.resolve()),
            '--root', str(self.rootfs_dir),
            '--arch', arch,
            '--initdb',
            '--allow-untrusted',
            '--no-network',
            '--repositories-file', '/dev/null',
            '--no-scripts',  # Don't run scripts - we'll run them manually with host bash
        ]

        # Add repository index files directly (APK v3 uses packages.adb)
        for index_file in repo_indexes:
            cmd.extend(['--repository', f'file://{index_file.resolve()}'])

        cmd.append('add')
        cmd.extend(packages)

        env = os.environ.copy()

        try:
            run_command(cmd, verbose=self.verbose, env=env)
            print(f"  Installed {len(packages)} packages to rootfs")
            
            # Run postinst scripts manually with host bash
            # This is how OpenWrt handles it in rootfs.mk - scripts are
            # extracted from scripts.tar and run with IPKG_INSTROOT set
            self._run_postinst_scripts()
        except Exception as e:
            print(f"  Warning: APK install failed: {e}")
            return self._fallback_rootfs(packages)

        # Create essential symlinks and set permissions
        self._finalize_rootfs()

        return self.rootfs_dir

    def _generate_list_files(self):
        """Generate .list files from APK installed database.
        
        APK v3 stores file lists in lib/apk/db/installed, but default_postinst
        expects .list files in lib/apk/packages/. We parse the installed db
        and generate .list files so postinst scripts can find init.d entries.
        """
        installed_db = self.rootfs_dir / 'lib' / 'apk' / 'db' / 'installed'
        packages_dir = self.rootfs_dir / 'lib' / 'apk' / 'packages'
        
        if not installed_db.exists():
            return
        
        packages_dir.mkdir(parents=True, exist_ok=True)
        
        # Parse APK v3 installed database
        # Format: P:pkgname, F:dir, R:file (relative to current F:)
        current_pkg = None
        current_dir = ''
        pkg_files = {}
        
        for line in installed_db.read_text().splitlines():
            if line.startswith('P:'):
                current_pkg = line[2:]
                pkg_files[current_pkg] = []
                current_dir = ''
            elif line.startswith('F:') and current_pkg:
                current_dir = '/' + line[2:]
            elif line.startswith('R:') and current_pkg:
                filepath = current_dir + '/' + line[2:]
                pkg_files[current_pkg].append(filepath)
        
        # Write .list files
        for pkgname, files in pkg_files.items():
            if files:
                list_file = packages_dir / f'{pkgname}.list'
                list_file.write_text('\n'.join(files) + '\n')

    def _run_postinst_scripts(self):
        """Run postinst scripts manually with host bash.
        
        APK stores scripts in lib/apk/db/scripts.tar.gz. We extract them
        and run each *.post-install script with IPKG_INSTROOT set so
        they operate on the target rootfs.
        
        This matches OpenWrt's rootfs.mk prepare_rootfs approach.
        """
        import gzip
        
        # First generate .list files so default_postinst can find init.d entries
        self._generate_list_files()
        
        scripts_tar_gz = self.rootfs_dir / 'lib' / 'apk' / 'db' / 'scripts.tar.gz'
        scripts_tar = self.rootfs_dir / 'lib' / 'apk' / 'db' / 'scripts.tar'
        
        if not scripts_tar_gz.exists():
            if self.verbose:
                print("    No scripts.tar.gz found, skipping postinst")
            return
        
        # Decompress scripts.tar.gz
        with gzip.open(scripts_tar_gz, 'rb') as f_in:
            scripts_tar.write_bytes(f_in.read())
        
        # Extract post-install scripts
        scripts_dir = self.rootfs_dir / 'lib' / 'apk' / 'db'
        postinst_scripts = []
        
        with tarfile.open(scripts_tar, 'r') as tar:
            for member in tar.getmembers():
                if member.name.endswith('.post-install'):
                    tar.extract(member, scripts_dir)
                    postinst_scripts.append(scripts_dir / member.name)
        
        if not postinst_scripts:
            if self.verbose:
                print("    No post-install scripts found")
            return
        
        # Run each postinst script with host bash and IPKG_INSTROOT set
        base_env = os.environ.copy()
        base_env['IPKG_INSTROOT'] = str(self.rootfs_dir)
        
        # Find bash on the host
        bash = shutil.which('bash') or '/bin/bash'
        
        failed = []
        for script in sorted(postinst_scripts):
            # Extract package name from script filename
            # Format: pkgname-version.X1hash.post-install
            # e.g., dnsmasq-2.91-r2.X1cfc6d0f39f1014b467f4e59fadf81db9aacb7283.post-install
            name = script.name
            # Remove .post-install suffix
            if name.endswith('.post-install'):
                name = name[:-len('.post-install')]
            # Remove hash part (.X1...)
            if '.X1' in name:
                name = name[:name.index('.X1')]
            # Remove version (-N.N.N-rN or -N-rN)
            # This regex matches version patterns at the end
            match = re.match(r'^(.+?)-\d+[\d.]*-r\d+$', name)
            if match:
                pkgname = match.group(1)
            else:
                pkgname = name
            
            env = base_env.copy()
            env['pkgname'] = pkgname
            
            try:
                result = subprocess.run(
                    [bash, str(script)],
                    env=env,
                    capture_output=True,
                    text=True,
                    cwd=str(self.rootfs_dir)
                )
                if result.returncode != 0:
                    failed.append((script.name, result.returncode, result.stderr))
                elif self.verbose:
                    print(f"    Ran: {script.name} (pkg={pkgname})")
            except Exception as e:
                failed.append((script.name, -1, str(e)))
        
        if failed:
            print(f"    Warning: {len(failed)} postinst scripts failed:")
            for name, code, err in failed[:5]:  # Show first 5 failures
                print(f"      {name}: exit {code}")
                if err and self.verbose:
                    print(f"        {err[:100]}")
        
        # Clean up extracted scripts from tar (like OpenWrt does)
        for script in postinst_scripts:
            try:
                script.unlink()
            except Exception:
                pass
        
        # Re-compress scripts.tar
        with open(scripts_tar, 'rb') as f_in:
            with gzip.open(scripts_tar_gz, 'wb') as f_out:
                f_out.write(f_in.read())
        scripts_tar.unlink()

    def _fallback_rootfs(self, packages: List[str]) -> Path:
        """Fallback rootfs assembly without APK (copies from staging)."""
        # This is the current behavior - copy from staging directory
        staging_dir = self.config.staging_dir

        if staging_dir.exists():
            for item in staging_dir.rglob('*'):
                if item.is_file():
                    rel_path = item.relative_to(staging_dir)
                    dst_path = self.rootfs_dir / rel_path
                    dst_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(item, dst_path)

        self._finalize_rootfs()
        return self.rootfs_dir

    def _finalize_rootfs(self):
        """Finalize rootfs with symlinks and permissions."""
        # Create basic directory structure
        for d in ['bin', 'dev', 'etc', 'etc/init.d', 'etc/config',
                  'lib', 'lib/firmware', 'lib/modules',
                  'mnt', 'opt', 'overlay', 'proc', 'rom', 'root',
                  'run', 'sbin', 'sys', 'tmp', 'usr/bin', 'usr/lib',
                  'usr/sbin', 'var', 'var/lock', 'var/log', 'var/run', 'www']:
            (self.rootfs_dir / d).mkdir(parents=True, exist_ok=True)

        # Create essential symlinks
        symlinks = [
            ('lib/ld-musl-aarch64.so.1', 'libc.so'),
            ('bin/sh', 'busybox'),
            ('bin/ash', 'busybox'),
            ('sbin/init', '../bin/busybox'),
            # /init symlink for initramfs boot (kernel looks for /init first)
            ('init', '/sbin/init'),
        ]

        for link, target in symlinks:
            link_path = self.rootfs_dir / link
            if not link_path.exists() and not link_path.is_symlink():
                link_path.parent.mkdir(parents=True, exist_ok=True)
                try:
                    link_path.symlink_to(target)
                except Exception:
                    pass

        # Set permissions (skip symlinks to avoid permission errors)
        for d in ['bin', 'sbin', 'usr/bin', 'usr/sbin']:
            bin_dir = self.rootfs_dir / d
            if bin_dir.exists():
                for f in bin_dir.iterdir():
                    if f.is_file() and not f.is_symlink():
                        try:
                            f.chmod(0o755)
                        except (PermissionError, OSError):
                            pass  # Skip files we can't chmod

        try:
            (self.rootfs_dir / 'root').chmod(0o700)
        except (PermissionError, OSError):
            pass
        try:
            (self.rootfs_dir / 'tmp').chmod(0o1777)
        except (PermissionError, OSError):
            pass


class APKRepository:
    """Manages APK package repository.

    APK v3 expects repositories to have architecture-specific subdirectories:
    <repo>/
      aarch64/
        packages.adb      # Repository index created by 'apk mkndx'
        package1.apk
        package2.apk
      x86_64/
        ...
    """

    def __init__(
        self,
        repo_dir: Path,
        build_dir: Optional[Path] = None,
        apk_binary: Optional[Path] = None,
        arch: str = 'aarch64',
        verbose: bool = False
    ):
        self.arch = arch
        self.repo_dir = repo_dir
        self.verbose = verbose

        # Find apk binary
        if apk_binary and apk_binary.exists():
            self.apk_binary = apk_binary
        else:
            # Search in host-staging and system paths
            search_paths = [Path('/usr/bin/apk')]
            if build_dir:
                search_paths = [
                    build_dir / 'host-staging' / 'bin' / 'apk',
                    build_dir / 'host-staging' / 'usr' / 'bin' / 'apk',
                ] + search_paths

            for try_path in search_paths:
                if try_path.exists():
                    self.apk_binary = try_path
                    break
            else:
                self.apk_binary = None

    def _get_arch_dir(self) -> Path:
        """Get the architecture-specific subdirectory."""
        return self.repo_dir / self.arch

    def add_package(self, apk_file: Path):
        """Add a package to the repository (in arch-specific subdir)."""
        arch_dir = self._get_arch_dir()
        arch_dir.mkdir(parents=True, exist_ok=True)

        # Copy package to arch-specific repo dir
        dst = arch_dir / apk_file.name
        shutil.copy2(apk_file, dst)

    def have_apk(self) -> bool:
        """Check if apk binary is available."""
        return self.apk_binary is not None and self.apk_binary.exists()

    def generate_index(self, description: str = "OpenWrt Package Repository"):
        """Generate APK repository index.

        This creates packages.adb in the architecture-specific subdir
        that APK uses to find packages.

        Raises:
            RuntimeError: If apk binary is not available
        """
        if not self.have_apk():
            raise RuntimeError(
                "apk binary not found. Build host tools first: ./build.sh tools"
            )

        arch_dir = self._get_arch_dir()
        arch_dir.mkdir(parents=True, exist_ok=True)

        # Find all .apk files in arch-specific directory
        apk_files = list(arch_dir.glob('*.apk'))
        if not apk_files:
            print(f"Warning: No packages in repository ({arch_dir})")
            return

        # APK v3 uses packages.adb as the index filename
        index_file = arch_dir / 'packages.adb'

        # APK v3 uses 'mkndx' to create repository index
        # --allow-untrusted is needed for unsigned packages
        cmd = [
            str(self.apk_binary), 'mkndx',
            '--allow-untrusted',
            '--output', str(index_file),
            '--description', description,
        ] + [str(f) for f in apk_files]

        run_command(cmd, cwd=arch_dir, verbose=self.verbose)
        print(f"  Generated APKINDEX with {len(apk_files)} packages")
