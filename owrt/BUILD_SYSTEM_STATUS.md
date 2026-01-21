# OpenWrt PoC Build System - Status and Roadmap

## Overview

This proof-of-concept reimagines the OpenWrt build system with:
- **YAML-based configuration** instead of Kconfig/Makefiles
- **Ninja-based parallel builds** with proper dependency tracking
- **Docker containerization** for reproducible builds
- **APK v3 packaging** (Alpine Package Keeper)
- **Python orchestration** replacing complex Make infrastructure

## Current Architecture

```
owrt/
├── build.sh                 # Main entry point for builds
├── docker/Dockerfile        # Build environment container
├── targets/                 # Target definitions in target/linux/*/
├── packages/                # Package definitions (44 converted)
├── tools/                   # Host tool definitions (YAML)
└── owrt/                    # Python build system
    ├── __main__.py          # CLI entry point
    ├── config.py            # YAML config loading
    ├── toolchain.py         # Cross-toolchain builder
    ├── kernel.py            # Linux kernel builder
    ├── kmod.py              # Kernel module packaging
    ├── package.py           # Package builder
    ├── apk.py               # APK packaging
    ├── image.py             # Firmware image generation
    ├── fit.py               # FIT/UBI/UBIFS/JFFS2 builders
    ├── bootloader.py        # TF-A and U-Boot builder
    ├── tool.py              # Host tool builder
    ├── resolver.py          # Dependency resolution with PROVIDES
    ├── ninja_gen.py         # Ninja file generation
    └── utils.py             # Shared utilities
```

## What Works Today

### 1. Toolchain Building (`toolchain.py`)
- [x] Downloads and builds binutils, GCC, musl, kernel headers
- [x] Applies OpenWrt downstream patches
- [x] Produces working cross-compiler
- [x] Hash-based caching for CI

### 2. Host Tools (`tool.py`)
- [x] YAML-based tool definitions
- [x] Dependency resolution (lua → apk)
- [x] Meson/autotools/cmake/make build systems
- [x] Installs to host-staging directory
- [x] mtd-utils (mkfs.ubifs, mkfs.jffs2, ubinize)
- [x] fwtool for sysupgrade metadata

### 3. Kernel Building (`kernel.py`)
- [x] Downloads kernel source
- [x] Applies OpenWrt kernel patches
- [x] Cross-compiles with toolchain
- [x] Builds modules and DTBs
- [x] Initramfs embedding support

### 4. Kernel Module Packaging (`kmod.py`)
- [x] Extracts built modules from kernel build
- [x] Creates separate APK packages per module (kmod-*)
- [x] Handles module dependencies (via kmods.yaml definitions)
- [x] Kernel version in package names
- [x] Autoload configuration (/etc/modules.d/)

### 5. Package Building (`package.py`)
- [x] YAML package definitions (44 packages converted)
- [x] Multiple build systems (autotools, cmake, meson, make, custom)
- [x] Cross-compilation with proper flags
- [x] Dependency resolution
- [x] Staging directory for build dependencies
- [x] Per-package install directories (ipkg-install/)
- [x] Subpackage support (multiple outputs from one source)

### 6. APK Packaging (`apk.py`)
- [x] Creates .apk packages using `apk mkpkg`
- [x] Generates PKGINFO metadata
- [x] Repository management
- [x] APKINDEX generation with `apk mkndx`
- [x] Rootfs assembly via APK installation

### 7. Dependency Resolution (`resolver.py`)
- [x] Virtual packages (PROVIDES) with priority system
- [x] Circular dependency detection
- [x] Topological sorting for build order
- [x] Conditional dependencies (`+CONDITION:dep`, `+!CONDITION:dep`, `+PACKAGE_*:dep`)

### 8. Image Generation (`image.py`, `fit.py`)
- [x] Rootfs assembly from APK packages
- [x] User/group creation from package definitions
- [x] Release info generation (/etc/openwrt_release, etc.)
- [x] Device node creation (/dev/console, etc.)
- [x] **Filesystem images:**
  - [x] SquashFS (XZ compressed with BCJ filter)
  - [x] ext4 (populated with mke2fs -d)
  - [x] UBIFS (for NAND/UBI)
  - [x] JFFS2 (for NOR flash and overlay)
- [x] **FIT images:**
  - [x] ITS generation and mkimage compilation
  - [x] Kernel + DTB + rootfs bundling
  - [x] External data support
