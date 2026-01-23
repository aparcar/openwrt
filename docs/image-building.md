# Image Building

This document describes how the OpenWrt build system generates firmware images
for different target devices.

## Overview

The image builder handles:

- Assembling the root filesystem from APK packages
- Creating filesystem images (squashfs, ext4, UBIFS, JFFS2)
- Building FIT images (kernel + DTB + rootfs)
- Creating bootable disk images (GPT with EFI boot)
- Creating UBI volumes for NAND flash
- Generating sysupgrade metadata for over-the-air updates
- Building initramfs images

## Build Process

```
┌─────────────────┐
│Assemble rootfs  │  Install packages via APK
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│Create users     │  From package userid declarations
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│Release files    │  /etc/openwrt_release, /etc/os-release
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│Device nodes     │  Essential /dev entries for boot
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│Filesystem image │  squashfs/ext4/UBIFS/JFFS2
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│Final image      │  FIT/EFI disk/initramfs
└─────────────────┘
```

## Image Types

### Sysupgrade (FIT)

The primary firmware format for most targets. A Flattened Image Tree (FIT)
containing:

- Compressed kernel image
- Device Tree Blob (DTB)
- SquashFS rootfs (appended externally)
- Sysupgrade metadata

```yaml
images:
  - name: sysupgrade.itb
    type: sysupgrade
    filesystem: squashfs
    kernel:
      compression: gzip
      load_address: "0x44000000"
    metadata: true
```

### Initramfs

Kernel with embedded root filesystem. Used for:
- Initial testing
- Recovery mode
- Diskless/netboot systems

The rootfs is embedded directly in the kernel via `CONFIG_INITRAMFS_SOURCE`.

```yaml
images:
  - name: initramfs-kernel.bin
    type: initramfs
    embedded: true
```

### Combined EFI

Bootable disk image for EFI systems:
- GPT partition table
- EFI System Partition (FAT32) with kernel
- Root partition (ext4)

```yaml
images:
  - name: combined.img.gz
    type: combined
    filesystem: ext4
    size: "512M"
```

### UBI/UBIFS

For NAND flash devices:
- UBI volume with wear leveling
- UBIFS filesystem optimized for NAND

```yaml
images:
  - name: sysupgrade.ubi
    type: sysupgrade
    filesystem: ubifs
ubi:
  blocksize: 128k
  pagesize: 2048
```

### JFFS2

For NOR flash devices:
- Journaling filesystem
- Built-in wear leveling

```yaml
images:
  - name: rootfs.jffs2
    type: rootfs
    filesystem: jffs2
jffs2:
  erase_block: 64k
```

## Image Pipeline

Modern image generation uses a pipeline approach:

```yaml
images:
  - name: sysupgrade.itb
    pipeline:
      - kernel                    # Start with kernel
      - compress: gzip            # Compress
      - fit:                      # Create FIT image
          compression: gzip
          rootfs: external
      - metadata: append          # Add sysupgrade metadata
```

Available pipeline steps:
- `kernel` - Use kernel image
- `compress` - Compress with gzip/lzma/xz
- `fit` - Create FIT image
- `ubi` - Create UBI volume
- `metadata` - Append sysupgrade metadata
- `pad` - Pad to specific size

## Root Filesystem Assembly

### Package Installation

Packages are installed via APK:

```
build/apk-repo/<arch>/           # Package repository
  └── *.apk                      # Package files

build/rootfs/<target>/           # Root filesystem
  ├── bin/
  ├── etc/
  ├── lib/
  ├── usr/
  └── ...
```

### User/Group Creation

Packages can declare users and groups:

```yaml
# In package.yaml
userid:
  - "dnsmasq=453:dnsmasq=453"    # user=uid:group=gid
  - ":network=101"               # group only
```

These are created in `/etc/passwd`, `/etc/shadow`, and `/etc/group`.

### Release Information

Generated files:
- `/etc/openwrt_release` - OpenWrt-specific info
- `/etc/openwrt_version` - Version string
- `/usr/lib/os-release` - Standard Linux release info

### Device Nodes

Essential device nodes created for early boot:
- `/dev/console` - Kernel console
- `/dev/null`, `/dev/zero` - Null devices
- `/dev/tty`, `/dev/ttyS0` - Terminals
- `/dev/random`, `/dev/urandom` - Random devices

## Filesystem Formats

### SquashFS

Compressed read-only filesystem. Default for most targets.

Features:
- XZ compression with architecture-specific BCJ filter
- 256KB block size
- Very small image size

