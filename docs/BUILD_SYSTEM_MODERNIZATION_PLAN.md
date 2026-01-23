# OpenWrt Build System Modernization Plan

## Executive Summary

This document proposes a complete redesign of the OpenWrt build system to address fundamental performance, complexity, and maintainability issues. The new system will leverage modern tooling (Nix/Docker for environments, Ninja for builds, declarative package definitions) while maintaining full cross-compilation capabilities and SDK generation.

**Goals:**
- 10x faster clean builds through pre-built toolchains and aggressive caching
- Simpler, declarative package definitions (YAML/TOML instead of Make macros)
- Full reproducibility via Nix or Docker-based build environments
- Maintained backward compatibility with existing package sources
- SDK for individual package development

---

## Part 1: Current System Analysis

### Architecture Overview

The current OpenWrt build system is based on Buildroot with extensive custom modifications. It uses recursive Make with a complex macro system spanning ~50 include files.

**Build Pipeline:**
```
tools/ → toolchain/ → target/linux → packages/ → images/
(host)   (cross-cc)    (kernel)      (userspace)  (firmware)
```

### Critical Pain Points

| Issue | Impact | Root Cause |
|-------|--------|------------|
| **Toolchain from source** | 20-60 min per arch | 7-stage GCC bootstrap with no caching |
| **Package scanning** | 30-60 sec before config | 218+ Makefiles evaluated with DUMP=1, forced -j1 |
| **No cross-build caching** | Full rebuild each time | Stamp files are session-local only |
| **Complex Make macros** | Hard to understand/modify | 10+ levels of macro nesting, implicit dependencies |
| **Poor parallelism** | Underutilized cores | Many forced -j1 sections, sequential phases |
| **Metadata in Makefiles** | Expensive to parse | Perl scripts parse Make syntax at runtime |

### Current Resource Requirements

- **RAM:** 8GB minimum, 16GB+ recommended for parallel builds
- **Disk:** 15-30GB per target
- **Time:** 2-4 hours for first build, 30-60 min incremental
- **CPU:** Underutilized due to sequential phases

---

## Part 2: Proposed Architecture

### Design Principles

1. **Pre-built toolchains** - Never compile GCC/glibc from source
2. **Declarative metadata** - Package definitions in YAML, not Make macros
3. **Content-addressable caching** - Hash-based package artifact storage
4. **Parallel by default** - Ninja for build orchestration
5. **Reproducible environments** - Docker or Nix for build isolation
6. **Incremental everything** - Fine-grained dependency tracking

### New System Components

```
┌─────────────────────────────────────────────────────────────────────┐
│                         owrt-build CLI                               │
│            (Python/Rust orchestrator - replaces make)               │
└─────────────────────────────────────────────────────────────────────┘
        │               │                │                │
        ▼               ▼                ▼                ▼
┌───────────────┐ ┌───────────────┐ ┌───────────────┐ ┌───────────────┐
│  Environment  │ │   Resolver    │ │    Builder    │ │    Imager     │
│   Manager     │ │               │ │               │ │               │
│ (Docker/Nix)  │ │ (Dependency   │ │ (Ninja-based  │ │ (Firmware     │
│               │ │  resolution)  │ │  compilation) │ │  assembly)    │
└───────────────┘ └───────────────┘ └───────────────┘ └───────────────┘
        │               │                │                │
        ▼               ▼                ▼                ▼
┌─────────────────────────────────────────────────────────────────────┐
│                        Cache Layer                                   │
│  (Content-addressable storage for toolchains, packages, images)     │
└─────────────────────────────────────────────────────────────────────┘
```

### Component Details

#### 1. Environment Manager

Provides reproducible build environments with pre-built toolchains.

**Option A: Docker-based (Recommended for PoC)**
```dockerfile
# owrt-toolchain-arm-musl:14.2.0
FROM ubuntu:24.04
RUN apt-get update && apt-get install -y \
    build-essential ninja-build python3 \
    crossbuild-essential-armhf

# Pre-built musl toolchain
COPY --from=muslcc/arm-linux-musleabihf /toolchain /opt/toolchain
ENV PATH="/opt/toolchain/bin:$PATH"
```

