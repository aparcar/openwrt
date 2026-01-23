# OpenWrt Modern Build System (owrt)

A modern, declarative build system for OpenWrt using YAML package definitions,
Docker-based build environments, and Ninja build orchestration.

## Quick Start

```bash
# Build firmware for ARM64 target
python3 -m owrt build armsr-armv8

# Build with specific profile
python3 -m owrt build armsr-armv8 -p generic

# Build a single package
python3 -m owrt package armsr-armv8 busybox
```

## Architecture

```
owrt/
├── __main__.py      # CLI entry point
├── config.py        # Target and package configuration (YAML loading)
├── toolchain.py     # Cross-toolchain builder (GCC, binutils, musl)
├── kernel.py        # Linux kernel builder
├── package.py       # Package builder with dependency resolution
├── apk.py           # APK v3 package creation and repository management
├── image.py         # Firmware image generation
├── pipeline.py      # Image processing pipeline (compress, pad, concat)
├── container.py     # Docker container management
├── resolver.py      # Dependency resolution with virtual package support
├── tool.py          # Host tool builder (apk, mkimage, fwtool, etc.)
├── kmod.py          # Kernel module packaging
├── qemu.py          # QEMU-based runtime testing
└── utils.py         # Utility functions
```

## Documentation

- [Getting Started](getting-started.md) - Installation and first build
- [Package Format](package-format.md) - Writing package.yaml files
- [Target Configuration](target-config.md) - Defining build targets
- [CLI Reference](cli-reference.md) - Command-line interface
- [Build Pipeline](build-pipeline.md) - How the build system works
- [Testing](testing.md) - Running tests

## Key Features

### Declarative Package Definitions

Packages are defined in YAML instead of Makefiles:

```yaml
name: busybox
version: 1.36.1
license: GPL-2.0

source:
  type: tarball
  url: https://busybox.net/downloads/busybox-${version}.tar.bz2
  hash: sha256:...

dependencies:
  build: []
  runtime: [libc]

build:
  system: make
```

### Docker-Based Build Environment

All builds run in containers with pre-built host tools:

```bash
# Base image includes: apk, mkimage, fwtool, squashfs4, mtd-utils
docker build -t openwrt-base docker/

# Toolchain images add cross-compiler for each target
python3 -m owrt toolchain build armsr-armv8
```

### Content-Addressable Caching

Packages are rebuilt only when their inputs change:

- Source files and patches
- Build dependencies
- Toolchain version

### Parallel Builds with Ninja

The build system generates Ninja files for maximum parallelism:

```bash
python3 -m owrt ninja generate armsr-armv8
ninja -f build/armsr-armv8/build.ninja
```

## Directory Structure

```
openwrt-ng/
├── docker/              # Dockerfile for build environment
├── docs/                # Documentation
├── owrt/                # Build system Python modules
│   ├── tools/           # Host tool definitions (apk, mkimage, etc.)
│   ├── tests/           # Unit tests
│   └── schema/          # JSON schemas for validation
├── package/             # Package definitions
│   ├── base-files/
│   ├── busybox/
│   └── ...
├── target/              # Target configurations
│   └── linux/
│       ├── armsr/armv8/
│       ├── mediatek/filogic/
│       └── x86/64/
├── toolchain/           # Toolchain patches
│   ├── binutils/
│   ├── gcc/
│   └── musl/
└── build/               # Build output (generated)
    ├── toolchain/       # Cross-compilers
    ├── packages/        # Built packages
    ├── kernel/          # Kernel builds
    └── output/          # Firmware images
```

## Requirements

- Python 3.10+
- Docker (for containerized builds)
- 8GB RAM minimum, 16GB recommended
- 20GB disk space per target

## License

GPL-2.0, matching OpenWrt.