- [x] **UBI images:**
  - [x] ubinize configuration generation
  - [x] Multi-volume support
- [x] **Sysupgrade format:**
  - [x] FIT with external rootfs
  - [x] Metadata via fwtool
- [x] **EFI images:**
  - [x] GPT partition table
  - [x] FAT32 ESP with kernel
  - [x] Combined disk images
- [x] Post-install scripts (via APK `--script` and `IPKG_INSTROOT` env)

### 9. Bootloader Building (`bootloader.py`)
- [x] ARM Trusted Firmware (TF-A) building
- [x] U-Boot building with defconfig
- [x] FIP image creation with fiptool
- [x] XZ compression for FIP contents
- [x] MediaTek MT7981/MT7986 support

### 10. Ninja Integration (`ninja_gen.py`)
- [x] Generates build.ninja from dependency graph
- [x] Parallel package builds
- [x] APK index generation after all packages
- [x] Proper dependency ordering

### 11. Docker Integration (`build.sh`)
- [x] Base image with build dependencies
- [x] Toolchain image with pre-built cross-compiler
- [x] Volume mounts for build artifacts
- [x] Non-Docker fallback mode

### 12. Download Management (`download.py`)
- [x] Tarball downloads with hash verification
- [x] Git repository cloning with commit checkout
- [x] OpenWrt mirror fallback (sources.openwrt.org)
- [x] Automatic retry when git.openwrt.org is unavailable
- [x] OpenWrt-compatible filename format for mirror

### 13. Target Support
- [x] **armsr-armv8** - ARM SystemReady (EFI) 64-bit
- [x] **x86-64** - x86 64-bit systems/VMs
- [x] **mediatek-filogic** - MT7981/MT7986 (OpenWrt One, ABT ASR3000)
  - [x] Multi-profile support
  - [x] FIT images for U-Boot
  - [x] UBI/UBIFS for NAND flash
  - [x] Factory images with BL2 + FIP

### 14. Per-Architecture Build Sharing
- [x] Package stamps shared across targets with same architecture
- [x] Staging directories per-architecture (not per-target)
- [x] APK packages per-architecture
- [x] Avoids rebuilds when switching between armsr-armv8 and mediatek-filogic (both aarch64)

---

## What's Missing (TODO List)

### Phase 1: Core Build System Completion

#### 1.1 Package Definitions
- [x] **Core packages converted to YAML format** (44 packages)
  - [x] `libc`, `libgcc` - From toolchain
  - [x] `busybox`, `base-files`
  - [x] `procd`, `ubus`, `ubox`, `uci`
  - [x] `libubox`, `libjson-c`, `ucode`, `udebug`
  - [x] `dropbear` - SSH server
  - [x] `netifd`, `fstools`
  - [x] `libnl-tiny`, `mbedtls`, `ustream-ssl`, `uclient`
  - [x] `apk`, `ca-certificates`
  - [x] `urandom-seed`, `urngd`
  - [x] `dnsmasq`, `odhcpd`
  - [x] `hostapd` - WiFi AP daemon
  - [x] `firewall4`, `nftables` - Firewall
  - [x] `mac80211`, `mt76` - WiFi drivers

- [ ] **Package YAML schema enhancements**
  - [x] `provides` field for virtual packages
  - [ ] `conflicts` field
  - [ ] `replaces` field
  - [ ] `alternatives` for busybox-style symlinks
  - [ ] `menu` field for grouping in menuconfig
  - [ ] `kconfig` field for kernel dependencies

#### 1.2 Kernel Integration
- [x] **Kernel module packaging** - Complete
- [ ] **Kernel configuration**
  - [ ] `make menuconfig` integration
  - [ ] Save config changes back to YAML/fragments
- [x] **Device Tree handling**
  - [x] Build DTBs for target devices
  - [x] Include correct DTB in firmware image
  - [x] DTB overlays support (profile `dts_overlay` field)

#### 1.3 Image Generation
- [x] **Rootfs assembly** - Complete
- [x] **Filesystem images** - Complete (squashfs, ext4, ubifs, jffs2)
- [x] **Bootloader integration** - Complete (TF-A, U-Boot, FIP)
- [x] **Image types** - Working for armsr, x86, mediatek
- [ ] **Post-install scripts** - Not running during rootfs assembly

