# Kernel Building

This document describes how the OpenWrt build system builds the Linux kernel
with OpenWrt patches and customizations.

## Overview

The kernel builder handles:

- Downloading kernel source from kernel.org
- Applying OpenWrt patches (backport, pending, hack, target-specific)
- Copying OpenWrt kernel files (custom drivers, etc.)
- Merging kernel config fragments
- Generating kmod configs from requested packages
- Compiling kernel and modules
- Building Device Tree Blobs (DTBs) and overlays
- Computing vermagic for module compatibility
- Embedding initramfs for initial RAM filesystem

## Build Process

```
┌─────────────────┐
│Download source  │  From kernel.org (cached in dl/)
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│Apply patches    │  Generic + target-specific patches
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│Copy files       │  Custom drivers from files/ and files-<version>/
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│Configure        │  Merge config fragments, enable kmod options
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│Compute vermagic │  MD5 hash of config for module compatibility
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│Compile          │  Build kernel image and modules
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│Build DTBs       │  Device Tree Blobs for target hardware
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│Install          │  Copy kernel, modules, DTBs to output
└─────────────────┘
```

## Kernel Patches

OpenWrt applies patches in a specific order:

1. **Generic backports** - `target/linux/generic/backport-<version>/`
2. **Generic pending** - `target/linux/generic/pending-<version>/`
3. **Generic hacks** - `target/linux/generic/hack-<version>/`
4. **Target-specific** - `target/linux/<board>/patches-<version>/`

Patch categories:
- **backport**: Fixes from newer kernels backported to this version
- **pending**: Patches pending upstream submission
- **hack**: Temporary workarounds or OpenWrt-specific changes

## Kernel Configuration

Configuration is built from multiple fragments:

```
┌──────────────────┐
│ Generic config   │  target/linux/generic/config-<version>
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│ Target config    │  target/linux/<board>/config-<version>
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│ Subtarget config │  target/linux/<board>/<subtarget>/config-<version>
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│ Kmod config      │  Auto-generated from requested kmod-* packages
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│ User config      │  From config.yaml kernel_config section
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│ olddefconfig     │  Fill in defaults for unset options
└──────────────────┘
```

### Kmod Configuration

When building for a profile, the builder automatically enables kernel CONFIG
options required by kmod-* packages:

```yaml
# config.yaml
packages:
  - kmod-usb-core
  - kmod-usb-storage
```

The kmod registry (`poc/kmod-*.yaml`) defines which CONFIG options each module
needs:

```yaml
# kmod-usb-core.yaml
name: usb-core
kconfig:
  - CONFIG_USB=y
  - CONFIG_USB_SUPPORT=y
```

### User Kernel Config

Override kernel options in your `config.yaml`:

```yaml
kernel_config:
  CONFIG_IPV6: y
  CONFIG_NETFILTER: y
  CONFIG_NF_CONNTRACK: m
```

## Vermagic

The vermagic is an MD5 hash of all kernel CONFIG options set to `y` or `m`.
It ensures kernel modules are only loaded by kernels with identical configuration.

Computation:
```bash
grep '=[ym]' .config | LC_ALL=C sort | md5sum
```

The vermagic is stored in `.vermagic` in the kernel build directory and used
in kmod package dependencies.

## Architecture Mapping

The builder maps OpenWrt architecture names to kernel architecture names:

| OpenWrt Arch | Kernel Arch | Kernel Image |
|--------------|-------------|--------------|
| aarch64 | arm64 | Image |
| arm | arm | zImage |
| x86_64 | x86 | bzImage |
| i386 | x86 | bzImage |
| mips, mipsel | mips | vmlinux |

## Device Tree

For ARM and ARM64 targets, Device Tree Blobs (DTBs) describe the hardware:

- Source files: `target/linux/<board>/dts/*.dts`
- Compiled to: `<build>/kernel/output/dtbs/*.dtb`

### DTB Overlays

DTB overlays allow modular device tree modifications:

```yaml
# In target.yaml profile
profiles:
  bananapi-r4:
    dts_overlay:
      - mt7988a-bananapi-bpi-r4-emmc
      - mt7988a-bananapi-bpi-r4-sd
```

Overlays are compiled from `.dtso` files to `.dtbo` files.

## Initramfs

For targets that boot from initramfs (kernel with embedded rootfs):

```bash
owrt build --initramfs <target>
```

This:
1. Builds the normal kernel first
2. Assembles the rootfs
3. Rebuilds kernel with `CONFIG_INITRAMFS_SOURCE` pointing to rootfs
4. Creates device nodes via gen_init_cpio
5. Outputs `initramfs-kernel.bin`

## Directory Structure

```
build/
└── kernel-build/<target>/
    ├── linux-<version>/     # Kernel source (patched)
    │   ├── .config          # Final kernel config
    │   ├── vmlinux          # Uncompressed kernel (debug)
    │   └── arch/<arch>/boot/
    │       ├── Image        # Compressed kernel
    │       └── dts/*.dtb    # Device tree blobs
    ├── output/              # Build outputs
    │   ├── kernel.bin       # Kernel image
    │   ├── vmlinux          # Debug symbols
    │   ├── modules/         # Installed modules
    │   ├── dtbs/            # DTB files
    │   └── dtbos/           # DTB overlays
    ├── stamp/               # Build stage markers
    │   ├── kernel_extracted
    │   ├── kernel_patched
    │   ├── kernel_configured
    │   ├── kernel_compiled
    │   └── kernel_installed
    └── .vermagic            # Kernel vermagic hash
```

## CLI Commands

```bash
# Build kernel for a target
owrt kernel build armsr-armv8

# Build kernel with profile-specific kmods
owrt kernel build --profile generic armsr-armv8

# Clean kernel build
owrt kernel clean armsr-armv8

# Run kernel menuconfig
owrt kernel menuconfig armsr-armv8
```

## Build Environment

The kernel is built with:

- Cross-compiler from toolchain (`<tuple>-gcc`)
- `ARCH=<kernel_arch>` for architecture-specific builds
- `CROSS_COMPILE=<tuple>-` prefix for all tools
- Reproducible build settings:
  - `KBUILD_BUILD_HOST=openwrt`
  - `KBUILD_BUILD_USER=builder`
  - `KBUILD_BUILD_TIMESTAMP=@0`

## ccache Support

Enable ccache for faster rebuilds:

```bash
owrt build --ccache <target>
```

This prepends `ccache` to the compiler commands.

## Troubleshooting

### "Kernel config changed, reconfiguring..."

The builder detected changes in:
- Profile (different kmod packages)
- User kernel config overrides
- Base config files (target configs)

This triggers a full kernel reconfigure and rebuild.

### "Module version mismatch"

The kernel and modules were built with different configs. This happens when:
- Kernel was rebuilt without rebuilding kmod packages
- Module from different kernel version

Solution: Rebuild both kernel and kmod packages together.

### DTB build fails

Check that DTS files exist in `target/linux/<board>/dts/` and have valid syntax.
Run with `-v` for verbose output showing DTC errors.

### Initramfs too large

The kernel with embedded initramfs may exceed flash limits. Solutions:
- Remove unnecessary packages
- Enable kernel compression (requires CONFIG_RD_GZIP)
- Use external rootfs instead of initramfs

## Implementation Details

The kernel builder is implemented in `owrt/kernel.py`. Key features:

- **Stamp files** - Track build stages for incremental builds
- **Config hash** - Detect config changes and trigger rebuilds
- **Kmod registry** - Auto-enable kernel options for requested modules
- **Vermagic computation** - Ensure module compatibility
- **ccache support** - Optional compiler caching for faster rebuilds
