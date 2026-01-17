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
poc/
├── build.sh                 # Main entry point for builds
├── docker/Dockerfile        # Build environment container
├── targets/                 # Target definitions (YAML)
│   └── armsr-armv8/
│       └── target.yaml
├── packages/                # Package definitions (YAML)
│   ├── busybox/
│   │   └── package.yaml
│   └── base-files/
│       └── package.yaml
├── tools/                   # Host tool definitions (YAML)
│   ├── lua/
│   │   └── tool.yaml
│   └── apk/
│       └── tool.yaml
└── owrt_build/              # Python build system
    ├── __main__.py          # CLI entry point
    ├── config.py            # YAML config loading
    ├── toolchain.py         # Cross-toolchain builder
    ├── kernel.py            # Linux kernel builder
    ├── package.py           # Package builder
    ├── apk.py               # APK packaging
    ├── image.py             # Firmware image generation
    ├── tool.py              # Host tool builder
    ├── resolver.py          # Dependency resolution
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

### 3. Kernel Building (`kernel.py`)
- [x] Downloads kernel source
- [x] Applies OpenWrt kernel patches
- [x] Cross-compiles with toolchain
- [x] Builds modules and DTBs

### 4. Package Building (`package.py`)
- [x] YAML package definitions
- [x] Multiple build systems (autotools, cmake, meson, make, custom)
- [x] Cross-compilation with proper flags
- [x] Dependency resolution
- [x] Staging directory for build dependencies
- [x] Per-package install directories (ipkg-install/)

### 5. APK Packaging (`apk.py`)
- [x] Creates .apk packages using `apk mkpkg`
- [x] Generates PKGINFO metadata
- [x] Repository management
- [x] APKINDEX generation with `apk index`

### 6. Ninja Integration (`ninja_gen.py`)
- [x] Generates build.ninja from dependency graph
- [x] Parallel package builds
- [x] APK index generation after all packages
- [x] Proper dependency ordering

### 7. Docker Integration (`build.sh`)
- [x] Base image with build dependencies
- [x] Toolchain image with pre-built cross-compiler
- [x] Volume mounts for build artifacts
- [x] Non-Docker fallback mode

---

## What's Missing (Detailed TODO List)

### Phase 1: Core Build System Completion

#### 1.1 Package Definitions
- [x] **Core packages converted to YAML format**
  - [x] `libc` - musl C library (from toolchain)
  - [x] `libgcc` - GCC runtime library (libgcc_s.so.1 from toolchain)
  - [x] `busybox` - Complete with config
  - [x] `procd` - Process manager with subpackages
  - [x] `ubus` - OpenWrt micro bus with libubus
  - [x] `ubox` - OpenWrt utilities
  - [x] `uci` - Unified Configuration Interface
  - [x] `libubox` - Core utility library (with subpackages)
  - [x] `libjson-c` - JSON parsing library
  - [x] `ucode` - Scripting language
  - [x] `udebug` - Debug library
  - [x] `dropbear` - SSH server
  - [x] `base-files` - Base filesystem with preinit

- [x] **Network and TLS packages (completed)**
  - [x] `netifd` - Network interface daemon
  - [x] `fstools` - Filesystem tools (overlay mount)
  - [x] `libnl-tiny` - Netlink library
  - [x] `mbedtls` - TLS/crypto library
  - [x] `ustream-ssl` - SSL stream for libubox
  - [x] `uclient` - HTTP client library
  - [x] `apk` - Alpine Package Keeper
  - [x] `ca-certificates` - CA bundle
  - [x] `urandom-seed` - Random seed handling
  - [x] `urngd` - Entropy daemon

- [x] **DNS/DHCP packages**
  - [x] `dnsmasq` - DNS/DHCP server
  - [x] `odhcpd` - DHCPv4/DHCPv6/NDP/RA server

- [ ] **Remaining packages to convert**
  - [ ] `hostapd` - WiFi AP daemon
  - [ ] `wpad` - Combined hostapd/wpa_supplicant
  - [ ] `firewall4` / `nftables` - Firewall (requires kernel modules)

- [ ] **Package YAML schema enhancements**
  - [ ] Add `provides` field for virtual packages
  - [ ] Add `conflicts` field
  - [ ] Add `replaces` field
  - [ ] Add `alternatives` for busybox-style symlinks
  - [ ] Add `menu` field for grouping in menuconfig
  - [ ] Add `kconfig` field for kernel dependencies
  - [ ] Add `default` field (y/m/n)
  - [ ] Add `variant` support (like apk-mbedtls vs apk-openssl)

