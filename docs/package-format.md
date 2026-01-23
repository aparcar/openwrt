# Package Format

Packages are defined in YAML files instead of Makefiles. This provides a
declarative, easily parseable format that's faster to scan and simpler to write.

## File Location

Package definitions are stored in `package/<name>/package.yaml`:

```
package/
├── base-files/
│   └── package.yaml
├── busybox/
│   └── package.yaml
│   └── patches/
│       └── 001-fix.patch
└── openssl/
    └── package.yaml
```

## Basic Structure

```yaml
name: example-package
version: 1.2.3
release: 1
license: MIT

metadata:
  title: Example Package
  description: A sample package definition
  section: utils
  maintainer: developer@example.com

source:
  type: tarball
  url: https://example.com/example-${version}.tar.gz
  hash: sha256:abc123...

dependencies:
  build:
    - libc
    - zlib
  runtime:
    - libc

build:
  system: autotools
  configure_args:
    - --disable-static
    - --enable-shared

install:
  files:
    - src: usr/bin/*
      dst: /usr/bin/
    - src: usr/lib/*.so*
      dst: /usr/lib/
```

## Required Fields

| Field | Description |
|-------|-------------|
| `name` | Package name (must match directory name) |
| `version` | Upstream version number |

## Optional Fields

| Field | Default | Description |
|-------|---------|-------------|
| `release` | 1 | Package release number (increment for packaging changes) |
| `license` | - | SPDX license identifier |
| `metadata` | {} | Package metadata (title, description, section) |
| `source` | {} | Source download configuration |
| `dependencies` | {} | Build and runtime dependencies |
| `build` | {} | Build system configuration |
| `install` | {} | Installation files and symlinks |

## Source Types

### Tarball

```yaml
source:
  type: tarball
  url: https://example.com/source-${version}.tar.gz
  hash: sha256:abc123def456...
  mirrors:
    - https://mirror1.example.com/source-${version}.tar.gz
```

### Git

```yaml
source:
  type: git
  url: https://github.com/example/repo.git
  version: abc123def456789...  # commit hash
  submodules: true  # optional, fetch submodules
  hash: sha256:...  # hash of resulting tarball (for mirror support)
```

### Local / None

```yaml
source:
  type: none  # package uses files from files/ directory only
```

## Variable Interpolation

Variables can be used in string values:

- `${name}` - Package name
- `${version}` - Package version
- `${release}` - Package release
- `${pkg_dir}` - Package directory path

```yaml
source:
  url: https://example.com/${name}-${version}.tar.gz
```

## Build Systems

### autotools (default)

```yaml
build:
  system: autotools
  autoreconf: true  # run autoreconf before configure
  configure_args:
    - --prefix=/usr
    - --disable-static
  make_args:
    - EXTRA_CFLAGS=-O2
```

### cmake

```yaml
build:
  system: cmake
  cmake_options:
    - -DBUILD_SHARED_LIBS=ON
    - -DCMAKE_BUILD_TYPE=Release
```

### meson

```yaml
build:
  system: meson
  meson_options:
    - -Dexamples=false
    - -Dtests=false
```

### make (plain Makefile)

```yaml
build:
  system: make
  make_args:
    - PREFIX=/usr
    - CC=${CC}
```

### custom

```yaml
build:
  system: custom
  configure_script: |
    ./configure --host=${TARGET}
  compile_script: |
    make -j${JOBS}
  install_script: |
    make DESTDIR=${DESTDIR} install
```

## Dependencies

```yaml
dependencies:
  # Required at build time (headers, pkg-config files)
  build:
    - zlib
    - openssl
  
  # Required at runtime
  runtime:
    - libc
    - libz
```

## Install Files

```yaml
install:
  files:
    # Simple glob pattern
    - src: usr/bin/example
      dst: /usr/bin/
    
    # Wildcard
    - src: usr/lib/*.so*
      dst: /usr/lib/
    
    # Rename file
    - src: usr/bin/example
      dst: /usr/bin/my-example
  
  symlinks:
    - src: /usr/lib/libexample.so.1
      dst: /usr/lib/libexample.so
  
  # Copy from toolchain sysroot (for runtime libs)
  toolchain_libs:
    - libgcc_s.so.*
    - libstdc++.so.*
```

