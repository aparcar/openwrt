# Package Building

This document describes how the OpenWrt build system builds userspace packages
from source code.

## Overview

The package builder handles:

- Loading package definitions from YAML
- Downloading and extracting source code
- Building with various build systems (autotools, cmake, meson, make)
- Installing to staging directories
- Creating APK packages for distribution
- Fakechroot isolation for security

## Build Process

For each package:

```
┌─────────────────┐
│ Load YAML config│
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│Resolve deps     │  Topologically sort packages by dependencies
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│Download source  │  From upstream URL or Git repo
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│Extract & patch  │  Apply OpenWrt patches from patches/
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│Install deps     │  Install build dependencies via APK
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│Configure        │  Run configure/cmake/meson
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│Compile          │  Cross-compile for target arch
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│Install          │  Install to staging and ipkg-install
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│Create APK       │  Package files into APK
└─────────────────┘
```

## Build Systems

The builder supports several build systems:

### autotools

For packages using GNU autotools (configure/make):

```yaml
build:
  system: autotools
  configure_args:
    - --disable-dependency-tracking
    - --disable-nls
```

Commands executed:
```bash
./configure --target=<tuple> --host=<tuple> --prefix=/usr [args...]
make -j<jobs>
make DESTDIR=/staging install
```

### cmake

For packages using CMake:

```yaml
build:
  system: cmake
  cmake_options:
    - -DBUILD_SHARED_LIBS=ON
```

Uses in-source builds to match OpenWrt behavior. Commands:
```bash
cmake . -DCMAKE_SYSTEM_NAME=Linux -DCMAKE_C_COMPILER=<gcc> [options...]
make -j<jobs>
make DESTDIR=/staging install
```

### meson

For packages using Meson:

```yaml
build:
  system: meson
  configure_args:
    - -Dexamples=false
```

Generates a cross-compilation file and runs:
```bash
meson setup build . --cross-file=openwrt-cross.txt
ninja -C build -j<jobs>
DESTDIR=/staging ninja -C build install
```

### make

For packages with simple Makefiles:

```yaml
build:
  system: make
  make_args:
    - PREFIX=/usr
```

Commands:
```bash
make -j<jobs> CC=<gcc> CROSS_COMPILE=<prefix>- [args...]
make DESTDIR=/staging install
```

### custom

For packages needing custom build scripts:

```yaml
build:
  system: custom
  configure_script: |
    ./autogen.sh
    ./configure --host=$TARGET
  compile_script: |
    make -j$JOBS special-target
  install_script: |
    make DESTDIR=$DESTDIR install
```

Environment variables available:
- `CC`, `CXX`, `AR`, `RANLIB` - Cross-compiler tools
- `OWRT_ARCH` - Target architecture (aarch64, arm, x86_64, etc.)
- `CROSS_COMPILE` - Cross-compiler prefix
- `JOBS`, `NPROC` - Number of parallel jobs
- `DESTDIR` - Installation directory
- `LINUX_DIR` - Kernel source directory
- `KERNEL_VERSION` - Kernel version
- `KERNEL_ARCH` - Kernel architecture

### none

For packages with no build step (like base-files):

```yaml
build:
  system: none
```

Only files from the `files/` directory are copied.

## Package Isolation

All packages are built with fakechroot isolation:

- **Per-package staging**: Each package has its own staging directory
- **Dependency installation**: Build deps are installed via APK into isolated root
- **No cross-contamination**: Packages cannot see other packages' build artifacts

This provides:
1. **Security**: Packages cannot interfere with each other
2. **Reproducibility**: Builds only depend on declared dependencies
3. **Parallelization**: Independent packages can build concurrently

## Change Detection

The builder uses content-based hashing for incremental builds:

Inputs hashed:
- package.yaml content
- patches/ directory contents
- files/ directory contents
- Version and release numbers
- Toolchain version (GCC, libc)
- Dependency hashes (cascading rebuilds)

Hash stored in stamp file:
```
packages/stamp/<name>.built_<hash>
```

If inputs change → hash changes → old stamp doesn't match → rebuild triggered.

## Directory Structure

```
build/
├── packages/<target>/
│   └── <package>/
│       ├── build/           # Build directory
│       ├── staging/         # Per-package staging
│       ├── deps/            # Assembled dependencies
│       └── ipkg-install/    # Files to package
├── staging/<target>/        # Shared staging (InstallDev)
├── apk-packages/<arch>/     # Built APK files
└── apk-repo/<arch>/         # APK repository
```

## Subpackages

A source package can produce multiple binary packages:

```yaml
name: openssl
subpackages:
  libopenssl:
    description: OpenSSL library
    files:
      - usr/lib/libssl.so*
      - usr/lib/libcrypto.so*
  openssl-util:
    description: OpenSSL utilities
    files:
      - usr/bin/openssl
```

Subpackages:
- Share the same source and build
- Have separate APK packages
- Can have different dependencies

## Variants

Variants are packages built from the same source but with different build options:

```yaml
name: libcurl
variants:
  libcurl-openssl:
    cmake_options:
      - -DCURL_USE_OPENSSL=ON
    build_deps:
      - libopenssl
  libcurl-mbedtls:
    cmake_options:
      - -DCURL_USE_MBEDTLS=ON
    build_deps:
      - libmbedtls
```

Unlike subpackages, variants require separate compilation.

## Development Packages

The `-dev` package (headers and development files) is auto-generated from
`install.staging` content:

```yaml
install:
  staging:
    - src: include/*.h
      dst: /usr/include/
    - src: lib/*.a
      dst: /usr/lib/
```

Files installed to staging become the `-dev` package.

## Target Overlays

For target-specific files (especially `base-files`), overlays are applied:

1. Package's `files/` directory
2. Target overlay: `target/linux/<board>/base-files/`
3. Subtarget overlay: `target/linux/<board>/<subtarget>/base-files/`

Later overlays override earlier ones.

## CLI Commands

```bash
# Build a single package
owrt package build busybox

# Build with verbose output
owrt -v package build dnsmasq

# Force rebuild
owrt package build --force libc

# Build all packages (buildbot mode)
owrt package build --all-packages --continue-on-error
```

## Troubleshooting

### "Package definition not found"

The package YAML doesn't exist or has an error. Check:
- `packages/<category>/<name>/package.yaml` exists
- YAML syntax is valid

### Build fails finding headers

The package is missing build dependencies:
```yaml
build_deps:
  - libncurses-dev  # Provides ncurses.h
```

### Linker errors "undefined reference"

Missing library dependency:
```yaml
build_deps:
  - libz  # Provides libz.so
```

### "fakechroot not available"

Install fakechroot in the Docker image or run without isolation (less secure).

### Hash keeps changing

Check if any input files are being modified during build:
- Source files in patches/
- Generated files in files/
- Dependencies being rebuilt
