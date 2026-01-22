# Package Variant System Design

## Overview

Many OpenWrt packages can be built with different backends (e.g., SSL libraries).
This document describes how variants are supported in the YAML package format.

## OpenWrt Makefile Pattern

In OpenWrt Makefiles, variants are defined like:

```makefile
define Package/libustream-openssl
  VARIANT:=openssl
  DEPENDS += +libopenssl
endef

define Package/libustream-mbedtls
  VARIANT:=mbedtls
  DEFAULT_VARIANT:=1
  DEPENDS += +libmbedtls
  CONFLICTS := libustream-openssl
endef

ifeq ($(BUILD_VARIANT),mbedtls)
  CMAKE_OPTIONS += -DMBEDTLS=on
endif
```

Key concepts:
- Each variant produces a **different package name** (e.g., `apk-mbedtls`, `apk-openssl`)
- All variants share the same source code
- `DEFAULT_VARIANT:=1` marks which variant is built by default
- Variants often conflict with each other

## YAML Schema for Variants

### Option 1: Variants as subpackages with variant-specific config

```yaml
name: ustream-ssl
version: "2025.10.03"

source:
  type: git
  url: "https://git.openwrt.org/project/ustream-ssl.git"
  version: "5a81c108d20e24724ed847cc4be033f2a74e6635"

# Base dependencies shared by all variants
dependencies:
  runtime:
    - libubox
  build:
    - libubox-dev

build:
  system: cmake
  parallel: true

# Variants define different build configurations
variants:
  mbedtls:
    default: true
    package_name: libustream-mbedtls
    dependencies:
      runtime:
        - libmbedtls
      build:
        - libmbedtls-dev
    conflicts:
      - libustream-openssl
      - libustream-wolfssl
    configure_args:
      - "-DMBEDTLS=on"

  openssl:
    package_name: libustream-openssl
    dependencies:
      runtime:
        - libopenssl
      build:
        - libopenssl-dev
    conflicts:
      - libustream-mbedtls
      - libustream-wolfssl
    # No extra configure args - openssl is the default in cmake

  wolfssl:
    package_name: libustream-wolfssl
    dependencies:
      runtime:
        - libwolfssl
      build:
        - libwolfssl-dev
    conflicts:
      - libustream-mbedtls
      - libustream-openssl
    configure_args:
      - "-DWOLFSSL=on"
    cflags:
      - "-I${STAGING_DIR}/usr/include/wolfssl"

# Install is the same for all variants
install:
  files:
    - src: "usr/lib/libustream-ssl.so"
      dst: "/lib/libustream-ssl.so"
```

### Option 2: Separate YAML files per variant

Keep each variant as a separate package.yaml, with a `variant_of` field:

```yaml
# package/libs/ustream-ssl-mbedtls/package.yaml
name: libustream-mbedtls
variant_of: ustream-ssl  # Points to base source package
version: "2025.10.03"
default_variant: true

dependencies:
  runtime:
    - libubox
    - libmbedtls
conflicts:
  - libustream-openssl
  - libustream-wolfssl

build:
  configure_args:
    - "-DMBEDTLS=on"
```

## Recommendation

**Option 1** (variants in single YAML) is cleaner because:
- All variant info is in one place
- Easier to see all options
- Avoids duplication of source info
- Matches how the Makefile organizes things

## Implementation Steps

1. Add `variants` field to PackageConfig in `config.py`
2. When building, select variant based on user config or default
3. Merge variant-specific deps/args with base package
4. Generate APK with variant's package_name
5. Add conflicts between variants automatically

## Build System Integration

When user requests a package like `libustream-mbedtls`:
1. Find the source package (`ustream-ssl`)
2. Look up the variant (`mbedtls`)
3. Merge base config with variant config
4. Build with variant-specific options
5. Create APK named `libustream-mbedtls`