#### 1.2 Kernel Integration
- [x] **Kernel module packaging**
  - [x] Extract built modules from kernel build
  - [x] Create separate APK packages per module (kmod-*)
  - [x] Handle module dependencies (via modinfo)
  - [x] Kernel version in package names
  - [x] Autoload configuration (/etc/modules.d/)

- [ ] **Kernel configuration**
  - [ ] Import existing OpenWrt kernel configs
  - [ ] Per-target kernel config fragments
  - [ ] `make menuconfig` integration
  - [ ] Save config changes back to YAML/fragments

- [ ] **Device Tree handling**
  - [ ] Build DTBs for target devices
  - [ ] Include correct DTB in firmware image
  - [ ] DTB overlays support

#### 1.3 Image Generation (`image.py`)
- [x] **Rootfs assembly**
  - [x] Use APK to install packages into rootfs
  - [ ] Run post-install scripts
  - [x] Generate /etc/openwrt_release
  - [x] Generate /etc/openwrt_version
  - [x] Generate /usr/lib/os-release (standard Linux)
  - [x] Create device nodes in /dev (console, tty, null, etc.)
  - [x] Set proper permissions

- [x] **Filesystem images (partial)**
  - [x] SquashFS rootfs (XZ compressed)
  - [x] ext4 rootfs
  - [ ] JFFS2 for overlay
  - [ ] UBI/UBIFS for NAND flash

- [x] **Bootloader integration (partial)**
  - [ ] U-Boot image headers
  - [ ] FIT image generation (kernel + DTB + rootfs)
  - [ ] Sysupgrade image format
  - [ ] Factory image format (vendor-specific)

- [x] **Image types per target (partial)**
  - [x] armsr-armv8: EFI combined.img.gz, initramfs.bin
  - [ ] mediatek-filogic: FIT image for U-Boot
  - [ ] ath79: sysupgrade.bin, factory.bin
  - [ ] x86: combined-efi.img, ISO

#### 1.4 Target Definitions
- [ ] **More target YAML configs**
  - [ ] `mediatek-filogic` (OpenWrt One, MT7981/MT7986)
  - [ ] `ath79-generic` (Classic Atheros routers)
  - [ ] `ramips-mt7621` (MediaTek MIPS)
  - [x] `x86-64` (PC/VM target)
  - [ ] `bcm27xx-bcm2711` (Raspberry Pi 4)

- [ ] **Target YAML enhancements**
  - [ ] `features` list (usb, pcie, wifi, etc.)
  - [ ] `image_types` list per target
  - [ ] `bootloader` configuration
  - [ ] `flash_layout` (partitions, sizes)

### Phase 2: Build System Features

#### 2.1 Dependency Resolution
- [ ] **Virtual packages**
  - [ ] `libssl` provided by `libopenssl` or `libmbedtls`
  - [ ] `wpad` vs `wpad-basic` vs `hostapd`
  - [ ] Handle `PROVIDES` from old Makefiles

- [ ] **Conditional dependencies**
  - [ ] `DEPENDS:=+IPV6:libc` style conditionals
  - [ ] Feature-based dependencies
  - [ ] Kernel version dependencies

- [ ] **Dependency visualization**
  - [ ] Generate dependency graph (graphviz)
  - [ ] Detect circular dependencies
  - [ ] Warn about missing dependencies

#### 2.2 Configuration System
- [ ] **menuconfig replacement**
  - [ ] TUI for package selection
  - [ ] Save selections to YAML
  - [ ] Load from existing .config (migration)
  - [ ] Diffconfig support

- [ ] **Profile system**
  - [ ] Device profiles with default packages
  - [ ] Profile inheritance
  - [ ] Custom package lists per profile

#### 2.3 Download Handling
- [ ] **Mirror support**
  - [ ] Primary URL + fallback mirrors
  - [ ] OpenWrt download mirror integration
  - [ ] Hash verification (SHA256)

- [ ] **Git source support**
  - [ ] Shallow clones
  - [ ] Submodule handling
  - [ ] Tag/branch/commit checkout

- [ ] **Download cache**
  - [ ] Shared download directory
  - [ ] CI cache integration
  - [ ] Checksum-based deduplication

