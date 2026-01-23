# Toolchain Building

This document describes how the OpenWrt build system constructs cross-compilation
toolchains for different target architectures.

## Overview

A toolchain is the collection of programs needed to compile code for a specific
target architecture. The OpenWrt toolchain consists of:

- **Binutils** - Assembler, linker, and binary utilities
- **GCC** - GNU Compiler Collection (C and C++ compilers)
- **musl** - Lightweight C library optimized for embedded systems
- **Kernel Headers** - Linux kernel API headers for system calls

## Build Process

The toolchain is built in a specific order due to dependencies between components:

```
┌─────────────┐
│  Binutils   │  [1] Assembler and linker - no dependencies
└──────┬──────┘
       │
       ▼
┌─────────────┐
│ GCC Initial │  [2] Minimal GCC for compiling musl
└──────┬──────┘      (no libc support yet)
       │
       ▼
┌──────────────┐
│Kernel Headers│ [3] Linux API headers
└──────┬───────┘
       │
       ▼
┌─────────────┐
│    Musl     │  [4] C library (uses GCC initial)
└──────┬──────┘
       │
       ▼
┌─────────────┐
│  GCC Final  │  [5] Full GCC with C++ and libc support
└─────────────┘
```

### Stage 1: Binutils

Binutils provides the assembler (`as`), linker (`ld`), and utilities like `objcopy`,
`objdump`, `ar`, etc. It has no dependencies and is built first.

Key configure options:
- `--target=<tuple>` - Cross-compilation target (e.g., `aarch64-openwrt-linux-musl`)
- `--with-sysroot` - Sysroot directory for headers and libraries
- `--enable-deterministic-archives` - Reproducible builds

### Stage 2: GCC Initial

A minimal GCC build that can compile C code but has no libc support. This is used
to compile musl in the next stage.

Key configure options:
- `--enable-languages=c` - Only C compiler, no C++
- `--without-headers` - No libc headers yet
- `--with-newlib` - Minimal runtime support
- `--disable-shared` - Static only

This stage also builds `libgcc`, which provides low-level runtime support functions
(like `__trunctfdf2` for floating-point conversions) that musl needs.

### Stage 3: Kernel Headers

Linux kernel headers are installed to provide the system call interface. These
define structures and constants for interacting with the kernel.

The headers are architecture-specific and installed with:
```bash
make ARCH=<arch> INSTALL_HDR_PATH=<sysroot>/usr headers_install
```

### Stage 4: Musl

Musl is a lightweight, fast, and correct C library implementation. It's preferred
for embedded systems due to its small size and static linking support.

Musl is compiled using the initial GCC and links against the kernel headers.

### Stage 5: GCC Final

The final GCC build has full C and C++ support with the musl libc. This is the
compiler used to build all target packages.

Key configure options:
- `--enable-languages=c,c++` - Both C and C++ compilers
- `--enable-__cxa_atexit` - Proper C++ destructors
- `--enable-libstdcxx-dual-abi` - C++11 ABI support

## Directory Structure

After building, the toolchain is installed to:

```
build/toolchains/<target>/
├── bin/                    # Cross-compiler binaries
│   ├── aarch64-openwrt-linux-musl-gcc
│   ├── aarch64-openwrt-linux-musl-g++
│   ├── aarch64-openwrt-linux-musl-ld
│   └── ...
├── lib/                    # Host libraries for GCC
│   └── gcc/<target>/<version>/
├── libexec/                # GCC helper programs
├── include/                # GCC headers
├── usr/                    # Sysroot (target files)
│   ├── include/            # C library and kernel headers
│   └── lib/                # C library
└── stamp/                  # Build stage markers
    ├── binutils_installed
    ├── gcc_initial_installed
    ├── kernel_headers_installed
    ├── musl_installed
    └── gcc_final_installed
```

## Target Tuple Format

The target tuple identifies the cross-compilation target:

```
<arch>-<vendor>-<os>-<libc>
```

Examples:
- `aarch64-openwrt-linux-musl` - 64-bit ARM with musl
- `arm-openwrt-linux-musleabi` - 32-bit ARM with EABI
- `mipsel-openwrt-linux-musl` - Little-endian MIPS
- `x86_64-openwrt-linux-musl` - x86-64

## Environment Variables

When using the toolchain, these environment variables are set:

| Variable | Description | Example |
|----------|-------------|---------|
| `CC` | C compiler | `aarch64-openwrt-linux-musl-gcc` |
| `CXX` | C++ compiler | `aarch64-openwrt-linux-musl-g++` |
| `AR` | Archiver | `aarch64-openwrt-linux-musl-ar` |
| `LD` | Linker | `aarch64-openwrt-linux-musl-ld` |
| `STRIP` | Strip symbols | `aarch64-openwrt-linux-musl-strip` |
| `OBJCOPY` | Object copy | `aarch64-openwrt-linux-musl-objcopy` |
| `CROSS_COMPILE` | Prefix for tools | `aarch64-openwrt-linux-musl-` |
| `STAGING_DIR` | Staging directory | `/path/to/staging` |

## ccache Support

The toolchain supports ccache for faster rebuilds:

```bash
owrt build --ccache
```

This prepends `ccache` to the `CC` and `CXX` variables, caching compilation results.
Set `CCACHE_DIR` to specify the cache directory.

## Component Versions

Default versions (can be overridden in target config):

| Component | Version |
|-----------|---------|
| Binutils | 2.44 |
| GCC | 14.3.0 |
| Musl | 1.2.5 |
| Linux Headers | 6.12.65 |

## Patching

OpenWrt applies patches to toolchain components for:
- Bug fixes not yet in upstream releases
- Embedded-specific optimizations
- Security hardening

Patches are stored in:
```
toolchain/<component>/patches[-<version>]/
```

## CLI Commands

```bash
# Build toolchain for a target
owrt toolchain build armsr-armv8

# Clean toolchain build artifacts
owrt toolchain clean armsr-armv8

# Check if toolchain is built
owrt toolchain status armsr-armv8
```

## Troubleshooting

### Build fails with "cannot find crti.o"

The sysroot is missing C library files. Ensure musl was built and installed correctly.

### GCC cannot find kernel headers

Kernel headers weren't installed to the correct location. They should be at
`<sysroot>/usr/include/linux/`.

### Floating-point errors with musl

Ensure `libgcc` was built and installed during the initial GCC stage. Musl requires
`libgcc` for some floating-point operations.

### Build fails with "host compiler not found"

The toolchain builder uses the host `gcc` (not the cross-compiler) to build
toolchain components. Ensure `gcc` and `g++` are installed on the host system.

## Implementation Details

The toolchain builder is implemented in `owrt/toolchain.py`. Key features:

- **Stamp files** - Track which stages are complete to support incremental builds
- **Host environment isolation** - Prevents cross-compiler from interfering with
  toolchain builds
- **Source caching** - Downloaded tarballs are cached in `dl/` directory
- **Parallel builds** - Uses `make -jN` for faster compilation