Options (in image spec):
```yaml
squashfs:
  compression: xz
  block_size: 256k
```

### Ext4

Standard Linux filesystem. Used for writable root or EFI boot.

### UBIFS

Optimized for NAND flash:
- Runs on top of UBI (wear leveling)
- Compression (zlib/lzo/zstd)

Configuration:
```yaml
ubi:
  blocksize: 128k      # Physical Erase Block size
  pagesize: 2048       # NAND page size
ubifs:
  max_leb_cnt: 4096    # Maximum Logical Erase Blocks
  compression: zlib
```

### JFFS2

For NOR flash:
- Journaling for reliability
- Built-in wear leveling

Configuration:
```yaml
jffs2:
  erase_block: 64k     # Erase block size
  page_size: null      # null for NOR, set for NAND
```

## FIT Images

Flattened Image Tree (FIT) bundles multiple components:

```
┌────────────────────────────┐
│      FIT Header            │
├────────────────────────────┤
│   Kernel (compressed)      │
├────────────────────────────┤
│   Device Tree Blob         │
├────────────────────────────┤
│   Configuration            │
└────────────────────────────┘
     │
     └─── [External: rootfs.squashfs]
```

### ITS (Image Tree Source)

The FIT image is described by an ITS file:

```dts
/dts-v1/;
/ {
    description = "OpenWrt FIT Image";
    images {
        kernel {
            data = /incbin/("kernel.gz");
            compression = "gzip";
            load = <0x44000000>;
            entry = <0x44000000>;
        };
        fdt {
            data = /incbin/("device.dtb");
        };
    };
    configurations {
        default = "config-1";
        config-1 {
            kernel = "kernel";
            fdt = "fdt";
        };
    };
};
```

## Sysupgrade Metadata

Appended to sysupgrade images:

```json
{
  "supported_devices": ["vendor,device-model"],
  "version": {
    "dist": "OpenWrt",
    "version": "SNAPSHOT",
    "revision": "r12345"
  }
}
```

The bootloader or sysupgrade script verifies device compatibility before
flashing.

## EFI Boot

For EFI-capable systems (armsr, x86):

```
Disk Image (GPT)
├── Partition 1: EFI System Partition (FAT32)
│   ├── /EFI/openwrt/Image       # Kernel
│   ├── /EFI/boot/bootaa64.efi   # UEFI bootloader
│   └── /startup.nsh             # UEFI shell script
└── Partition 2: Root filesystem (ext4)
```

The kernel has `CONFIG_EFI_STUB` enabled, making it a valid EFI application.

## Image Naming Convention

Images follow OpenWrt naming:

```
<dist>-<version>-<board>-<subtarget>-<device>-<type>.<ext>
```

Example:
```
openwrt-SNAPSHOT-mediatek-filogic-openwrt_one-sysupgrade.itb
```

## Directory Structure

```
build/
├── rootfs/<target>/          # Assembled root filesystem
├── image/<target>/           # Build working directory
│   ├── rootfs.squashfs       # Filesystem images
│   ├── rootfs.ext4
│   └── *.its                 # FIT image descriptions
└── images/<target>/          # Final output images
    ├── *-sysupgrade.itb
    ├── *-initramfs.bin
    └── *-combined.img.gz
```

## CLI Commands

```bash
# Build images for default profile
owrt image build armsr-armv8

# Build for specific profile
owrt image build --profile bananapi-r4 mediatek-filogic

# Build initramfs image
owrt build --initramfs armsr-armv8
```

## Troubleshooting

### "APK not available"

The APK package manager wasn't built. Build host tools first:
```bash
owrt tool build-all
```

### Image too large for flash

Reduce image size:
- Remove unnecessary packages
- Use stronger compression
- Split into multiple partitions

### FIT image fails to boot

Check:
- Load address matches kernel config
- DTB is correct for the device
- Kernel supports device hardware

### EFI boot fails

Verify:
- Kernel has `CONFIG_EFI_STUB=y`
- ESP partition is FAT32
- Kernel is copied to `/EFI/boot/boot<arch>.efi`

### Sysupgrade rejects image

Device name mismatch. Check:
- `supported_devices` in metadata
- Device's existing `/etc/board.json`

## Implementation Details

The image builder is implemented in `owrt/image.py`. Key features:

- **APK rootfs** - Uses APK to install packages to rootfs
- **Pipeline architecture** - Composable image generation steps
- **FIT builder** - Generates ITS and builds FIT images
- **UBI/UBIFS/JFFS2** - Flash filesystem support
- **Metadata** - Sysupgrade compatibility info