#### 2.4 Caching and Incremental Builds
- [x] **Build caching**
  - [x] Hash-based package cache keys (SHA256 of YAML, patches, files)
  - [x] Skip rebuild if inputs unchanged (stamps: `.built_<hash>`)
  - [x] Dependency cascading (dependency hash included in package hash)
  - [x] Toolchain version in package hash (GCC, libc changes trigger rebuild)
  - [ ] ccache integration for C/C++

- [ ] **CI integration**
  - [ ] GitHub Actions workflow
  - [ ] Cacheable toolchain images
  - [ ] Parallel target builds
  - [ ] Binary package hosting

### Phase 3: Advanced Features

#### 3.1 Feeds System
- [ ] **External package feeds**
  - [ ] YAML feed definitions
  - [ ] Git-based feeds
  - [ ] Feed update mechanism
  - [ ] Package override from feeds

- [ ] **Package search**
  - [ ] Index all available packages
  - [ ] Search by name, description
  - [ ] Show package info

#### 3.2 SDK and Image Builder
- [ ] **SDK generation**
  - [ ] Package toolchain + staging
  - [ ] Allow out-of-tree package builds
  - [ ] Reproducible SDK

- [ ] **Image Builder**
  - [ ] Use pre-built packages
  - [ ] Custom rootfs generation
  - [ ] No compilation required

#### 3.3 Testing Infrastructure
- [ ] **Package testing**
  - [ ] Unit tests for build system
  - [ ] Integration tests (build → run)
  - [ ] QEMU-based runtime testing

- [ ] **Reproducibility**
  - [ ] Reproducible builds verification
  - [ ] diffoscope integration
  - [ ] SOURCE_DATE_EPOCH handling

#### 3.4 Migration Tools
- [ ] **Makefile to YAML converter**
  - [ ] Parse existing package Makefiles
  - [ ] Extract metadata to package.yaml
  - [ ] Handle PKG_* variables
  - [ ] Convert Build/ functions to YAML

- [ ] **Config migration**
  - [ ] Import .config to YAML
  - [ ] Convert Kconfig symbols
  - [ ] Preserve custom settings

### Phase 4: Polish and Documentation

#### 4.1 Error Handling
- [ ] Clear error messages with solutions
- [ ] Build log collection
- [ ] Failure recovery (resume from last success)
- [ ] Parallel build error reporting

#### 4.2 Documentation
- [ ] Package YAML format specification
- [ ] Target YAML format specification
- [ ] Build system architecture guide
- [ ] Migration guide from Makefiles
- [ ] API documentation for Python modules

#### 4.3 Developer Experience
- [ ] Shell completion for CLI commands
- [ ] Progress bars for long operations
- [ ] Build time estimates
- [ ] IDE integration (VSCode tasks)

---

## Known Issues

1. **EFI boot**: combined.img.gz boots to EFI shell, not directly to kernel (startup.nsh issue)
2. **Virtual packages**: Not handled in dependency resolution
3. **Conditional dependencies**: Not supported yet

**Resolved Issues:**
- ~~Dependency cascading~~: Now implemented - changing a library auto-rebuilds dependents
- ~~procd boot panic~~: fstools and mount_root are now built
- ~~ESP size too small~~: Increased from 32MB to 64MB (kernel copied twice: /efi/openwrt/ + /efi/boot/)
- ~~Kernel module packages~~: Implemented with autoload config and dependency tracking

## Quick Reference

### Build Commands
```bash
./build.sh all              # Full build
./build.sh toolchain        # Toolchain only
./build.sh tools            # Host tools (lua, apk)
./build.sh firmware         # Packages + images
./build.sh clean all        # Clean everything
```

### CLI Commands
```bash
python -m owrt_build info armsr-armv8
python -m owrt_build toolchain build armsr-armv8
python -m owrt_build kernel build armsr-armv8
python -m owrt_build package armsr-armv8 busybox
python -m owrt_build apk-index armsr-armv8
python -m owrt_build ninja generate armsr-armv8
python -m owrt_build ninja run armsr-armv8
```

### Output Locations
```
poc/build/
├── toolchain/armsr-armv8/    # Cross-compiler
├── host-staging/             # Host tools (apk, lua)
├── kernel/armsr-armv8/       # Kernel build
├── packages/armsr-armv8/     # Package builds
├── staging/armsr-armv8/      # Installed headers/libs
├── apk-packages/armsr-armv8/ # Individual .apk files
├── apk-repo/armsr-armv8/     # Repository with APKINDEX
└── rootfs/armsr-armv8/       # Assembled rootfs
```

---

*Last updated: 2026-01-18*
*Branch: claude/plan-build-system-8DNjM*