#### 1.4 Target Definitions
- [x] `armsr-armv8` (ARM SystemReady EFI)
- [x] `x86-64` (PC/VM target)
- [x] `mediatek-filogic` (OpenWrt One, MT7981/MT7986)
- [ ] `ath79-generic` (Classic Atheros routers)
- [ ] `ramips-mt7621` (MediaTek MIPS)
- [ ] `bcm27xx-bcm2711` (Raspberry Pi 4)

### Phase 2: Build System Features

#### 2.1 Dependency Resolution
- [x] **Virtual packages** - PROVIDES support implemented
- [x] **Conditional dependencies** - `+IPV6:libc`, `+!COND:dep`, `+PACKAGE_*:dep`

#### 2.2 Configuration System
- [ ] **menuconfig replacement** - TUI for package selection
- [x] **Profile system** - Multi-profile support working

#### 2.3 Build Caching
- [x] **Hash-based package cache keys**
- [x] **Skip rebuild if inputs unchanged**
- [x] **Dependency cascading**
- [ ] **ccache integration**

### Phase 3: Advanced Features

#### 3.1 Feeds System
- [ ] External package feeds
- [ ] Package search

#### 3.2 SDK and Image Builder
- [ ] SDK generation
- [ ] Image Builder (no compilation)

#### 3.3 Testing Infrastructure
- [ ] Unit tests for build system
- [ ] QEMU-based runtime testing

#### 3.4 Migration Tools
- [ ] Makefile to YAML converter
- [ ] Config migration from .config

---

## Known Issues

1. **Package versions**: Some package versions are newer than what's on sources.openwrt.org mirror - when git.openwrt.org is down, these packages cannot be downloaded

**Workaround for git.openwrt.org downtime:**
- The build system automatically falls back to sources.openwrt.org mirror
- For packages not on the mirror, update package.yaml to use an available version
- Check available versions: `curl -s https://sources.openwrt.org/ | grep <pkgname>`

**Resolved Issues:**
- ~~Virtual packages~~: PROVIDES support implemented with priority system
- ~~UBIFS support~~: Implemented with mkfs.ubifs
- ~~JFFS2 support~~: Implemented with mkfs.jffs2
- ~~FIT images~~: Full support for kernel + DTB + rootfs
- ~~UBI images~~: Working with ubinize
- ~~Sysupgrade format~~: Working with fwtool metadata
- ~~DTB overlays~~: Implemented via profile `dts_overlay` field
- ~~Post-install scripts~~: Enabled via APK `--script` and `IPKG_INSTROOT` env
- ~~Conditional dependencies~~: `+CONDITION:dep`, `+!COND:dep`, `+PACKAGE_*:dep` supported

## Quick Reference

### Build Commands
```bash
./build.sh                  # Full build (default target)
./build.sh -t mediatek-filogic firmware  # Specific target
./build.sh -t mediatek-filogic -p openwrt_one firmware  # Specific profile
./build.sh toolchain        # Toolchain only
./build.sh tools            # Host tools (lua, apk, mtd-utils)
./build.sh clean all        # Clean everything
```

### CLI Commands
```bash
python -m owrt info armsr-armv8
python -m owrt toolchain build armsr-armv8
python -m owrt kernel build armsr-armv8
python -m owrt package armsr-armv8 busybox
python -m owrt image mediatek-filogic --profile openwrt_one
python -m owrt ninja generate armsr-armv8
python -m owrt ninja run armsr-armv8
```

### Output Locations
```
build/
├── toolchain/<target>/       # Cross-compiler (per-target)
├── host-staging/             # Host tools (apk, lua, mtd-utils)
├── kernel/<target>/          # Kernel build (per-target)
├── packages/<arch>/          # Package builds (per-architecture, shared)
├── staging/<arch>/           # Installed headers/libs (per-architecture)
├── apk-packages/<arch>/      # Individual .apk files (per-architecture)
├── apk-repo/<arch>/          # Repository with APKINDEX (per-architecture)
├── rootfs/<target>/          # Assembled rootfs (per-target)
├── <target>/stamp/           # Build stamps (toolchain, kernel, kmod)
└── output/images/            # Final firmware images

# Architecture mapping:
#   armsr-armv8 → aarch64
#   mediatek-filogic → aarch64
#   x86-64 → x86_64
```

---

*Last updated: 2026-01-20*
*Branch: claude/plan-build-system-8DNjM*