## Subpackages

Split a source package into multiple binary packages:

```yaml
name: openssl
version: 3.3.0

subpackages:
  libopenssl:
    description: OpenSSL shared library
    dependencies:
      runtime: [libc]
    install:
      files:
        - src: usr/lib/libssl.so*
          dst: /usr/lib/
        - src: usr/lib/libcrypto.so*
          dst: /usr/lib/
  
  openssl-util:
    description: OpenSSL command-line utility
    dependencies:
      runtime: [libopenssl]
    install:
      files:
        - src: usr/bin/openssl
          dst: /usr/bin/
  
  libopenssl-dev:
    description: OpenSSL development files
    dependencies:
      build: [libopenssl]
    install:
      files:
        - src: usr/include/openssl/*
          dst: /usr/include/openssl/
        - src: usr/lib/pkgconfig/*.pc
          dst: /usr/lib/pkgconfig/
```

## Variants

Build the same source with different configurations:

```yaml
name: ustream-ssl
version: 2024.01.01

variants:
  mbedtls:
    package_name: libustream-mbedtls
    default: true
    description: SSL stream library (mbedtls)
    dependencies:
      build: [libmbedtls]
      runtime: [libmbedtls]
    cmake_options:
      - -DBACKEND=mbedtls

  openssl:
    package_name: libustream-openssl
    description: SSL stream library (openssl)
    dependencies:
      build: [libopenssl]
      runtime: [libopenssl]
    cmake_options:
      - -DBACKEND=openssl
```

## Virtual Packages (Provides)

Declare that a package provides a virtual dependency:

```yaml
name: mbedtls
version: 3.6.0

provides:
  - libssl
default_variant: true  # preferred provider of libssl
```

Other packages can then depend on `libssl` and the resolver will
select an appropriate provider.

## Conflicts and Replaces

```yaml
name: new-package
version: 2.0.0

# Cannot be installed alongside these packages
conflicts:
  - old-package
  - incompatible-package

# Can replace files from these packages
replaces:
  - legacy-package
```

## User/Group Creation

```yaml
# Create user and group at install time
userid:
  - dnsmasq=453:dnsmasq=453
  - :nogroup=65534  # group only
```

## Install Scripts

```yaml
scripts:
  postinst: |
    #!/bin/sh
    /etc/init.d/example enable
    /etc/init.d/example start
  
  prerm: |
    #!/bin/sh
    /etc/init.d/example stop
    /etc/init.d/example disable
```

## Kernel Modules

Kernel module packages have special handling:

```yaml
name: kmod-usb-core
version: ${KERNEL_VERSION}

kernel:
  modules:
    - usb-common
    - usbcore
  kconfig:
    - USB_SUPPORT=y
    - USB=m
  autoload:
    priority: 10
    boot: true  # load at boot

dependencies:
  runtime:
    - kernel
```

## Complete Example

```yaml
name: curl
version: 8.5.0
release: 1
license: MIT

metadata:
  title: cURL
  description: Command line tool and library for transferring data with URLs
  section: net
  maintainer: OpenWrt Developers

source:
  type: tarball
  url: https://curl.se/download/curl-${version}.tar.xz
  hash: sha256:42ab8db9e20d8290a3b633e7fbb3cec15db34df65fd1015ef8a1c4f82be6b3fc

dependencies:
  build:
    - zlib
    - openssl
    - nghttp2
  runtime:
    - libcurl

build:
  system: autotools
  configure_args:
    - --with-openssl
    - --with-zlib
    - --with-nghttp2
    - --disable-manual
    - --disable-verbose

subpackages:
  libcurl:
    description: cURL library
    dependencies:
      runtime:
        - libc
        - libz
        - libopenssl
    install:
      files:
        - src: usr/lib/libcurl.so*
          dst: /usr/lib/

  curl:
    description: cURL command-line tool
    dependencies:
      runtime:
        - libcurl
    install:
      files:
        - src: usr/bin/curl
          dst: /usr/bin/
```
