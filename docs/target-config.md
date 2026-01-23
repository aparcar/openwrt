# Target Configuration

Targets define the hardware platform and build configuration. Each target
has a `target.yaml` file that specifies the architecture, toolchain, kernel,
and default packages.

## File Location

Target configurations are stored in:

```
target/linux/<board>/<subtarget>/target.yaml
```

For example:
- `target/linux/armsr/armv8/target.yaml` - ARM64 UEFI systems
- `target/linux/mediatek/filogic/target.yaml` - MediaTek MT7981/MT7986
- `target/linux/x86/64/target.yaml` - x86-64 systems

## Basic Structure

```yaml
name: armsr-armv8
board: armsr
subtarget: armv8
description: "ARM SystemReady (EFI) compliant 64-bit machines"

arch: aarch64

cpu:
  type: generic
  endian: little

toolchain:
  libc: musl
  gcc_version: "14.3.0"
  binutils_version: "2.44"

kernel:
  config_fragments:
    - config-6.12
  patch_dirs:
    - ../patches-6.12

features:
  - fpu
  - pci
  - usb

default_packages:
  - base-files
  - busybox
  - dropbear

profiles:
  - name: generic
    title: Generic ARM64 Device
    default: true
```

## Required Fields

| Field | Description |
|-------|-------------|
| `name` | Target identifier (format: `<board>-<subtarget>`) |
| `arch` | CPU architecture (`aarch64`, `arm`, `x86_64`, `mips`, etc.) |
| `toolchain` | Toolchain configuration |

## Optional Fields

| Field | Default | Description |
|-------|---------|-------------|
| `board` | From name | Hardware board family |
| `subtarget` | From name | Specific variant |
| `description` | - | Human-readable description |
| `cpu` | {} | CPU-specific settings |
| `kernel` | {} | Kernel configuration |
| `features` | [] | Hardware features |
| `default_packages` | [] | Packages for this target |
| `profiles` | [] | Device profiles |
| `image` | {} | Image generation settings |

## CPU Configuration

```yaml
cpu:
  type: cortex-a53     # CPU type for optimization
  endian: little       # little or big
  fpu: vfpv4           # FPU type
```

Common CPU types:
- `generic` - No specific optimization
- `cortex-a53`, `cortex-a72` - ARM Cortex variants
- `mips32r2` - MIPS32 Release 2

## Toolchain Configuration

```yaml
toolchain:
  libc: musl                    # C library: musl or glibc
  gcc_version: "14.3.0"         # GCC version
  binutils_version: "2.44"      # Binutils version
  target_tuple: aarch64-openwrt-linux-musl  # GNU target tuple
  
  cflags:
    - "-Os"                     # Optimize for size
    - "-pipe"                   # Use pipes for compilation
    - "-mcpu=cortex-a53"        # CPU-specific optimization
  
  ldflags:
    - "-Wl,--gc-sections"       # Remove unused sections
```

The target tuple is auto-generated if not specified, based on arch and libc.

## Kernel Configuration

```yaml
kernel:
  # Config fragments (merged in order)
  config_fragments:
    - config-6.12              # Base config for this kernel version
    - config-wifi              # Additional WiFi options

  # Patch directories (applied in order)
  patch_dirs:
    - ../patches-6.12          # Target patches
  
  # Device Tree source directory
  dts_dir: dts
  
  # Kernel load/entry addresses
  load_address: "0x40080000"
  entry_address: "0x40080000"
  
  # EFI stub support
  efi_stub: true
```

### Config Fragments vs Config Directory

Two approaches for kernel configuration:

**Config Fragments (Recommended):**
```yaml
kernel:
  config_fragments:
    - config-6.12
    - subtarget/config-default
```

**Legacy Config Directory:**
```yaml
kernel:
  config_dir: "${OPENWRT_DIR}/target/linux/armsr"
```

## Features

Features describe hardware capabilities and affect package selection:

```yaml
features:
  - fpu          # Floating point unit
  - pci          # PCI bus support
  - pcie         # PCI Express
  - usb          # USB support
  - rtc          # Real-time clock
  - ext4         # ext4 filesystem
  - squashfs     # SquashFS filesystem
  - ramdisk      # RAM-based root filesystem
  - nand         # NAND flash storage
  - emmc         # eMMC storage
  - wifi         # WiFi support
```

## Default Packages

Packages installed on all profiles for this target:

```yaml
default_packages:
  # Core system
  - base-files
  - busybox
  - libc
  
  # Init system
  - procd
  - ubox
  
  # Networking
  - netifd
  - dropbear
  - dnsmasq
  
  # Package management
  - apk
  - uclient-fetch
  - ca-bundle
```

Use `-` prefix to exclude a global default:
```yaml
default_packages:
  - -dropbear     # Exclude dropbear
  - openssh       # Use openssh instead
```

## Device Profiles

Profiles define specific device configurations:

```yaml
profiles:
  - name: generic
    title: Generic ARM64 Device
    description: For any ARM64 UEFI-compliant device
    default: true
    packages:
      - kmod-usb-storage
      - kmod-fs-ext4
  
  - name: qemu
    title: QEMU Virtual Machine
    packages:
      - kmod-virtio-net
      - kmod-virtio-blk
```

### Profile Fields

| Field | Description |
|-------|-------------|
| `name` | Profile identifier |
| `title` | Human-readable name |
| `description` | Optional description |
| `default` | Set as default profile |
| `packages` | Additional packages for this profile |
| `features` | Profile-specific features |
| `image` | Image generation overrides |

### Profile Files

Profiles can be defined in separate files:

```
target/linux/mediatek/filogic/
├── target.yaml
└── profiles/
    ├── bananapi_bpi-r3.yaml
    ├── bananapi_bpi-r4.yaml
    └── glinet_gl-mt6000.yaml
```

Profile file example:
```yaml
# profiles/bananapi_bpi-r3.yaml
name: bananapi_bpi-r3
title: Banana Pi BPI-R3
description: MediaTek MT7986 router board

packages:
  - kmod-mt7986-firmware
  - kmod-mt7915e
  - wpad-basic-mbedtls

supported_devices:
  - bananapi,bpi-r3

image:
  kernel_dtb: mt7986a-bananapi-bpi-r3.dtb
```

## Image Configuration

Configure firmware image generation:

```yaml
image:
  # Filesystem type
  filesystem: squashfs
  
  # Kernel format
  kernel_format: fit
  
  # Image types to generate
  types:
    - sysupgrade
    - factory
    - sdcard
  
  # Size limits
  kernel_size: 8M
  rootfs_size: 104M
  
  # Compression
  compression: gzip
```

## Complete Example

```yaml
# target/linux/mediatek/filogic/target.yaml
name: mediatek-filogic
board: mediatek
subtarget: filogic
description: "MediaTek Filogic 880/830 (MT7986/MT7981)"

arch: aarch64

cpu:
  type: cortex-a53
  endian: little

toolchain:
  libc: musl
  gcc_version: "14.3.0"
  binutils_version: "2.44"
  cflags:
    - "-Os"
    - "-pipe"
    - "-mcpu=cortex-a53"

kernel:
  config_fragments:
    - config-6.12
  patch_dirs:
    - patches-6.12
  dts_dir: dts

features:
  - nand
  - emmc
  - pcie
  - usb
  - wifi

default_packages:
  - base-files
  - busybox
  - libc
  - procd
  - netifd
  - dropbear
  - dnsmasq
  - wpad-basic-mbedtls
  - kmod-mt7915e

# Profiles loaded from profiles/*.yaml
```
