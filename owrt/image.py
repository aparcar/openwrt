"""
Image builder - generates firmware images.

Handles:
- Assembling root filesystem from staging
- Creating squashfs/ext4/initramfs images
- Building FIT images with kernel + DTB + rootfs
- Creating UBI volumes for NAND flash
- Appending sysupgrade metadata
"""

import gzip
import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional, Dict, Any, List

from .config import Config, PackageConfig
from .kernel import KernelBuilder
from .package import PackageBuilder
from .fit import FITBuilder, UBIBuilder, UBIFSBuilder, MetadataBuilder
from .apk import APKRootfs
from .bootloader import BootloaderBuilder
from .utils import run_command
from .pipeline import ImagePipeline, PipelineContext, PIPELINE_STEPS


class ImageBuilder:
    """Generates firmware images."""

    # Version info (would come from build config in full implementation)
    VERSION_DIST = "openwrt"
    VERSION_NUMBER = "SNAPSHOT"

    def __init__(self, config: Config, verbose: bool = False):
        self.config = config
        self.verbose = verbose

        # Paths
        self.rootfs_dir = config.rootfs_dir
        self.images_dir = config.images_dir
        self.build_dir = config.build_dir / 'image' / config.name

        # Get kernel and package builders
        self.kernel_builder = KernelBuilder(config, verbose=verbose)
        self.package_builder = PackageBuilder(config, verbose=verbose)

        # Image builders
        self.fit_builder = FITBuilder(arch=config.arch, verbose=verbose)
        self.metadata_builder = MetadataBuilder(
            target=f"{config.board}/{config.subtarget}",
            board=config.name,
            host_staging=config.build_dir / 'host-staging',
            verbose=verbose,
        )

        # Bootloader builder (for MediaTek targets)
        self.bootloader_builder = BootloaderBuilder(config, verbose=verbose)

    def _get_image_prefix(self) -> str:
        """Get the image prefix following OpenWrt naming convention.

        Format: <dist>-<version>-<board>-<subtarget>
        Example: openwrt-SNAPSHOT-mediatek-filogic
        """
        return f"{self.VERSION_DIST}-{self.VERSION_NUMBER}-{self.config.board}-{self.config.subtarget}"

    def _get_device_image_prefix(self, profile_name: str) -> str:
        """Get device-specific image prefix.

        Format: <image_prefix>-<device>
        Example: openwrt-SNAPSHOT-mediatek-filogic-openwrt_one
        """
        return f"{self._get_image_prefix()}-{profile_name}"

    def _get_image_name(self, profile_name: str, image_type: str, ext: str = '') -> str:
        """Get full image name following OpenWrt convention.

        Format: <device_prefix>-<type>.<ext>
        Example: openwrt-SNAPSHOT-mediatek-filogic-openwrt_one-sysupgrade.itb
        """
        name = f"{self._get_device_image_prefix(profile_name)}-{image_type}"
        if ext:
            name = f"{name}.{ext}"
        return name

    def build(self, profile_name: str = 'generic'):
        """Build firmware images for a profile."""
        profile = self.config.get_profile(profile_name)
        print(f"Generating images for profile: {profile_name}")

        # Create directories
        self.rootfs_dir.mkdir(parents=True, exist_ok=True)
        self.images_dir.mkdir(parents=True, exist_ok=True)
        self.build_dir.mkdir(parents=True, exist_ok=True)

        # Assemble root filesystem
        self._assemble_rootfs()

        # Build rootfs images (squashfs, etc.)
        rootfs_images = self._build_rootfs_images(profile)

        # Generate images based on profile
        for image_spec in profile.get('images', []):
            self._generate_image(image_spec, profile, rootfs_images)

        # Generate artifacts (boot loaders, factory images)
        for artifact in profile.get('artifacts', []):
            self._generate_artifact(artifact, profile, rootfs_images)

        print(f"Images generated in: {self.images_dir}")

    def _assemble_rootfs(self):
        """Assemble root filesystem using APK package installation."""
        print("  Assembling root filesystem...")

        # Get list of packages to install
        packages = list(self.config.default_packages)

        # Use APKRootfs to install packages
        apk_rootfs = APKRootfs(self.config, verbose=self.verbose)

        if apk_rootfs.have_apk():
            print(f"    Using APK to install {len(packages)} packages...")
            apk_rootfs.create_rootfs(packages)
        else:
            # Fallback: copy from staging
            print("    Warning: APK not available, using staging fallback...")
            self._assemble_rootfs_fallback()

        # Note: Kernel modules are installed via kmod-* APK packages
        # Only modules explicitly listed in default_packages or profile packages
        # will be installed. This keeps the rootfs minimal.

        # Create users and groups from package definitions
        self._create_users_and_groups(packages)

        # Generate release info files
        self._generate_release_files()

        # Create device nodes (requires root)
        self._create_device_nodes()

        # Count files
        file_count = sum(1 for _ in self.rootfs_dir.rglob('*') if _.is_file())
        print(f"    Root filesystem: {file_count} files")

    def _assemble_rootfs_fallback(self):
        """Fallback rootfs assembly from staging (when APK unavailable)."""
        # Start fresh
        if self.rootfs_dir.exists():
            shutil.rmtree(self.rootfs_dir)
        self.rootfs_dir.mkdir(parents=True)

        # Create basic directory structure
        for d in ['bin', 'dev', 'etc', 'etc/init.d', 'etc/config',
                  'lib', 'lib/firmware', 'lib/modules',
                  'mnt', 'opt', 'overlay', 'proc', 'rom', 'root',
                  'run', 'sbin', 'sys', 'tmp', 'usr/bin', 'usr/lib',
                  'usr/sbin', 'var', 'var/lock', 'var/log', 'var/run', 'www']:
            (self.rootfs_dir / d).mkdir(parents=True, exist_ok=True)

        # Copy from staging
        staging_dir = self.package_builder.get_staging_dir()
        if staging_dir.exists():
            self._copy_tree(staging_dir, self.rootfs_dir)

        # Create essential symlinks
        self._create_symlinks()

        # Set permissions
        self._set_permissions()

    def _copy_tree(self, src: Path, dst: Path):
        """Copy directory tree, merging with existing."""
        for item in src.rglob('*'):
            if item.is_file():
                rel_path = item.relative_to(src)
                dst_path = dst / rel_path
                dst_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, dst_path)

    def _create_symlinks(self):
        """Create essential symlinks in rootfs."""
        # Determine musl loader name based on architecture
        # Format: ld-musl-<arch>.so.1
        musl_arch_map = {
            'aarch64': 'aarch64',
            'arm': 'armhf',  # or 'arm' depending on float ABI
            'x86_64': 'x86_64',
            'i386': 'i386',
            'mips': 'mips-sf',
            'mipsel': 'mipsel-sf',
            'mips64': 'mips64',
        }
        musl_arch = musl_arch_map.get(self.config.arch, self.config.arch)
        musl_loader = f'lib/ld-musl-{musl_arch}.so.1'

        symlinks = [
            # Common library symlinks (arch-specific musl loader)
            (musl_loader, 'libc.so'),
            # Busybox symlinks (basic set)
            ('bin/sh', 'busybox'),
            ('bin/ash', 'busybox'),
            ('sbin/init', '../bin/busybox'),
        ]

        for link, target in symlinks:
            link_path = self.rootfs_dir / link
            if not link_path.exists() and not link_path.is_symlink():
                link_path.parent.mkdir(parents=True, exist_ok=True)
                try:
                    link_path.symlink_to(target)
                except Exception:
                    pass

    def _set_permissions(self):
        """Set correct permissions on rootfs files."""
        # Make binaries executable (skip symlinks to avoid permission errors)
        for d in ['bin', 'sbin', 'usr/bin', 'usr/sbin']:
            bin_dir = self.rootfs_dir / d
            if bin_dir.exists():
                for f in bin_dir.iterdir():
                    if f.is_file() and not f.is_symlink():
                        try:
                            f.chmod(0o755)
                        except (PermissionError, OSError):
                            pass

        # Secure sensitive directories
        try:
            (self.rootfs_dir / 'root').chmod(0o700)
        except (PermissionError, OSError):
            pass
        try:
            (self.rootfs_dir / 'tmp').chmod(0o1777)
        except (PermissionError, OSError):
            pass

    def _generate_release_files(self):
        """Generate /etc/openwrt_release and /etc/openwrt_version files."""
        import subprocess
        from datetime import datetime

        # Get git revision
        try:
            revision = subprocess.check_output(
                ['git', 'rev-parse', '--short', 'HEAD'],
                cwd=self.config.openwrt_dir,
                stderr=subprocess.DEVNULL
            ).decode().strip()
        except Exception:
            revision = 'unknown'

        # Version info
        distrib_id = 'OpenWrt'
        version = 'SNAPSHOT'
        target = f'{self.config.board}/{self.config.subtarget}'
        arch = self.config.arch
        build_date = datetime.now().strftime('%Y-%m-%d')

        # Generate /etc/openwrt_release
        release_content = f"""DISTRIB_ID='{distrib_id}'
DISTRIB_RELEASE='{version}'
DISTRIB_REVISION='r{revision}'
DISTRIB_TARGET='{target}'
DISTRIB_ARCH='{arch}'
DISTRIB_DESCRIPTION='{distrib_id} {version} r{revision}'
DISTRIB_TAINTS=''
"""
        release_file = self.rootfs_dir / 'etc' / 'openwrt_release'
        release_file.parent.mkdir(parents=True, exist_ok=True)
        release_file.write_text(release_content)

        # Generate /etc/openwrt_version
        version_content = f'r{revision}\n'
        version_file = self.rootfs_dir / 'etc' / 'openwrt_version'
        version_file.write_text(version_content)

        # Generate /usr/lib/os-release (standard Linux format)
        os_release_content = f"""NAME="{distrib_id}"
VERSION="{version}"
ID="openwrt"
ID_LIKE="lede openwrt"
PRETTY_NAME="{distrib_id} {version}"
VERSION_ID="{version}"
HOME_URL="https://openwrt.org/"
BUG_REPORT_URL="https://bugs.openwrt.org/"
SUPPORT_URL="https://forum.openwrt.org/"
BUILD_ID="{build_date}"
OPENWRT_BOARD="{target}"
OPENWRT_ARCH="{arch}"
OPENWRT_TAINTS=""
OPENWRT_DEVICE_MANUFACTURER="OpenWrt"
OPENWRT_DEVICE_MANUFACTURER_URL="https://openwrt.org/"
OPENWRT_DEVICE_PRODUCT="Generic"
OPENWRT_DEVICE_REVISION="v0"
OPENWRT_RELEASE="{distrib_id} {version} r{revision}"
"""
        os_release_file = self.rootfs_dir / 'usr' / 'lib' / 'os-release'
        os_release_file.parent.mkdir(parents=True, exist_ok=True)
        os_release_file.write_text(os_release_content)

        # Create symlink /etc/os-release -> /usr/lib/os-release
        etc_os_release = self.rootfs_dir / 'etc' / 'os-release'
        if not etc_os_release.exists():
            try:
                etc_os_release.symlink_to('../usr/lib/os-release')
            except Exception:
                pass

        print("    Generated release info files")

    def _create_users_and_groups(self, packages: List[str]):
        """Create users and groups from package userid declarations.

        Parses userid entries in format: "user=uid:group=gid" or ":group=gid"
        and creates entries in /etc/passwd, /etc/shadow, and /etc/group.
        """
        # Collect all userid entries from packages
        userids = []

        for pkg_name in packages:
            # Use PackageConfig.find_package which searches all package locations
            pkg = PackageConfig.find_package(pkg_name)
            if pkg:
                # Get userid from the package (main or subpackage)
                if hasattr(pkg, 'userid') and pkg.userid:
                    userids.extend(pkg.userid)

        if not userids:
            return

        # Parse userids and collect users/groups to create
        users = {}  # name -> (uid, gid, home, shell)
        groups = {}  # name -> gid

        for userid in userids:
            # Format: "user=uid:group=gid" or ":group=gid"
            parts = userid.split(':')
            user_part = parts[0] if len(parts) > 0 else ''
            group_part = parts[1] if len(parts) > 1 else ''

            # Parse user
            if user_part and '=' in user_part:
                uname, uid = user_part.split('=', 1)
                if uname and uid:
                    users[uname] = (int(uid), None, f'/var/run/{uname}', '/bin/false')

            # Parse group
            if group_part and '=' in group_part:
                gname, gid = group_part.split('=', 1)
                if gname and gid:
                    groups[gname] = int(gid)
                    # Link user to group if both exist
                    if user_part and '=' in user_part:
                        uname = user_part.split('=')[0]
                        if uname in users:
                            uid, _, home, shell = users[uname]
                            users[uname] = (uid, int(gid), home, shell)

        # Read existing passwd and group files
        passwd_file = self.rootfs_dir / 'etc' / 'passwd'
        shadow_file = self.rootfs_dir / 'etc' / 'shadow'
        group_file = self.rootfs_dir / 'etc' / 'group'

        existing_users = set()
        existing_groups = set()

        if passwd_file.exists():
            for line in passwd_file.read_text().splitlines():
                if line and not line.startswith('#'):
                    existing_users.add(line.split(':')[0])

        if group_file.exists():
            for line in group_file.read_text().splitlines():
                if line and not line.startswith('#'):
                    existing_groups.add(line.split(':')[0])

        # Create groups
        group_lines = []
        for gname, gid in sorted(groups.items(), key=lambda x: x[1]):
            if gname not in existing_groups:
                group_lines.append(f'{gname}:x:{gid}:')

        if group_lines:
            with open(group_file, 'a') as f:
                f.write('\n'.join(group_lines) + '\n')

        # Create users
        passwd_lines = []
        shadow_lines = []
        for uname, (uid, gid, home, shell) in sorted(users.items(), key=lambda x: x[1][0]):
            if uname not in existing_users:
                gid = gid if gid else uid
                passwd_lines.append(f'{uname}:x:{uid}:{gid}:{uname}:{home}:{shell}')
                shadow_lines.append(f'{uname}:x:0:0:99999:7:::')

        if passwd_lines:
            with open(passwd_file, 'a') as f:
                f.write('\n'.join(passwd_lines) + '\n')

        if shadow_lines:
            with open(shadow_file, 'a') as f:
                f.write('\n'.join(shadow_lines) + '\n')

        created = len([u for u in users if u not in existing_users]) + len([g for g in groups if g not in existing_groups])
        if created:
            print(f"    Created {len(users)} users and {len(groups)} groups")

    def _create_device_nodes(self):
        """Create essential device nodes in /dev for early boot.

        These are needed before devtmpfs is mounted. Modern Linux systems
        mount devtmpfs at /dev during early boot, but some nodes are needed
        even earlier (e.g., /dev/console for kernel messages).
        """
        import os

        dev_dir = self.rootfs_dir / 'dev'
        dev_dir.mkdir(parents=True, exist_ok=True)

        # Essential device nodes: (name, type, major, minor, mode)
        # type: 'c' for char, 'b' for block
        devices = [
            ('console', 'c', 5, 1, 0o600),   # Kernel console
            ('tty', 'c', 5, 0, 0o666),       # Controlling terminal
            ('null', 'c', 1, 3, 0o666),      # Null device
            ('zero', 'c', 1, 5, 0o666),      # Zero device
            ('urandom', 'c', 1, 9, 0o666),   # Random device
            ('random', 'c', 1, 8, 0o666),    # Random device
            ('ptmx', 'c', 5, 2, 0o666),      # PTY master multiplexer
            ('ttyS0', 'c', 4, 64, 0o660),    # Serial port
            ('ttyAMA0', 'c', 204, 64, 0o660),# ARM AMBA serial (QEMU virt)
        ]

        created = 0
        for name, dev_type, major, minor, mode in devices:
            node_path = dev_dir / name
            if node_path.exists():
                continue

            try:
                # Create device node
                # os.mknod requires root privileges
                if dev_type == 'c':
                    dev_mode = mode | 0o020000  # S_IFCHR
                else:
                    dev_mode = mode | 0o060000  # S_IFBLK
                dev_num = os.makedev(major, minor)
                os.mknod(str(node_path), dev_mode, dev_num)
                created += 1
            except PermissionError:
                # Can't create device nodes without root
                if created == 0:
                    print("    Warning: Cannot create device nodes (need root)")
                break
            except Exception as e:
                print(f"    Warning: Failed to create /dev/{name}: {e}")

        if created > 0:
            print(f"    Created {created} device nodes in /dev")

        # Create /dev/pts directory for PTY slaves
        pts_dir = dev_dir / 'pts'
        pts_dir.mkdir(exist_ok=True)

    def _build_rootfs_images(self, profile: Dict[str, Any]) -> Dict[str, Path]:
        """Build rootfs images (squashfs, ext4, etc.)."""
        images = {}

        # Determine which filesystems are needed
        filesystems = set()
        for img in profile.get('images', []):
            fs = img.get('filesystem')
            if fs and fs not in ('initramfs',):
                filesystems.add(fs)

        # Build each filesystem type
        for fs in filesystems:
            if fs == 'squashfs':
                images['squashfs'] = self._build_squashfs()
            elif fs == 'ext4':
                images['ext4'] = self._build_ext4()
            elif fs == 'ubifs':
                images['ubifs'] = self._build_ubifs(profile)

        return images

    def _build_squashfs(self) -> Path:
        """Build squashfs root filesystem image."""
        print("  Building squashfs rootfs...")
        output = self.build_dir / 'rootfs.squashfs'

        # Determine compression filter based on arch
        bcj_filter = {
            'aarch64': 'arm',
            'arm': 'arm',
            'mips': 'mips',
            'mipsel': 'mips',
            'x86_64': 'x86',
            'i386': 'x86',
        }.get(self.config.arch, 'arm')

        cmd = [
            'mksquashfs',
            str(self.rootfs_dir),
            str(output),
            '-noappend',
            '-comp', 'xz',
            '-Xbcj', bcj_filter,
            '-b', '256K',
        ]

        try:
            run_command(cmd, verbose=self.verbose)
            print(f"    Created: {output.name} ({output.stat().st_size // 1024}KB)")
        except subprocess.CalledProcessError as e:
            print(f"    Warning: squashfs build failed: {e}")

        return output

    def _build_ext4(self, size_mb: int = 256) -> Path:
        """Build ext4 root filesystem image populated with rootfs contents."""
        print("  Building ext4 rootfs...")
        output = self.build_dir / 'rootfs.ext4'

        # Use mke2fs -d to create and populate ext4 in one step
        # This avoids needing to mount the image
        try:
            run_command([
                'mke2fs',
                '-t', 'ext4',
                '-d', str(self.rootfs_dir),  # Source directory
                '-L', 'rootfs',
                '-O', '^metadata_csum,^64bit',  # Compatibility options
                '-F',  # Force
                '-b', '4096',  # Block size
                str(output),
                f'{size_mb}M',  # Size
            ], verbose=self.verbose)
        except subprocess.CalledProcessError:
            # Fallback: create empty ext4 (old method)
            print("    Warning: mke2fs -d failed, creating empty ext4")
            run_command([
                'dd', 'if=/dev/zero', f'of={output}',
                'bs=1M', f'count={size_mb}',
            ], verbose=self.verbose)
            run_command([
                'mkfs.ext4', '-F', '-L', 'rootfs',
                '-O', '^metadata_csum', str(output),
            ], verbose=self.verbose)

        print(f"    Created: {output.name} ({size_mb}MB)")

        return output

    def _build_ubifs(self, profile: Dict[str, Any]) -> Path:
        """Build UBIFS root filesystem image for NAND flash.

        UBIFS (Unsorted Block Image File System) is designed for raw NAND
        flash devices. It runs on top of UBI (Unsorted Block Images) which
        handles wear leveling and bad block management.

        Args:
            profile: Device profile containing UBI/UBIFS configuration

        Returns:
            Path to generated UBIFS image
        """
        print("  Building UBIFS rootfs...")
        output = self.build_dir / 'rootfs.ubifs'

        # Get UBI configuration from profile
        ubi_config = profile.get('ubi', {})
        block_size_str = ubi_config.get('blocksize', '128k')
        page_size = ubi_config.get('pagesize', 2048)

        # Parse block size to bytes
        block_size = block_size_str.upper()
        if block_size.endswith('K'):
            block_size_bytes = int(block_size[:-1]) * 1024
        elif block_size.endswith('M'):
            block_size_bytes = int(block_size[:-1]) * 1024 * 1024
        else:
            block_size_bytes = int(block_size)

        # Calculate LEB size: PEB - 2 * page_size
        leb_size = UBIFSBuilder.calculate_leb_size(block_size_bytes, page_size)

        # Get UBIFS-specific options from profile
        ubifs_config = profile.get('ubifs', {})
        max_leb_cnt = ubifs_config.get('max_leb_cnt', 4096)
        compression = ubifs_config.get('compression', 'zlib')

        staging_dir = self.config.build_dir / 'host-staging'

        ubifs_builder = UBIFSBuilder(
            min_io_size=page_size,
            leb_size=leb_size,
            max_leb_cnt=max_leb_cnt,
            compression=compression,
            staging_dir=staging_dir,
            verbose=self.verbose,
        )

        try:
            ubifs_builder.build_ubifs(
                rootfs_dir=self.rootfs_dir,
                output=output,
                space_fixup=True,
                squash_uids=True,
            )
            print(f"    Created: {output.name} ({output.stat().st_size // 1024}KB)")
            print(f"      LEB size: {leb_size} bytes ({leb_size // 1024}KiB)")
            print(f"      Min I/O: {page_size} bytes")
            print(f"      Compression: {compression}")
        except subprocess.CalledProcessError as e:
            print(f"    Warning: UBIFS build failed: {e}")
        except FileNotFoundError:
            print(f"    Warning: mkfs.ubifs not found (build mtd-utils host tool)")

        return output

    def _generate_image(
        self,
        image_spec: Dict[str, Any],
        profile: Dict[str, Any],
        rootfs_images: Dict[str, Path],
    ):
        """Generate a single image."""
        profile_name = profile.get('name', 'generic')
        # Get image type from spec (e.g., 'sysupgrade', 'initramfs')
        image_type = image_spec.get('type', 'combined')
        # Get file extension from the spec name (e.g., 'sysupgrade.itb' -> 'itb')
        spec_name = image_spec['name']
        ext = spec_name.split('.')[-1] if '.' in spec_name else ''

        # Generate OpenWrt-style image name
        image_name = self._get_image_name(profile_name, image_type, ext)

        print(f"  Generating {image_name}...")

        # Check for pipeline definition - modern approach
        pipeline_def = image_spec.get('pipeline')
        if pipeline_def:
            self._generate_with_pipeline(image_name, image_spec, profile, rootfs_images)
            return

        # Legacy image type handling
        if image_type == 'initramfs':
            self._generate_initramfs(image_name, image_spec, profile)
        elif image_type == 'sysupgrade':
            self._generate_sysupgrade(image_name, image_spec, profile, rootfs_images)
        elif image_type == 'combined':
            # For EFI targets, use combined-efi; otherwise use sysupgrade/FIT
            if self.config.kernel.get('efi_stub'):
                self._generate_combined_efi(image_name, image_spec, profile, rootfs_images)
            else:
                self._generate_sysupgrade(image_name, image_spec, profile, rootfs_images)
        elif image_type == 'rootfs':
            self._generate_rootfs_image(image_name, image_spec, rootfs_images)
        elif image_type == 'kernel':
            self._generate_kernel_image(image_name, image_spec, profile)

    def _generate_with_pipeline(
        self,
        image_name: str,
        image_spec: Dict[str, Any],
        profile: Dict[str, Any],
        rootfs_images: Dict[str, Path],
    ):
        """Generate image using pipeline definition.

        Pipeline definitions in YAML:
            images:
              - name: sysupgrade.itb
                pipeline:
                  - kernel                    # Start with kernel
                  - compress: gzip            # Compress with gzip
                  - fit:                      # Create FIT image
                      compression: gzip
                      rootfs: external
                  - metadata: append          # Append sysupgrade metadata
        """
        pipeline_def = image_spec.get('pipeline', [])
        if not pipeline_def:
            print(f"    Error: No pipeline definition")
            return

        # Create output path
        output = self.images_dir / image_name

        # Get inputs
        kernel = self.kernel_builder.get_kernel_path()
        dtb = self._get_profile_dtb(profile)
        filesystem = image_spec.get('filesystem', 'squashfs')
        rootfs = rootfs_images.get(filesystem)

        # Get initrd if specified
        initrd = None
        if image_spec.get('initrd') or image_spec.get('with_initrd'):
            initrd_path = self.build_dir / 'initramfs.cpio.gz'
            if initrd_path.exists():
                initrd = initrd_path

        # Create pipeline context
        work_dir = self.build_dir / 'pipeline' / image_name.replace('.', '_')
        work_dir.mkdir(parents=True, exist_ok=True)

        ctx = PipelineContext(
            kernel=kernel,
            dtb=dtb,
            rootfs=rootfs,
            initrd=initrd,
            kernel_load_addr=self.config.kernel.get('load_address', '0x44000000'),
            kernel_entry_addr=self.config.kernel.get('entry_address',
                self.config.kernel.get('load_address', '0x44000000')),
            dtb_load_addr=profile.get('dts_load_address'),
            arch=self.config.arch,
            kernel_version=self.config.kernel.get('version', '6.12'),
            target=f"{self.config.board}/{self.config.subtarget}",
            board=profile.get('name', self.config.name),
            profile=profile.get('name', 'generic'),
            work_dir=work_dir,
            output_dir=self.images_dir,
            artifacts={},
            verbose=self.verbose,
        )

        # Execute pipeline
        pipeline = ImagePipeline(verbose=self.verbose)
        try:
            result = pipeline.execute(pipeline_def, ctx)

            # Copy result to final output
            if result and result.exists():
                shutil.copy2(result, output)
                print(f"    Created: {image_name} ({output.stat().st_size // 1024}KB)")
            else:
                print(f"    Error: Pipeline produced no output")
        except Exception as e:
            print(f"    Error: Pipeline failed - {e}")
            if self.verbose:
                import traceback
                traceback.print_exc()

    def _generate_initramfs(
        self,
        image_name: str,
        image_spec: Dict[str, Any],
        profile: Dict[str, Any],
    ):
        """Generate initramfs image with kernel containing embedded rootfs.

        This follows OpenWrt's approach of embedding the initramfs directly
        into the kernel via CONFIG_INITRAMFS_SOURCE.
        """
        output = self.images_dir / image_name

        # Check if we should use embedded initramfs (OpenWrt style) or FIT image
        use_embedded = image_spec.get('embedded', True)

        if use_embedded:
            # Rebuild kernel with embedded initramfs (OpenWrt approach)
            initramfs_kernel = self.kernel_builder.rebuild_with_initramfs(self.rootfs_dir)

            if initramfs_kernel.exists():
                # For some targets, we might need to wrap in FIT image
                dtb = self._get_profile_dtb(profile)
                if dtb and dtb.exists() and image_spec.get('fit', False):
                    # Build FIT image with kernel (initramfs already embedded)
                    kernel_spec = image_spec.get('kernel', {})
                    compression = kernel_spec.get('compression', 'none')
                    load_addr = kernel_spec.get('load_address',
                        self.config.kernel.get('load_address', '0x44000000'))

                    kernel_compressed = self._compress_kernel(initramfs_kernel, compression)
                    dtb_load_addr = profile.get('dts_load_address')

                    its_file = self.build_dir / f'{image_name}.its'
                    self.fit_builder.generate_its(
                        output=its_file,
                        kernel=kernel_compressed,
                        dtb=dtb,
                        compression=compression,
                        kernel_load_addr=load_addr,
                        dtb_load_addr=dtb_load_addr,
                    )
                    self.fit_builder.build_fit(its_file, output)
                else:
                    # Just copy the kernel (for EFI-style boot like armsr)
                    shutil.copy(initramfs_kernel, output)

                if output.exists():
                    size_kb = output.stat().st_size // 1024
                    print(f"    Created: {image_name} ({size_kb}KB)")
            else:
                print(f"    Error: Failed to build initramfs kernel")
        else:
            # Legacy: Create separate CPIO archive (FIT with external initrd)
            cpio_file = self.build_dir / 'initramfs.cpio'
            cpio_gz = self.build_dir / 'initramfs.cpio.gz'

            # Build cpio using find | cpio
            cpio_cmd = f"cd {self.rootfs_dir} && find . | cpio -o -H newc -R 0:0 > {cpio_file}"
            subprocess.run(cpio_cmd, shell=True, check=True)

            # Compress
            with open(cpio_file, 'rb') as f_in:
                with gzip.open(cpio_gz, 'wb') as f_out:
                    shutil.copyfileobj(f_in, f_out)

            # Get kernel and DTB paths
            kernel = self.kernel_builder.get_kernel_path()
            dtb = self._get_profile_dtb(profile)

            if not kernel.exists():
                print(f"    Error: Kernel not found at {kernel}")
                return

            # Get kernel config from image spec
            kernel_spec = image_spec.get('kernel', {})
            compression = kernel_spec.get('compression', 'lzma')
            load_addr = kernel_spec.get('load_address',
                self.config.kernel.get('load_address', '0x44000000'))

            # Compress kernel if needed
            kernel_compressed = self._compress_kernel(kernel, compression)

            # Build FIT image with initrd
            if dtb and dtb.exists():
                dtb_load_addr = profile.get('dts_load_address')

                its_file = self.build_dir / f'{image_name}.its'
                self.fit_builder.generate_its(
                    output=its_file,
                    kernel=kernel_compressed,
                    dtb=dtb,
                    initrd=cpio_gz,
                    compression=compression,
                    kernel_load_addr=load_addr,
                    dtb_load_addr=dtb_load_addr,
                )
                self.fit_builder.build_fit(its_file, output)
            else:
                # No DTB, just copy kernel + initramfs
                shutil.copy(kernel_compressed, output)

            # Pad if specified
            pad_to = image_spec.get('pad_to')
            if pad_to:
                self._pad_image(output, pad_to)

            if output.exists():
                print(f"    Created: {image_name} ({output.stat().st_size // 1024}KB)")

    def _generate_sysupgrade(
        self,
        image_name: str,
        image_spec: Dict[str, Any],
        profile: Dict[str, Any],
        rootfs_images: Dict[str, Path],
    ):
        """Generate sysupgrade image (FIT with external rootfs)."""
        output = self.images_dir / image_name

        # Get kernel and DTB
        kernel = self.kernel_builder.get_kernel_path()
        dtb = self._get_profile_dtb(profile)

        if not kernel.exists():
            print(f"    Error: Kernel not found at {kernel}")
            return

        # Get rootfs
        filesystem = image_spec.get('filesystem', 'squashfs')
        rootfs = rootfs_images.get(filesystem)
        if not rootfs or not rootfs.exists():
            print(f"    Warning: {filesystem} rootfs not available")
            rootfs = None

        # Get kernel config
        kernel_spec = image_spec.get('kernel', {})
        compression = kernel_spec.get('compression', 'gzip')
        load_addr = kernel_spec.get('load_address', self.config.kernel.get('load_address', '0x44000000'))

        # Compress kernel
        kernel_compressed = self._compress_kernel(kernel, compression)

        # Sync rootfs to page boundary
        if rootfs:
            rootfs_sync = rootfs.with_suffix('.pagesync')
            run_command([
                'dd', f'if={rootfs}', f'of={rootfs_sync}',
                'bs=4096', 'conv=sync',
            ], verbose=self.verbose)
            rootfs = rootfs_sync

        # Build FIT image
        if dtb and dtb.exists():
            dtb_load_addr = profile.get('dts_load_address')

            its_file = self.build_dir / f'{image_name}.its'
            self.fit_builder.generate_its(
                output=its_file,
                kernel=kernel_compressed,
                dtb=dtb,
                rootfs=rootfs,
                compression=compression,
                kernel_load_addr=load_addr,
                dtb_load_addr=dtb_load_addr,
            )
            self.fit_builder.build_fit(its_file, output, external_data=True)
        else:
            print(f"    Warning: No DTB found, cannot create FIT image")
            return

        # Append metadata if requested
        if image_spec.get('metadata', False):
            supported_devices = [profile['name']]
            self.metadata_builder.append_metadata(output, supported_devices)

        if output.exists():
            print(f"    Created: {image_name} ({output.stat().st_size // 1024}KB)")

    def _generate_combined_efi(
        self,
        image_name: str,
        image_spec: Dict[str, Any],
        profile: Dict[str, Any],
        rootfs_images: Dict[str, Path],
    ):
        """Generate combined EFI disk image with kernel and rootfs.

        Creates a bootable disk image with:
        - GPT partition table
        - Partition 1: EFI System Partition (FAT32) with kernel
        - Partition 2: Root filesystem (ext4)

        This format is used for EFI-capable systems like armsr-armv8.
        """
        # Determine output path
        compress = image_name.endswith('.gz')
        base_name = image_name[:-3] if compress else image_name
        output = self.images_dir / base_name
        output_gz = self.images_dir / image_name if compress else None

        # Get kernel
        kernel = self.kernel_builder.get_kernel_path()
        if not kernel.exists():
            print(f"    Error: Kernel not found at {kernel}")
            return

        # Get rootfs image
        filesystem = image_spec.get('filesystem', 'ext4')
        rootfs = rootfs_images.get(filesystem)

        # Parse size spec (e.g., "512M")
        size_spec = image_spec.get('size', '512M')
        total_size_mb = self._parse_size_mb(size_spec)

        # Partition sizes
        # ESP needs to fit: kernel (22MB) + bootaa64.efi copy (22MB) + room for DTBs
        esp_size_mb = 64  # EFI System Partition
        rootfs_size_mb = total_size_mb - esp_size_mb - 1  # 1MB for GPT headers

        print(f"    Creating {total_size_mb}MB disk image...")
        print(f"      ESP: {esp_size_mb}MB, rootfs: {rootfs_size_mb}MB")

        # Create empty disk image
        run_command([
            'dd', 'if=/dev/zero', f'of={output}',
            'bs=1M', f'count={total_size_mb}',
        ], verbose=self.verbose)

        # Create GPT partition table using sgdisk (or fallback to parted)
        try:
            self._create_gpt_partitions_sgdisk(output, esp_size_mb, rootfs_size_mb)
        except Exception as e:
            print(f"    Warning: sgdisk failed ({e}), trying parted...")
            self._create_gpt_partitions_parted(output, esp_size_mb, rootfs_size_mb)

        # Create and populate EFI System Partition
        esp_img = self.build_dir / 'esp.img'
        self._create_esp(esp_img, kernel, esp_size_mb)

        # Write ESP to partition 1 (starts at 1MB for alignment)
        run_command([
            'dd', f'if={esp_img}', f'of={output}',
            'bs=1M', 'seek=1', 'conv=notrunc',
        ], verbose=self.verbose)

        # Write rootfs to partition 2
        if rootfs and rootfs.exists():
            rootfs_offset_mb = 1 + esp_size_mb
            run_command([
                'dd', f'if={rootfs}', f'of={output}',
                'bs=1M', f'seek={rootfs_offset_mb}', 'conv=notrunc',
            ], verbose=self.verbose)

        # Compress if needed
        if compress:
            print(f"    Compressing to {image_name}...")
            with open(output, 'rb') as f_in:
                with gzip.open(output_gz, 'wb') as f_out:
                    shutil.copyfileobj(f_in, f_out)
            output.unlink()  # Remove uncompressed version
            print(f"    Created: {image_name} ({output_gz.stat().st_size // 1024 // 1024}MB)")
        else:
            print(f"    Created: {image_name} ({output.stat().st_size // 1024 // 1024}MB)")

    def _parse_size_mb(self, size_spec: str) -> int:
        """Parse size specification (e.g., '512M', '1G') to MB."""
        size_spec = size_spec.strip().upper()
        if size_spec.endswith('G'):
            return int(size_spec[:-1]) * 1024
        elif size_spec.endswith('M'):
            return int(size_spec[:-1])
        elif size_spec.endswith('K'):
            return int(size_spec[:-1]) // 1024
        else:
            return int(size_spec) // (1024 * 1024)  # Assume bytes

    def _create_gpt_partitions_sgdisk(self, disk: Path, esp_size_mb: int, rootfs_size_mb: int):
        """Create GPT partition table using sgdisk."""
        # Clear existing partition table
        run_command(['sgdisk', '-Z', str(disk)], verbose=self.verbose)

        # Create EFI System Partition (type EF00)
        run_command([
            'sgdisk',
            '-n', f'1:2048:+{esp_size_mb}M',  # Start at sector 2048 (1MB)
            '-t', '1:EF00',  # EFI System Partition
            '-c', '1:kernel',  # Partition name
            str(disk),
        ], verbose=self.verbose)

        # Create rootfs partition (type 8300 - Linux filesystem)
        run_command([
            'sgdisk',
            '-n', f'2:0:0',  # Use remaining space
            '-t', '2:8300',  # Linux filesystem
            '-c', '2:rootfs',  # Partition name
            str(disk),
        ], verbose=self.verbose)

    def _create_gpt_partitions_parted(self, disk: Path, esp_size_mb: int, rootfs_size_mb: int):
        """Create GPT partition table using parted (fallback)."""
        esp_end = 1 + esp_size_mb  # ESP ends at this MB offset

        run_command([
            'parted', '-s', str(disk),
            'mklabel', 'gpt',
            'mkpart', 'kernel', 'fat32', '1MiB', f'{esp_end}MiB',
            'set', '1', 'esp', 'on',
            'mkpart', 'rootfs', 'ext4', f'{esp_end}MiB', '100%',
        ], verbose=self.verbose)

    def _create_esp(self, output: Path, kernel: Path, size_mb: int):
        """Create EFI System Partition image with kernel and startup script.

        Creates an ESP with:
        - /efi/openwrt/<kernel> - Linux kernel
        - /efi/boot/<kernel> - Copy in standard location
        - /startup.nsh - UEFI shell script to boot kernel automatically

        The startup.nsh approach is simpler than GRUB for the PoC.
        OpenWrt uses GRUB for more features (boot menu, failsafe).
        """
        # Create FAT32 filesystem image
        run_command([
            'dd', 'if=/dev/zero', f'of={output}',
            'bs=1M', f'count={size_mb}',
        ], verbose=self.verbose)

        run_command([
            'mkfs.fat', '-F', '32', '-n', 'KERNEL', str(output),
        ], verbose=self.verbose)

        # Create EFI boot directory structure and copy kernel
        # Use mtools to copy files without mounting
        kernel_name = self.config.image.get('kernel_name', 'Image')
        efi_kernel_path = f'::/efi/openwrt/{kernel_name}'
        efi_boot_kernel = f'::/efi/boot/{kernel_name}'

        # Create directories using mmd
        for dir_path in ['::/efi', '::/efi/openwrt', '::/efi/boot']:
            try:
                run_command(['mmd', '-i', str(output), dir_path], verbose=self.verbose)
            except Exception:
                pass  # Directory may already exist

        # Copy kernel to EFI paths
        run_command([
            'mcopy', '-i', str(output), str(kernel), efi_kernel_path,
        ], verbose=self.verbose)

        # Copy kernel to standard UEFI boot location
        # Since our kernel has EFI_STUB, it's a valid EFI application
        # UEFI firmware will try to load /EFI/Boot/boot<arch>.efi by default
        # - aarch64: bootaa64.efi
        # - x86_64: bootx64.efi
        # - i386: bootia32.efi
        efi_boot_file = self.config.image.get('efi_boot_file', 'bootaa64.efi')
        efi_boot_default = f'::/efi/boot/{efi_boot_file}'
        try:
            run_command([
                'mcopy', '-i', str(output), str(kernel), efi_boot_default,
            ], verbose=self.verbose)
        except Exception:
            pass

        # Create startup.nsh to boot kernel automatically
        # This runs when UEFI shell starts (if no other boot option works)
        # Format: filesystem selection, then kernel path with kernel command line
        startup_nsh = self.build_dir / 'startup.nsh'
        kernel_cmdline = self.config.image.get('cmdline', 'earlycon console=ttyAMA0')

        # UEFI shell uses backslashes for paths and CRLF line endings
        # Root partition is partition 2 (GPT partition numbering)
        # Use /dev/vda2 for virtio (QEMU) or /dev/sda2 for SCSI/SATA
        # PARTLABEL requires kernel support that may not work in all environments
        # fs0: is typically the first FAT partition (our ESP)
        # Note: UEFI shell requires CRLF line endings
        startup_lines = [
            '@echo -off',
            'echo Booting OpenWrt...',
            'fs0:',
            f'\\efi\\openwrt\\{kernel_name} root=/dev/vda2 rootwait {kernel_cmdline}',
        ]
        startup_content = '\r\n'.join(startup_lines) + '\r\n'
        startup_nsh.write_text(startup_content)

        # Copy startup.nsh to root of ESP
        run_command([
            'mcopy', '-i', str(output), str(startup_nsh), '::/startup.nsh',
        ], verbose=self.verbose)

        print(f"      Created ESP ({size_mb}MB) with kernel and startup.nsh")

    def _generate_rootfs_image(
        self,
        image_name: str,
        image_spec: Dict[str, Any],
        rootfs_images: Dict[str, Path],
    ):
        """Generate standalone rootfs image."""
        filesystem = image_spec.get('filesystem', 'squashfs')
        rootfs = rootfs_images.get(filesystem)

        if not rootfs or not rootfs.exists():
            print(f"    Error: {filesystem} rootfs not available")
            return

        output = self.images_dir / image_name

        # Copy and optionally compress
        if image_name.endswith('.gz'):
            with open(rootfs, 'rb') as f_in:
                with gzip.open(output, 'wb') as f_out:
                    shutil.copyfileobj(f_in, f_out)
        else:
            shutil.copy(rootfs, output)

        print(f"    Created: {image_name}")

    def _generate_kernel_image(
        self,
        image_name: str,
        image_spec: Dict[str, Any],
        profile: Dict[str, Any],
    ):
        """Generate standalone kernel image."""
        kernel = self.kernel_builder.get_kernel_path()
        output = self.images_dir / image_name

        if kernel.exists():
            shutil.copy(kernel, output)
            print(f"    Created: {image_name}")
        else:
            print(f"    Error: Kernel not found")

    def _generate_artifact(
        self,
        artifact: Dict[str, Any],
        profile: Dict[str, Any],
        rootfs_images: Dict[str, Path],
    ):
        """Generate boot artifacts (BL2, FIP, factory images)."""
        profile_name = profile.get('name', 'generic')
        artifact_name = artifact['name']
        artifact_type = artifact['type']

        # Generate OpenWrt-style artifact name
        # Format: openwrt-<version>-<board>-<subtarget>-<device>-<artifact_name>
        ext = artifact_name.split('.')[-1] if '.' in artifact_name else ''
        base_name = artifact_name.rsplit('.', 1)[0] if '.' in artifact_name else artifact_name
        name = f"{self._get_device_image_prefix(profile_name)}-{base_name}"
        if ext:
            name = f"{name}.{ext}"

        print(f"  Generating artifact: {name}...")

        if artifact_type == 'ubi':
            self._generate_ubi_artifact(name, artifact, profile, rootfs_images)
        elif artifact_type == 'combined':
            self._generate_combined_artifact(name, artifact, profile, rootfs_images)
        elif artifact_type == 'bl2':
            self._generate_bl2_artifact(name, artifact, profile)
        elif artifact_type == 'fip':
            self._generate_fip_artifact(name, artifact, profile)
        else:
            print(f"    Unknown artifact type: {artifact_type}")

    def _generate_bl2_artifact(
        self,
        name: str,
        artifact: Dict[str, Any],
        profile: Dict[str, Any],
    ):
        """Generate BL2 (preloader) artifact from TF-A."""
        variant = artifact.get('variant', '')
        if not variant:
            print(f"    Error: No variant specified for BL2 artifact")
            return

        output = self.images_dir / name

        # Map artifact variant to TFA variant
        # Artifact variants like 'nor-ddr4' need SoC prefix
        hardware = profile.get('hardware', {})
        soc = hardware.get('soc', '').lower()
        if soc.startswith('mt'):
            soc_prefix = soc.rstrip('ab').lower()
            tfa_variant = f'{soc_prefix}-{variant}'
        else:
            tfa_variant = variant

        # Check if already built
        bl2_path = self.bootloader_builder.get_bl2_path(tfa_variant)

        if not bl2_path:
            # Try to build
            try:
                self.bootloader_builder.build_tfa(tfa_variant)
                bl2_path = self.bootloader_builder.get_bl2_path(tfa_variant)
            except Exception as e:
                print(f"    Warning: Failed to build TF-A {tfa_variant}: {e}")
                print(f"    Note: TF-A build requires cross-compiler and host tools")
                return

        if bl2_path and bl2_path.exists():
            shutil.copy(bl2_path, output)
            print(f"    Created: {name} ({output.stat().st_size // 1024}KB)")
        else:
            print(f"    Warning: BL2 not available for {tfa_variant}")

    def _generate_fip_artifact(
        self,
        name: str,
        artifact: Dict[str, Any],
        profile: Dict[str, Any],
    ):
        """Generate FIP (Firmware Image Package) artifact from U-Boot + TF-A."""
        variant = artifact.get('variant', '')
        if not variant:
            print(f"    Error: No variant specified for FIP artifact")
            return

        output = self.images_dir / name

        # Map artifact variant to U-Boot variant
        # Map known variants
        variant_map = {
            'openwrt_one-snand': 'mt7981_openwrt_one-snand',
            'openwrt_one-nor': 'mt7981_openwrt_one-nor',
        }
        uboot_variant = variant_map.get(variant, variant)

        # Check if already built
        fip_path = self.bootloader_builder.get_fip_path(uboot_variant)

        if not fip_path:
            # Try to build
            try:
                self.bootloader_builder.build_uboot(uboot_variant)
                fip_path = self.bootloader_builder.get_fip_path(uboot_variant)
            except Exception as e:
                print(f"    Warning: Failed to build U-Boot {uboot_variant}: {e}")
                print(f"    Note: U-Boot build requires TF-A, cross-compiler, and fiptool")
                return

        if fip_path and fip_path.exists():
            shutil.copy(fip_path, output)
            print(f"    Created: {name} ({output.stat().st_size // 1024}KB)")
        else:
            print(f"    Warning: FIP not available for {uboot_variant}")

    def _generate_ubi_artifact(
        self,
        name: str,
        artifact: Dict[str, Any],
        profile: Dict[str, Any],
        rootfs_images: Dict[str, Path],
    ):
        """Generate UBI image artifact."""
        output = self.images_dir / name

        # Get UBI config from profile
        ubi_config = profile.get('ubi', {})
        block_size = ubi_config.get('blocksize', '128k')
        page_size = ubi_config.get('pagesize', 2048)
        ubi_opts = ubi_config.get('options', '')

        # Build volume list
        volumes = []
        for vol in ubi_config.get('volumes', []):
            vol_def = {
                'name': vol['name'],
                'type': vol.get('type', 'dynamic'),
            }
            if 'image' in vol:
                image_path = vol['image']
                # Resolve image path
                for search_dir in [self.images_dir, self.build_dir]:
                    candidate = search_dir / Path(image_path).name
                    if candidate.exists():
                        vol_def['image'] = str(candidate)
                        break
            volumes.append(vol_def)

        # Add sysupgrade image as fit volume
        content = artifact.get('content')
        if content:
            sysupgrade_img = None
            for search_dir in [self.images_dir, self.build_dir]:
                # First try exact match
                exact_path = search_dir / content
                if exact_path.exists():
                    sysupgrade_img = exact_path
                    break
                # Then try glob pattern for device-prefixed files
                import glob
                pattern = str(search_dir / f'*{content}')
                matches = glob.glob(pattern)
                if matches:
                    sysupgrade_img = Path(matches[0])
                    break
            if sysupgrade_img and sysupgrade_img.exists():
                volumes.append({
                    'name': 'fit',
                    'type': 'dynamic',
                    'image': str(sysupgrade_img),
                    'autoresize': True,
                })

        if volumes:
            staging_dir = self.config.build_dir / 'host-staging'
            ubi_builder = UBIBuilder(
                block_size=block_size,
                page_size=page_size,
                staging_dir=staging_dir,
                verbose=self.verbose,
            )

            extra_opts = ubi_opts.split() if ubi_opts else None
            try:
                ubi_builder.create_ubi_image(output, volumes, extra_opts)
                print(f"    Created: {name} ({output.stat().st_size // 1024}KB)")
            except subprocess.CalledProcessError as e:
                print(f"    Warning: UBI build failed (ubinize required): {e}")
            except FileNotFoundError:
                print(f"    Warning: ubinize not found, skipping UBI image")
        else:
            print(f"    Warning: No volumes defined for UBI image")

    def _generate_combined_artifact(
        self,
        name: str,
        artifact: Dict[str, Any],
        profile: Dict[str, Any],
        rootfs_images: Dict[str, Path],
    ):
        """Generate combined artifact (concatenated parts with padding)."""
        output = self.images_dir / name
        output.parent.mkdir(parents=True, exist_ok=True)

        # Start with empty file
        output.write_bytes(b'')
        description = artifact.get('description', '')
        if description:
            print(f"    {description}")

        # Get hardware info for variant mapping
        hardware = profile.get('hardware', {})
        soc = hardware.get('soc', '').lower()
        soc_prefix = soc.rstrip('ab').lower() if soc.startswith('mt') else ''

        for part in artifact.get('parts', []):
            part_type = part.get('type')
            pad_to = part.get('pad_to')

            if part_type == 'bl2':
                variant = part.get('variant', '')
                tfa_variant = f'{soc_prefix}-{variant}' if soc_prefix else variant

                # Try to get pre-built BL2
                bl2_path = self.bootloader_builder.get_bl2_path(tfa_variant)
                if not bl2_path:
                    try:
                        self.bootloader_builder.build_tfa(tfa_variant)
                        bl2_path = self.bootloader_builder.get_bl2_path(tfa_variant)
                    except Exception as e:
                        print(f"      Part bl2 ({variant}): build failed - {e}")

                if bl2_path and bl2_path.exists():
                    with open(output, 'ab') as out:
                        out.write(bl2_path.read_bytes())
                    print(f"      Part bl2 ({variant}): {bl2_path.stat().st_size // 1024}KB")
                else:
                    print(f"      Part bl2 ({variant}): not available")

            elif part_type == 'fip':
                variant = part.get('variant', '')
                # Map known variants
                variant_map = {
                    'openwrt_one-snand': 'mt7981_openwrt_one-snand',
                    'openwrt_one-nor': 'mt7981_openwrt_one-nor',
                }
                uboot_variant = variant_map.get(variant, variant)

                fip_path = self.bootloader_builder.get_fip_path(uboot_variant)
                if not fip_path:
                    try:
                        self.bootloader_builder.build_uboot(uboot_variant)
                        fip_path = self.bootloader_builder.get_fip_path(uboot_variant)
                    except Exception as e:
                        print(f"      Part fip ({variant}): build failed - {e}")

                if fip_path and fip_path.exists():
                    with open(output, 'ab') as out:
                        out.write(fip_path.read_bytes())
                    print(f"      Part fip ({variant}): {fip_path.stat().st_size // 1024}KB")
                else:
                    print(f"      Part fip ({variant}): not available")

            elif part_type == 'ubi':
                content = part.get('content')
                if content:
                    # For UBI parts in combined artifacts, use the already-generated factory.ubi
                    # which contains the sysupgrade image
                    import glob
                    content_path = None
                    # First look for factory.ubi (already generated)
                    for pattern in [f'*factory.ubi', f'*{content}']:
                        matches = glob.glob(str(self.images_dir / pattern))
                        if matches:
                            content_path = Path(matches[0])
                            break
                    if content_path and content_path.exists():
                        with open(output, 'ab') as out:
                            out.write(content_path.read_bytes())
                        print(f"      Part ubi: {content_path.stat().st_size // 1024}KB")

            elif part_type == 'image':
                source = part.get('source')
                if source:
                    source_path = self.images_dir / source
                    if source_path.exists():
                        with open(output, 'ab') as out:
                            out.write(source_path.read_bytes())
                        print(f"      Part image ({source}): {source_path.stat().st_size // 1024}KB")

            elif part_type == 'eeprom':
                eeprom_name = part.get('name', '')
                print(f"      Part eeprom ({eeprom_name}): requires calibration data")

            # Pad if specified
            if pad_to and output.exists():
                self._pad_image(output, pad_to)

        if output.exists() and output.stat().st_size > 0:
            print(f"    Created: {name} ({output.stat().st_size // 1024}KB)")

    def _get_profile_dtb(self, profile: Dict[str, Any]) -> Optional[Path]:
        """Get DTB path for a profile."""
        dts_name = profile.get('dts')
        if not dts_name:
            return None

        dtbs_dir = self.kernel_builder.get_dtbs_dir()
        if dtbs_dir.exists():
            # Try direct match
            dtb = dtbs_dir / f'{dts_name}.dtb'
            if dtb.exists():
                return dtb
            # Try in subdirectories (mediatek/, etc.)
            for subdir in dtbs_dir.iterdir():
                if subdir.is_dir():
                    dtb = subdir / f'{dts_name}.dtb'
                    if dtb.exists():
                        return dtb

        return None

    def _compress_kernel(self, kernel: Path, compression: str) -> Path:
        """Compress kernel image."""
        if compression == 'none' or not compression:
            return kernel

        output = self.build_dir / f'kernel.{compression}'

        if compression == 'gzip':
            with open(kernel, 'rb') as f_in:
                with gzip.open(output, 'wb') as f_out:
                    shutil.copyfileobj(f_in, f_out)
        elif compression == 'lzma':
            try:
                subprocess.run(
                    f'lzma -9 -c {kernel} > {output}',
                    shell=True, check=True,
                )
            except subprocess.CalledProcessError:
                return kernel
        elif compression == 'xz':
            try:
                subprocess.run(
                    f'xz -9 -c {kernel} > {output}',
                    shell=True, check=True,
                )
            except subprocess.CalledProcessError:
                return kernel
        else:
            return kernel

        return output if output.exists() else kernel

    def _pad_image(self, image: Path, pad_to: str):
        """Pad image to specified size with 0xFF bytes."""
        # Parse size
        size = pad_to.upper()
        if size.endswith('K'):
            target_size = int(size[:-1]) * 1024
        elif size.endswith('M'):
            target_size = int(size[:-1]) * 1024 * 1024
        else:
            target_size = int(size)

        current_size = image.stat().st_size
        if current_size < target_size:
            with open(image, 'ab') as f:
                f.write(b'\xff' * (target_size - current_size))
