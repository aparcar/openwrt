# Getting Started

This guide walks you through building your first OpenWrt firmware image using
the modern build system.

## Prerequisites

- Python 3.10 or later
- Docker (recommended) or native build dependencies
- 8GB RAM minimum, 16GB recommended
- 20GB free disk space per target

### Python Dependencies

```bash
pip install pyyaml jsonschema click rich
```

### Docker Setup (Recommended)

The build system works best with Docker, which provides a reproducible
environment with all dependencies pre-installed.

```bash
# Build the base image (includes host tools)
docker build -t openwrt-build-base docker/
```

## Building Firmware

### Quick Build

The simplest way to build firmware:

```bash
# Build for ARM64 (QEMU-compatible)
python3 -m owrt build armsr-armv8

# Build for MediaTek Filogic (real hardware)
python3 -m owrt build mediatek-filogic -p bananapi_bpi-r3
```

### Step-by-Step Build

For more control, build each component separately:

```bash
# 1. Build the cross-compilation toolchain
python3 -m owrt toolchain build armsr-armv8

# 2. Build the Linux kernel
python3 -m owrt kernel build armsr-armv8

# 3. Build packages
python3 -m owrt package armsr-armv8 busybox
python3 -m owrt package armsr-armv8 dropbear

# 4. Generate firmware images
python3 -m owrt image armsr-armv8 -p generic
```

### Using Ninja for Parallel Builds

For faster builds, use the Ninja backend:

```bash
# Generate Ninja build file
python3 -m owrt ninja generate armsr-armv8

# Run parallel build
python3 -m owrt ninja run armsr-armv8
```

## Build Outputs

After a successful build, find your firmware in:

```
build/
├── toolchain/armsr-armv8/     # Cross-compiler
├── kernel/armsr-armv8/        # Kernel build
├── packages/aarch64/          # Built packages
├── apk-repo/aarch64/          # APK repository
└── output/
    └── armsr-armv8/
        └── images/            # Firmware images
            ├── openwrt-armsr-armv8-generic-initramfs-kernel.bin
            └── openwrt-armsr-armv8-generic-squashfs-combined.img.gz
```

## Testing with QEMU

The `armsr-armv8` target can be tested in QEMU:

```bash
# Run QEMU smoke tests
python3 -m owrt test qemu armsr-armv8 \
    -f build/output/armsr-armv8/images/openwrt-*-initramfs-kernel.bin

# Boot interactively
qemu-system-aarch64 \
    -machine virt \
    -cpu cortex-a53 \
    -m 512M \
    -kernel build/output/armsr-armv8/images/openwrt-*-initramfs-kernel.bin \
    -nographic
```

## Available Targets

List available targets:

```bash
python3 -m owrt info armsr-armv8
python3 -m owrt info mediatek-filogic
python3 -m owrt info x86-64
```

## Next Steps

- [Package Format](package-format.md) - Learn to write package definitions
- [Target Configuration](target-config.md) - Configure new targets
- [CLI Reference](cli-reference.md) - All available commands
