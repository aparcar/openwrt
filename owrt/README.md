# OpenWrt Modern Build System - Proof of Concept

This is a proof of concept for a modern replacement of the OpenWrt build system. It demonstrates:

- **YAML-based package definitions** instead of complex Make macros
- **Docker-based build environments** for reproducibility
- **Toolchain built from OpenWrt sources** (binutils, gcc, musl)
- **Kernel build with full patch support** (backport, pending, hack, target-specific)
- **Device Tree compilation** from OpenWrt's DTS files
- **Modular Python CLI** for build orchestration

## Quick Start

### Prerequisites

- Docker and Docker Compose
- Python 3.10+ (for local development without Docker)
- Git

### Building with Docker (Recommended)

```bash
# Enter the PoC directory
cd poc

# Build the Docker image
docker compose -f docker/docker-compose.yml build

# Start interactive shell
docker compose -f docker/docker-compose.yml run shell

# Inside container: build everything for ARM64
python3 -m owrt_build build armsr-armv8
```

### Building Locally (for development)

```bash
# Install dependencies
pip install pyyaml click

# Set environment variables
export OPENWRT_DIR=/path/to/openwrt
export POC_DIR=/path/to/openwrt/poc
export BUILD_DIR=/path/to/build
export OUTPUT_DIR=/path/to/output

# Build toolchain
python3 -m owrt_build toolchain build armsr-armv8

# Build kernel
python3 -m owrt_build kernel build armsr-armv8

# Build complete firmware
python3 -m owrt_build build armsr-armv8
```

## CLI Commands

```bash
# Show target information
python3 -m owrt_build info armsr-armv8

# Build toolchain only
python3 -m owrt_build toolchain build armsr-armv8

# Build kernel only
python3 -m owrt_build kernel build armsr-armv8

# Build kernel DTBs only
python3 -m owrt_build kernel dtbs armsr-armv8

# Run kernel menuconfig
python3 -m owrt_build kernel menuconfig armsr-armv8

# Build single package
python3 -m owrt_build package armsr-armv8 busybox

# Build complete firmware
python3 -m owrt_build build armsr-armv8 --profile generic

# Generate images only
python3 -m owrt_build image armsr-armv8 --profile generic

# Clean build
python3 -m owrt_build clean armsr-armv8
python3 -m owrt_build clean armsr-armv8 --all  # Include toolchain
```

## Directory Structure

```
poc/
├── docker/
│   ├── Dockerfile              # Build environment
│   └── docker-compose.yml      # Docker services
│
├── owrt_build/                 # Python build system
│   ├── __init__.py
│   ├── __main__.py            # CLI entry point
│   ├── config.py              # Configuration loading
│   ├── toolchain.py           # Toolchain builder
│   ├── kernel.py              # Kernel builder (patches, DTS)
│   ├── package.py             # Package builder
│   ├── image.py               # Image generator
│   └── utils.py               # Utility functions
│
├── packages/                   # YAML package definitions
│   ├── base-files/
│   │   ├── package.yaml
│   │   └── files/             # Root filesystem files
│   ├── busybox/
│   │   └── package.yaml
│   └── linux/
│       └── package.yaml
│
├── targets/                    # Target definitions
│   └── armsr-armv8/
│       └── target.yaml
│
├── schema/                     # JSON schemas
│   ├── package.schema.json
│   └── target.schema.json
│
└── build/                      # Build output (gitignored)
    └── armsr-armv8/
        ├── toolchain/         # Cross-compiler
        ├── kernel/            # Kernel build
        ├── packages/          # Package builds
        ├── staging/           # Installed files
        ├── rootfs/            # Root filesystem
        └── images/            # Final images
```

## Target: armsr-armv8

The PoC uses the `armsr-armv8` target (ARM SystemReady, 64-bit ARMv8/aarch64):

- **Architecture:** aarch64
- **Toolchain:** GCC 14.3.0, Binutils 2.44, musl 1.2.5
- **Kernel:** 6.12.65
- **Features:** EFI boot, PCI/PCIe, USB, ext4, squashfs

## Kernel Handling

The kernel builder handles:

1. **Downloading** kernel source with hash verification
2. **Applying patches** in order:
   - `target/linux/generic/backport-6.12/` (upstream backports)
   - `target/linux/generic/pending-6.12/` (pending upstream)
   - `target/linux/generic/hack-6.12/` (OpenWrt-specific)
   - `target/linux/armsr/patches-6.12/` (target-specific)
3. **Copying kernel files** from `files/` and `files-6.12/` directories
4. **Merging kernel configs** from multiple fragments
5. **Compiling** kernel and modules
6. **Building Device Trees** from DTS files

## Package Definitions

Packages are defined in YAML format:

```yaml
name: example
version: "1.0.0"
license: MIT

source:
  type: tarball
  url: "https://example.com/example-1.0.0.tar.gz"
  sha256: "..."

dependencies:
  runtime: [libc]
  build: []

build:
  system: autotools  # or cmake, meson, make, custom
  configure_args:
    - "--disable-docs"

install:
  files:
    - src: "${build_dir}/example"
      dst: "/usr/bin/example"
      mode: "0755"
```

## Comparison with Current System

| Aspect | Current (Makefile) | PoC (YAML/Python) |
|--------|-------------------|-------------------|
| Package definition | Complex Make macros | Simple YAML |
| Config parsing | Seconds (run each Makefile) | Instant (YAML load) |
| Build environment | Manual setup | Docker/reproducible |
| Toolchain | Always rebuild | Cached, reusable |
| Learning curve | Steep (Make macros) | Low (declarative) |
| IDE support | None | YAML schema validation |

## Ninja Build System

The PoC includes a Ninja-based build orchestrator for parallel builds:

```bash
# Show build plan (dry-run)
python -m owrt_build ninja plan mediatek-filogic

# Generate Ninja build file
python -m owrt_build ninja generate mediatek-filogic

# Run parallel build
python -m owrt_build ninja run mediatek-filogic

# Generate dependency graph
python -m owrt_build ninja graph mediatek-filogic
dot -Tpng build/mediatek-filogic/graph.dot -o graph.png
```

The Ninja integration provides:
- Parallel package builds with proper dependency ordering
- Content-addressable caching (skips unchanged packages)
- Visual dependency graphs
- Fine-grained rebuilds

## Future Work

- [x] Ninja build file generation for parallel builds
- [ ] Content-addressable package caching (S3/HTTP backends)
- [ ] SDK generation
- [ ] More targets (x86_64, mips, etc.)
- [ ] Migration tool for existing Makefiles
- [ ] Feed support for external packages
