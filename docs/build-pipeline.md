# Build Pipeline

This document describes how the OpenWrt modern build system works internally,
from source to firmware image.

## Overview

The build pipeline has five main stages:

```
┌───────────────┐     ┌───────────────┐     ┌───────────────┐
│   Toolchain   │────▶│    Kernel     │────▶│   Packages    │
│   (host gcc,  │     │  (linux +     │     │  (userspace   │
│   binutils,   │     │   dtbs +      │     │   software)   │
│   musl)       │     │   kmod.apk)   │     │               │
└───────────────┘     └───────────────┘     └───────────────┘
                                                    │
                                                    ▼
                      ┌───────────────┐     ┌───────────────┐
                      │    Images     │◀────│    Rootfs     │
                      │  (firmware    │     │  (APK-based   │
                      │   files)      │     │   assembly)   │
                      └───────────────┘     └───────────────┘
```

## Stage 1: Toolchain

Builds the cross-compilation toolchain from source:

1. **binutils** - Cross-assembler and linker
2. **gcc-initial** - Minimal GCC for compiling libc
3. **kernel-headers** - Linux kernel headers
4. **musl** - C library
5. **gcc-final** - Full GCC with C++ support

OpenWrt applies custom patches to all components for size optimization
and embedded-specific fixes.

```bash
python3 -m owrt toolchain build armsr-armv8
```

Output: `build/toolchain/<target>/bin/<target-tuple>-gcc`

## Stage 2: Kernel

Builds the Linux kernel with target-specific configuration:

1. **Download** - Fetch kernel source
2. **Patch** - Apply generic + target patches
3. **Configure** - Merge config fragments
4. **Compile** - Build kernel and modules
5. **Package** - Create kmod-*.apk packages

```bash
python3 -m owrt kernel build armsr-armv8
python3 -m owrt kernel modules armsr-armv8
```

Output:
- `build/kernel/<target>/vmlinux` - Kernel binary
- `build/apk-repo/<arch>/kmod-*.apk` - Module packages

## Stage 3: Packages

Builds userspace packages in dependency order:

1. **Resolve** - Compute build order from dependencies
2. **Download** - Fetch all sources in parallel
3. **Build** - Compile packages (parallel with Ninja)
4. **Package** - Create APK v3 packages
5. **Index** - Generate APK repository

```bash
python3 -m owrt package armsr-armv8 busybox
python3 -m owrt apk-index armsr-armv8
```

Output: `build/apk-repo/<arch>/*.apk`

## Stage 4: Rootfs

Assembles the root filesystem:

1. **Create base structure** - /bin, /etc, /lib, etc.
2. **Install packages** - APK installs packages to rootfs
3. **Run postinst scripts** - Configure packages
4. **Finalize** - Create symlinks, set permissions

The rootfs uses APK v3 format for package management.

## Stage 5: Images

Generates firmware images from rootfs:

1. **Compress rootfs** - Create SquashFS or initramfs
2. **Process kernel** - Append DTB, compress
3. **Assemble image** - Combine kernel + rootfs
4. **Generate variants** - sysupgrade, factory, sdcard

```bash
python3 -m owrt image armsr-armv8 -p generic
```

Output: `build/output/<target>/images/`

## Ninja Build System

For parallel builds, the system generates Ninja files:

### Build Order

Ninja ensures correct build order:

```
toolchain ─────┬─────────────────────▶ packages ──▶ images
               │                           ▲
               └─▶ kernel ─▶ kmod-*.apk ───┘
```

### Content Hashing

Packages are rebuilt only when inputs change:

- Source files (package.yaml, patches/)
- Build dependencies
- Toolchain version

Hash stored in `build/packages/<arch>/stamp/<pkg>.key`

### Parallel Execution

```bash
# Generate ninja file
python3 -m owrt ninja generate armsr-armv8

# Run build with all cores
ninja -f build/armsr-armv8/build.ninja

# Build specific target
ninja -f build/armsr-armv8/build.ninja packages
```

### Pool Management

Ninja uses resource pools:

| Pool | Depth | Purpose |
|------|-------|---------|
| `download_pool` | 32 | Parallel downloads |
| `package_pool` | 16 | Concurrent package builds |
| `console` | 1 | Toolchain/kernel (full CPU) |

## Image Pipeline

Image generation uses a declarative pipeline:

```yaml
# Example pipeline in target.yaml
image:
  pipeline:
    - kernel-bin
    - append-dtb: ${dtb_file}
    - pad-to: 64K
    - gzip
    - save: kernel.bin.gz
    
    - load: rootfs.squashfs
    - pad-extra: 256
    - concat: [kernel.bin.gz, rootfs.squashfs]
    - save: firmware-combined.img
```

### Available Steps

| Step | Description |
|------|-------------|
| `kernel-bin` | Load kernel binary |
| `append-dtb` | Append device tree blob |
| `append-rootfs` | Append rootfs to kernel |
| `gzip`, `lzma`, `xz` | Compression |
| `pad-to` | Pad to size boundary |
| `pad-extra` | Add padding bytes |
| `concat` | Concatenate files |
| `save` | Save artifact |
| `load` | Load artifact |
| `sysupgrade-tar` | Create sysupgrade tarball |
| `check-size` | Verify size limits |

## Docker Integration

All builds run in containers:

```
┌─────────────────────────────────────────────────────┐
│                   Host System                        │
│                                                     │
│  ┌─────────────────────────────────────────────┐   │
│  │           Base Container                     │   │
│  │  - Ubuntu 24.04                             │   │
│  │  - Build dependencies                       │   │
│  │  - Host tools (apk, mkimage, etc.)         │   │
│  └─────────────────────────────────────────────┘   │
│                      │                              │
│  ┌─────────────────────────────────────────────┐   │
│  │        Toolchain Container                   │   │
│  │  - Base image + cross-compiler              │   │
│  │  - aarch64-openwrt-linux-musl-gcc          │   │
│  └─────────────────────────────────────────────┘   │
│                                                     │
│  Volumes:                                          │
│  - /openwrt: source code (read-only)              │
│  - /build/dl: download cache                      │
│  - /build/output: build outputs                   │
└─────────────────────────────────────────────────────┘
```

## Caching Strategy

Multiple caching layers:

1. **Download cache** - `dl/` directory persisted
2. **Toolchain image** - Cached in container registry
3. **Package stamps** - Per-architecture, hash-based
4. **ccache** - Compiler cache (optional)

### Cache Keys

| Component | Key Based On |
|-----------|--------------|
| Base image | Dockerfile hash |
| Toolchain | GCC version + patches + base hash |
| Package | package.yaml + patches + deps + toolchain |

## Environment Variables

| Variable | Description |
|----------|-------------|
| `BUILD_DIR` | Build output directory |
| `DL_DIR` | Download cache directory |
| `STAGING_DIR` | Package staging directory |
| `TOOLCHAIN_DIR` | Toolchain installation |
| `PKG_CONFIG_PATH` | pkg-config search path |

## Debugging Builds

### Verbose Output

```bash
python3 -m owrt -v build armsr-armv8
```

### Single Package

```bash
python3 -m owrt -v package armsr-armv8 busybox
```

### Ninja Graph

```bash
python3 -m owrt ninja graph armsr-armv8 -o graph.dot
dot -Tpng graph.dot -o graph.png
```

### Build Plan

```bash
python3 -m owrt ninja plan armsr-armv8
```
