# OpenWrt Build System Documentation

Welcome to the OpenWrt modern build system documentation. This build system
is a Python-based replacement for the traditional Makefile-based system,
offering faster builds, better dependency tracking, and more maintainable code.

## Quick Start

```bash
# Build everything for a target
owrt build armsr-armv8

# Build with a specific profile
owrt build --profile generic armsr-armv8

# Build initramfs image for testing
owrt build --initramfs armsr-armv8

# Test in QEMU
owrt qemu armsr-armv8
```

## Documentation Index

### Getting Started

- [Getting Started Guide](getting-started.md) - Prerequisites, installation, first build
- [CLI Reference](cli-reference.md) - Complete command-line interface documentation

### Architecture

- [Architecture Overview](README.md) - System design and component overview
- [Build Pipeline](build-pipeline.md) - Ninja-based build orchestration

### Components

- [Toolchain](toolchain.md) - Cross-compilation toolchain building
- [Kernel](kernel.md) - Linux kernel compilation and configuration
- [Package Building](package-building.md) - Userspace package compilation
- [Image Building](image-building.md) - Firmware image generation

### Configuration

- [Target Configuration](target-config.md) - target.yaml reference
- [Package Format](package-format.md) - package.yaml reference

## Key Concepts

### Targets

A target is a combination of board and subtarget:
- `armsr-armv8` - ARM64 virtual machines
- `mediatek-filogic` - MediaTek Filogic SoCs
- `x86-64` - x86-64 PCs

### Profiles

Each target has multiple device profiles:
- Hardware-specific configurations
- Device Tree selection
- Default package sets
- Image formats

### Packages

Packages are defined in YAML:
- Source location and build system
- Dependencies (build and runtime)
- Installation files
- Subpackages and variants

## Build Workflow

```
┌─────────────┐    ┌─────────────┐    ┌─────────────┐
│  Toolchain  │───▶│   Kernel    │───▶│  Packages   │
└─────────────┘    └─────────────┘    └─────────────┘
                                             │
                                             ▼
                   ┌─────────────┐    ┌─────────────┐
                   │   Images    │◀───│   Rootfs    │
                   └─────────────┘    └─────────────┘
```

1. **Toolchain** - Build cross-compilation toolchain
2. **Kernel** - Compile Linux kernel with patches
3. **Packages** - Build userspace packages
4. **Rootfs** - Assemble root filesystem
5. **Images** - Generate firmware images

## Features

### Content-Based Caching

- Hash-based rebuild detection
- Only rebuild what changed
- Cascading rebuilds on dependency changes

### Package Isolation

- Each package built in isolation
- Dependencies installed via APK
- No cross-package contamination

### Parallel Builds

- Ninja-based build orchestration
- Automatic dependency tracking
- Maximum CPU utilization

### APK Package Format

- Modern Alpine-compatible packages
- Signed repositories
- Atomic updates

## Directory Structure

```
openwrt-ng/
├── owrt/                 # Build system Python code
├── packages/             # Package definitions
│   └── <category>/
│       └── <name>/
│           ├── package.yaml
│           ├── patches/
│           └── files/
├── targets/              # Target configurations
│   └── <board>-<subtarget>/
│       └── target.yaml
├── poc/                  # Proof-of-concept configs
├── toolchain/            # Toolchain component patches
├── build/                # Build output (generated)
│   ├── toolchains/
│   ├── packages/
│   ├── kernel-build/
│   └── images/
└── docs/                 # Documentation
```

## Contributing

See the [GitHub repository](https://github.com/openwrt/openwrt-ng) for:
- Issue tracking
- Pull requests
- Development discussion

## License

This build system is licensed under the GPL v2, same as OpenWrt.