**Option B: Nix-based (Full reproducibility)**
```nix
{
  crossPkgs = import nixpkgs {
    crossSystem = {
      config = "arm-unknown-linux-musleabihf";
      libc = "musl";
    };
  };

  buildEnv = crossPkgs.mkShell {
    nativeBuildInputs = [ crossPkgs.stdenv.cc ninja python3 ];
  };
}
```

**Toolchain Sources:**
- [muslcc](https://musl.cc/) - Pre-built musl-based toolchains
- [bootlin](https://toolchains.bootlin.com/) - Various arch/libc combinations
- [nixpkgs pkgsCross](https://nix.dev/tutorials/cross-compilation.html) - Nix cross-compilation infrastructure

#### 2. Package Definitions (YAML Format)

Replace complex Makefile macros with declarative YAML:

**Current (Makefile):**
```makefile
include $(TOPDIR)/rules.mk

PKG_NAME:=dropbear
PKG_VERSION:=2024.86
PKG_RELEASE:=1
PKG_SOURCE:=$(PKG_NAME)-$(PKG_VERSION).tar.bz2
PKG_SOURCE_URL:=https://matt.ucc.asn.au/dropbear/releases/
PKG_HASH:=sha256:abc123...
PKG_LICENSE:=MIT
PKG_BUILD_PARALLEL:=1
PKG_CONFIG_DEPENDS:=CONFIG_DROPBEAR_CURVE25519

include $(INCLUDE_DIR)/package.mk

define Package/dropbear
  SECTION:=net
  CATEGORY:=Base system
  TITLE:=Small SSH server
  DEPENDS:=+libc +zlib
  PROVIDES:=ssh
endef

define Package/dropbear/config
  source "$(SOURCE)/Config.in"
endef

define Build/Configure
  $(call Build/Configure/Default,--disable-wtmp)
endef

define Package/dropbear/install
  $(INSTALL_DIR) $(1)/usr/sbin
  $(INSTALL_BIN) $(PKG_BUILD_DIR)/dropbear $(1)/usr/sbin/
endef

$(eval $(call BuildPackage,dropbear))
```

**Proposed (YAML):**
```yaml
# packages/dropbear/package.yaml
name: dropbear
version: "2024.86"
release: 1
license: MIT

source:
  url: "https://matt.ucc.asn.au/dropbear/releases/dropbear-${version}.tar.bz2"
  sha256: "abc123..."

metadata:
  section: net
  category: Base system
  title: Small SSH server
  provides: [ssh]

dependencies:
  runtime: [libc, zlib]
  build: []

config:
  options:
    - name: DROPBEAR_CURVE25519
      type: bool
      default: true
      description: "Enable Curve25519 support"

build:
  system: autotools  # or cmake, meson, custom
  parallel: true
  configure_args:
    - "--disable-wtmp"
    - "--enable-curve25519=${config.DROPBEAR_CURVE25519}"

install:
  files:
    - src: "${build_dir}/dropbear"
      dst: "/usr/sbin/dropbear"
      mode: 0755
```

**Benefits:**
- Parseable in milliseconds (vs seconds for Make evaluation)
- IDE support, validation, autocomplete
- No hidden macro expansions
- Easy to generate, transform, migrate

#### 3. Dependency Resolver

Fast, pre-computed dependency resolution.

```python
# Pseudocode for resolver
class PackageResolver:
    def __init__(self, package_index: dict):
        self.index = package_index  # Loaded from YAML at startup
        self.cache = DependencyCache()

    def resolve(self, packages: list[str], target: Target) -> BuildPlan:
        """Resolve all dependencies and return ordered build plan."""
        graph = self._build_dependency_graph(packages)
        ordered = topological_sort(graph)

        # Check cache for pre-built packages
        to_build = []
        for pkg in ordered:
            cache_key = self._compute_hash(pkg, target)
            if not self.cache.has(cache_key):
                to_build.append(pkg)

        return BuildPlan(to_build, self.cache)
```

**Key improvements:**
- O(1) package lookup (vs O(n) Makefile scanning)
- Cached dependency graphs
- Parallel-safe resolution

#### 4. Ninja-based Builder

Generate Ninja build files for maximum parallelism.

```python
def generate_ninja(build_plan: BuildPlan, target: Target) -> str:
    """Generate build.ninja for the given plan."""
    lines = [
        f"# Auto-generated for {target.name}",
        f"builddir = build/{target.name}",
        "",
        "rule compile_package",
        "  command = owrt-build compile $pkg --target $target",
        "  description = Building $pkg",
        "",
    ]

    for pkg in build_plan.packages:
        deps = " ".join(f"$builddir/{d}.stamp" for d in pkg.depends)
        lines.append(f"build $builddir/{pkg.name}.stamp: compile_package | {deps}")
        lines.append(f"  pkg = {pkg.name}")
        lines.append(f"  target = {target.name}")

    return "\n".join(lines)
```

**Parallelism gains:**
- Ninja's superior job scheduling
- No forced -j1 phases
- Fine-grained file-level dependencies

#### 5. Content-Addressable Cache

Hash-based caching for all build artifacts.

```
cache/
├── toolchains/
│   └── arm-musl-14.2.0-abc123.tar.zst
├── packages/
│   ├── dropbear-2024.86-arm-musl-def456.ipk
│   └── zlib-1.3-arm-musl-789abc.ipk
└── images/
    └── openwrt-arm-generic-squashfs-xyz789.img
```

**Cache key computation:**
```python
def package_cache_key(pkg: Package, target: Target) -> str:
    """Compute deterministic cache key for a package build."""
    h = hashlib.sha256()
    h.update(pkg.name.encode())
    h.update(pkg.version.encode())
    h.update(pkg.source_hash.encode())
    h.update(target.toolchain_hash.encode())
    for dep in sorted(pkg.depends):
        h.update(package_cache_key(dep, target).encode())
    h.update(json.dumps(pkg.build_config, sort_keys=True).encode())
    return h.hexdigest()[:16]
```

**Cache backends:**
- Local filesystem (default)
- S3/GCS for CI sharing
- HTTP for public read-only mirrors

#### 6. Image Generator

Simplified firmware image assembly.

```yaml
# targets/arm-generic/target.yaml
name: arm-generic
arch: arm
variant: generic

kernel:
  version: "6.12"
  config: config-6.12
  patches: patches-6.12/

defaults:
  packages:
    - base-files
    - dropbear
    - dnsmasq

  filesystem: squashfs

devices:
  - name: generic-initramfs
    images: [initramfs.bin]
    rootfs: initramfs

  - name: generic-ext4
    images: [combined.img, combined.img.gz]
    rootfs: ext4
    partitions:
      boot: 16M
      root: 256M
```

---

## Part 3: Proof of Concept Scope

### Target Selection: ARM (arm-generic)

**Rationale:**
- Simpler than x86 (no GRUB/BIOS complexity)
- Widely used architecture
- Pre-built toolchains readily available
- Fast kernel compilation

### PoC Deliverables

1. **Build Environment**
   - Docker image with pre-built ARM musl toolchain
   - Nix flake as alternative

2. **Package System**
   - YAML package definitions for ~10 core packages
   - Dependency resolver
   - Ninja build file generator
   - Cache integration

3. **Core Packages** (minimal bootable system)
   - `base-files` - System structure
   - `busybox` - Core utilities
   - `musl` - C library (from toolchain)
   - `linux` - Kernel
   - `dropbear` - SSH (optional)

4. **Image Generation**
   - initramfs image
   - ext4 rootfs image

5. **SDK**
   - Standalone package for external package building
   - Uses same Docker/Nix environment
   - Includes package templates

### Success Criteria

| Metric | Current | PoC Target |
|--------|---------|------------|
| Clean build time | 2-4 hours | 10-15 min |
| Config/scan time | 30-60 sec | <2 sec |
| Incremental rebuild | 10-30 min | 1-2 min |
| RAM required | 16GB | 4GB |
| Disk space | 20GB | 5GB |

---

## Part 4: Implementation Plan

### Phase 1: Foundation (Weeks 1-2)

#### 1.1 Build Environment Setup

```
poc/
├── docker/
│   ├── Dockerfile.toolchain-arm-musl
│   └── docker-compose.yml
├── nix/
│   └── flake.nix
└── toolchain/
    └── verify-toolchain.sh
```

**Tasks:**
- [ ] Create Docker image with ARM musl toolchain from musl.cc
- [ ] Create Nix flake with equivalent cross-compilation setup
- [ ] Add toolchain verification tests
- [ ] Document environment setup

#### 1.2 Package Definition Schema

```
poc/
├── schema/
│   └── package.schema.json
└── packages/
    ├── base-files/
    │   └── package.yaml
    ├── busybox/
    │   └── package.yaml
    └── ...
```

**Tasks:**
- [ ] Define JSON Schema for package.yaml
- [ ] Create YAML definitions for core packages
- [ ] Build schema validator
- [ ] Create migration guide from Makefile

### Phase 2: Build System Core (Weeks 3-4)

#### 2.1 CLI Tool (`owrt-build`)

```python
# owrt-build/cli.py
import click

@click.group()
def cli():
    """OpenWrt Modern Build System"""
    pass

@cli.command()
@click.argument('target')
@click.option('--profile', default='generic')
def build(target: str, profile: str):
    """Build firmware for TARGET."""
    env = EnvironmentManager().get_or_create(target)
    packages = load_target_packages(target, profile)
    plan = Resolver().resolve(packages)
    ninja_file = generate_ninja(plan)
    run_ninja(ninja_file, env)

@cli.command()
@click.argument('package')
def compile(package: str):
    """Compile a single package (called by Ninja)."""
    pkg = load_package(package)
    build_package(pkg)
```

**Tasks:**
- [ ] Implement package YAML loader
- [ ] Implement dependency resolver
- [ ] Implement Ninja file generator
- [ ] Implement package compiler (autotools/cmake support)
- [ ] Add cache layer

#### 2.2 Kernel Build Integration

**Tasks:**
- [ ] Create kernel package definition
- [ ] Integrate kernel config/patches from OpenWrt
- [ ] Cross-compile kernel with pre-built toolchain
- [ ] Generate kernel modules

### Phase 3: Image Generation (Week 5)

#### 3.1 Root Filesystem Assembly

**Tasks:**
- [ ] Implement package installation to staging directory
- [ ] Create squashfs/ext4 image generation
- [ ] Implement initramfs bundling
- [ ] Add metadata/manifest generation

#### 3.2 Device Image Creation

**Tasks:**
- [ ] Implement device profile system
- [ ] Create image assembly for arm-generic
- [ ] Add image checksums and signing

### Phase 4: SDK & Documentation (Week 6)

#### 4.1 SDK Generation

```
sdk/
├── Dockerfile                 # Build environment
├── owrt-build                 # CLI tool
├── packages/                  # Package template
│   └── template/
│       └── package.yaml
├── staging/                   # Headers/libraries
└── README.md
```

**Tasks:**
- [ ] Create SDK container/tarball generation
- [ ] Include toolchain and build tools
- [ ] Add package development templates
- [ ] Document SDK usage

#### 4.2 Documentation

**Tasks:**
- [ ] Architecture documentation
- [ ] Package authoring guide
- [ ] Migration guide from current system
- [ ] API reference

---

## Part 5: Technical Specifications

### Package YAML Schema

```yaml
# JSON Schema for package.yaml (simplified)
$schema: https://json-schema.org/draft/2020-12/schema
type: object
required: [name, version, source, metadata, build]
properties:
  name:
    type: string
    pattern: "^[a-z0-9-]+$"
  version:
    type: string
  release:
    type: integer
    default: 1
  license:
    type: string
  source:
    type: object
    properties:
      url: { type: string, format: uri }
      sha256: { type: string, pattern: "^[a-f0-9]{64}$" }
      git: { type: string }
      ref: { type: string }
  metadata:
    type: object
    properties:
      section: { type: string }
      category: { type: string }
      title: { type: string }
      description: { type: string }
      provides: { type: array, items: { type: string } }
  dependencies:
    type: object
    properties:
      runtime: { type: array, items: { type: string } }
      build: { type: array, items: { type: string } }
  build:
    type: object
    properties:
      system: { enum: [autotools, cmake, meson, make, custom] }
      parallel: { type: boolean, default: true }
      configure_args: { type: array, items: { type: string } }
      make_args: { type: array, items: { type: string } }
      script: { type: string }  # For custom builds
  install:
    type: object
    properties:
      files: { type: array }
      script: { type: string }
```

### Target Definition Schema

```yaml
# Target definition
name: arm-generic
arch: arm
endian: little
cpu: cortex-a7
fpu: neon-vfpv4

toolchain:
  libc: musl
  gcc: "14.2"
  source: "docker://owrt/toolchain-arm-musl:14.2"
  # or: source: "nix:pkgsCross.armv7l-hf-multiplatform"

kernel:
  version: "6.12"
  config: config-6.12
  patches:
    - patches-6.12/*.patch
  modules:
    - fs-ext4
    - net-usb

defaults:
  packages:
    - base-files
    - busybox
    - dropbear
  features:
    - squashfs
    - ext4
```

### Cache Key Algorithm

```python
def compute_cache_key(pkg: Package, target: Target, deps: dict[str, str]) -> str:
    """
    Compute deterministic cache key.

    Inputs:
    - Package source hash (sha256 of source tarball/git commit)
    - Package definition hash (sha256 of package.yaml)
    - Toolchain identifier (name + version + hash)
    - Dependency cache keys (recursive)
    - Build configuration hash
    """
    components = [
        f"pkg:{pkg.name}@{pkg.version}",
        f"src:{pkg.source_hash}",
        f"def:{hash_file(pkg.definition_path)}",
        f"tc:{target.toolchain_id}",
        f"deps:{hash_dict(deps)}",
        f"cfg:{hash_dict(pkg.resolved_config)}",
    ]
    return sha256("|".join(components))[:20]
```

---

## Part 6: Migration Strategy

### Parallel Operation

The new build system can coexist with the existing system:

```
openwrt/
├── Makefile              # Existing system
├── include/              # Existing system
├── package/              # Existing packages (Makefile-based)
│
├── owrt-build/           # New system CLI
├── poc/                  # Proof of concept
│   ├── packages/         # YAML package definitions
│   └── targets/          # YAML target definitions
└── docs/
    └── BUILD_SYSTEM_MODERNIZATION_PLAN.md
```

### Package Migration Tool

```python
def migrate_makefile_to_yaml(makefile_path: str) -> dict:
    """
    Parse OpenWrt package Makefile and generate YAML.

    Extracts:
    - PKG_NAME, PKG_VERSION, PKG_SOURCE, PKG_HASH
    - Package/*/DEPENDS, TITLE, SECTION, CATEGORY
    - Build/Configure, Build/Compile commands
    - Package/*/install commands
    """
    # Parse Makefile with regex/AST
    # Generate equivalent YAML
    pass
```

### Gradual Rollout

1. **Phase A:** PoC with 10 packages, single target
2. **Phase B:** Expand to 50 packages, 3 targets
3. **Phase C:** Migration tools, parallel CI
4. **Phase D:** Full migration, deprecate old system

---

## Part 7: Risks and Mitigations

| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| Pre-built toolchain incompatibility | High | Medium | Test extensively, maintain escape hatch to build toolchain |
| YAML migration misses edge cases | Medium | High | Extensive testing, keep Makefile parser for complex packages |
| Performance regression | High | Low | Benchmark continuously, profile hotspots |
| Community adoption resistance | Medium | Medium | Clear migration path, maintain compatibility layer |
| Nix learning curve | Low | Medium | Provide Docker alternative, documentation |

---

## Part 8: References

### Modern Build Systems
- [Nix Cross Compilation](https://nix.dev/tutorials/cross-compilation.html)
- [NixOS Cross-Compiling Images](https://nixcademy.com/posts/cross-compile-nixos-images/)
- [Repeatable Cross-GCC Toolchain Builds with Nix](https://twosixtech.com/blog/repeatable-cross-gcc-toolchain-builds-with-nix/)
- [Using Nix as a Yocto Alternative](https://www.kdab.com/using-nix-as-a-yocto-alternative/)

### Pre-built Toolchains
- [musl.cc](https://musl.cc/) - Pre-built musl toolchains
- [Bootlin Toolchains](https://toolchains.bootlin.com/)
- [Linaro Toolchains](https://www.linaro.org/downloads/)

### Build System Comparisons
- [Yocto vs Buildroot](https://www.incredibuild.com/blog/yocto-or-buildroot-which-to-use-when-building-your-custom-embedded-systems)
- [Embedded Build Systems Comparison](https://www.mm-software.com/en/more-the-newsroom/detail/yocto-buildroot-ptxdist-elbe/)

---

## Appendix A: Directory Structure (PoC)

```
poc/
├── owrt-build/                    # Main CLI tool
│   ├── __init__.py
│   ├── cli.py                     # Click-based CLI
│   ├── environment.py             # Docker/Nix environment management
│   ├── resolver.py                # Dependency resolution
│   ├── builder.py                 # Package compilation
│   ├── ninja_gen.py               # Ninja file generation
│   ├── cache.py                   # Content-addressable cache
│   └── image.py                   # Image generation
│
├── docker/
│   ├── Dockerfile.toolchain-arm-musl
│   ├── Dockerfile.build-env
│   └── docker-compose.yml
│
├── nix/
│   ├── flake.nix
│   ├── flake.lock
│   └── toolchain.nix
│
├── schema/
│   ├── package.schema.json
│   └── target.schema.json
│
├── packages/
│   ├── base-files/
│   │   ├── package.yaml
│   │   └── files/                 # Root filesystem overlay
│   ├── busybox/
│   │   └── package.yaml
│   ├── linux/
│   │   ├── package.yaml
│   │   └── config-6.12
│   ├── dropbear/
│   │   └── package.yaml
│   └── ...
│
├── targets/
│   └── arm-generic/
│       ├── target.yaml
│       ├── config-6.12
│       └── patches-6.12/
│
├── build/                         # Build output (gitignored)
│   ├── arm-generic/
│   │   ├── staging/
│   │   ├── packages/
│   │   └── images/
│   └── cache/
│
└── sdk/                           # Generated SDK
    └── ...
```

---

## Appendix B: Example Build Session

```bash
# Setup environment (one-time)
$ cd openwrt/poc
$ docker compose build toolchain-arm-musl

# Configure target
$ ./owrt-build config arm-generic --profile minimal
Loading target: arm-generic (ARM Cortex-A7, musl)
Selected packages: base-files, busybox, linux
Configuration saved to: build/arm-generic/.config

# Build
$ ./owrt-build build arm-generic
[1/3] Resolving dependencies...
  → base-files (no deps)
  → busybox (depends: libc)
  → linux (no deps)
[2/3] Generating build plan...
  → 3 packages to build (0 cached)
  → Generated: build/arm-generic/build.ninja
[3/3] Building...
[1/3] Compiling linux...
[2/3] Compiling base-files...
[3/3] Compiling busybox...
Build complete in 8m 32s

# Generate image
$ ./owrt-build image arm-generic --type initramfs
Generating initramfs image...
  → Installing packages to staging...
  → Creating initramfs...
Output: build/arm-generic/images/openwrt-arm-generic-initramfs.bin (4.2MB)

# SDK generation
$ ./owrt-build sdk arm-generic
Generating SDK...
  → Packaging toolchain...
  → Including staging headers/libs...
  → Adding build tools...
Output: build/arm-generic/sdk/openwrt-sdk-arm-generic.tar.zst (180MB)
```

---

## Conclusion

This plan outlines a complete modernization of the OpenWrt build system that addresses the fundamental issues of complexity, performance, and maintainability. The proof of concept will demonstrate:

1. **10x build speed improvement** through pre-built toolchains and caching
2. **Simpler package definitions** using YAML instead of Make macros
3. **Full reproducibility** via Docker/Nix environments
4. **Modern tooling** with Ninja builds and content-addressable caching
5. **SDK support** for external package development

The phased implementation allows for validation of the approach before committing to a full migration, while the parallel operation strategy ensures the existing system remains functional throughout the transition.
