# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is **OpenWrt-ng PoC** - a proof-of-concept modern build system for OpenWrt that replaces the traditional Makefile-based system with:
- YAML-based package/target definitions
- Python CLI orchestration (Click-based)
- Ninja for parallel builds
- Docker for reproducible builds
- APK v3 packaging (Alpine format)

**Current target**: armsr-armv8 (ARM64 EFI)

## Important: Commit After Finishing Todos

After completing todos or significant features/fixes, **create a git commit** to checkpoint the work. This prevents losing progress and makes it easier to track what changed. Use the conventional commit style: `poc: <description>`.

## Important: Reference the Original Build System

When facing complex problems, **always check how the original OpenWrt build system handles it**. The original system is located at `/home/aparcar/openwrt-ng/` (parent directory).

Key reference locations:
- `../include/*.mk` - Core build infrastructure (package.mk, image.mk, kernel.mk, etc.)
- `../package/*/Makefile` - Package definitions (PKG_*, Build/, Install/)
- `../target/linux/*/` - Target definitions, kernel patches, DTS files
- `../tools/*/Makefile` - Host tool builds
- `../toolchain/*/Makefile` - Toolchain component builds
- `../scripts/` - Build helper scripts
- `../rules.mk` - Global build rules and variables

Commonly referenced files:
- `../include/package.mk` - Package build infrastructure
- `../include/package-ipkg.mk` - Package installation and APK/IPKG creation
- `../include/image.mk` - Image generation (rootfs, firmware)
- `../include/kernel.mk` - Kernel build rules
- `../target/linux/armsr/` - ARM SystemReady target reference

## Build Commands

### Using build.sh (Recommended)

The `build.sh` script handles Docker containerization automatically:

```bash
# Full build (toolchain + host tools + firmware)
./build.sh

# Individual steps
./build.sh toolchain          # Build cross-compiler
./build.sh tools              # Build host tools (apk, lua)
./build.sh firmware           # Build firmware (requires toolchain + tools)

# Interactive shell with toolchain in PATH
./build.sh shell

# Clean build artifacts
./build.sh clean stamps       # Clean stamps only (force rebuild)
./build.sh clean build        # Clean packages/kernel (keep toolchain)
./build.sh clean package libc # Clean specific package
./build.sh clean rootfs       # Clean rootfs and image stamps
./build.sh clean all          # Clean everything

# IMPORTANT: Always use build.sh to clean package stamps, not rm -rf directly
# Example: ./build.sh clean package libmnl

# Force rebuild a package after changing package.yaml:
# Combine clean and build in ONE command to avoid multiple prompts
./build.sh -t mediatek-filogic clean package hostapd && ./build.sh -t mediatek-filogic package hostapd

# Options
./build.sh -t armsr-armv8     # Specify target
./build.sh -p generic         # Specify profile
./build.sh -v                 # Verbose output
./build.sh -j 8               # Parallel jobs

# Show build info
./build.sh info
```

### Direct Python CLI (inside Docker or with local deps)

```bash
# Individual components
python3 -m owrt_build toolchain build armsr-armv8
python3 -m owrt_build kernel build armsr-armv8
python3 -m owrt_build package armsr-armv8 <package-name>

# Ninja parallel build (preferred for packages)
python3 -m owrt_build ninja generate armsr-armv8
python3 -m owrt_build ninja run armsr-armv8

# Host tools
python3 -m owrt_build tool build apk
python3 -m owrt_build tool build-all

# APK repository index
python3 -m owrt_build apk-index armsr-armv8

# Show info
python3 -m owrt_build info armsr-armv8
```

## Architecture

```
poc/
├── owrt_build/           # Python build system (~6000 lines)
│   ├── __main__.py       # CLI entry point
│   ├── config.py         # YAML config loading, path management
│   ├── toolchain.py      # Cross-compiler builder
│   ├── kernel.py         # Kernel builder with patches
│   ├── package.py        # Package builder (autotools/cmake/meson)
│   ├── apk.py            # APK v3 packaging and rootfs assembly
│   ├── image.py          # Firmware image generation
│   ├── resolver.py       # Dependency resolution
│   ├── ninja_gen.py      # Ninja build file generation
│   └── utils.py          # Shared utilities
├── packages/             # YAML package definitions
├── targets/              # YAML target definitions
├── tools/                # YAML host tool definitions
├── schema/               # JSON schemas for validation
├── docker/               # Dockerfile and compose
└── build/                # Build output (gitignored)
```

## Key Concepts

### Package YAML Format
```yaml
name: example
version: "1.0.0"
source:
  type: tarball|git|local
  url: "..."
  sha256: "..."
dependencies:
  runtime: [libc]
  build: [cmake]
build:
  system: autotools|cmake|meson|make|custom|none
  configure_args: [...]
subpackages:          # Multiple outputs from one source
  libexample:
    files:
      - src: "usr/lib/*.so*"
        dst: "/usr/lib/"
  libexample-dev:
    files:
      - src: "usr/include/*.h"
        dst: "/usr/include/"
```

### Subpackages
One source can produce multiple installable packages. The source is built once, then files are split into subpackages. Build dependencies should reference `-dev` packages.

### Profile YAML Format (in target.yaml)
```yaml
profiles:
  - name: my_device
    title: "My Device"
    dts: mt7981b-my-device           # Main device tree
    dts_dir: "${OPENWRT_DIR}/target/linux/mediatek/dts"
    dts_load_address: "0x43f00000"   # DTB load address

    # DTB overlays (optional) - for devices with multiple flash configs
    # Each overlay name corresponds to a .dtso file in dts_dir
    dts_overlay:
      - mt7981b-my-device-emmc       # For eMMC boot
      - mt7981b-my-device-nand       # For NAND boot
      - mt7981b-my-device-sd         # For SD card boot

    packages:
      - kmod-mt7915e
      - kmod-usb3
```

### Build Output Paths
```
build/
├── toolchain/armsr-armv8/      # Cross-compiler
├── host-staging/               # Host tools (apk, lua)
├── kernel/armsr-armv8/         # Kernel build
├── packages/armsr-armv8/       # Package builds
├── staging/armsr-armv8/        # Headers/libs for dependencies
├── apk-packages/armsr-armv8/   # Individual .apk files
├── apk-repo/armsr-armv8/       # Repository with APKINDEX
├── rootfs/armsr-armv8/         # Assembled rootfs
└── output/                     # Final images
```

## Change Detection

The build system uses content-addressable hashing to detect when packages need to be rebuilt.

### Package Change Detection (Implemented)
Packages are automatically rebuilt when any of these change:
- `package.yaml` content (version, dependencies, build args, etc.)
- Patch files in `packages/<name>/patches/`
- Files in `packages/<name>/files/`
- Version and release numbers

The implementation in `owrt_build/package.py`:
1. Computes SHA256 hash of all inputs
2. Stores hash in stamp file name: `.built_<hash>`, `.apk_<hash>`
3. Before building, checks if stamp with current hash exists
4. If hash differs or no stamp exists → cleans old build and rebuilds

```bash
# Example: stamps in build/packages/armsr-armv8/stamp/
base-files.built_4bd3e8408c4f
busybox.built_4246bcc50ec5
```

### Kernel (Not Yet Implemented)
Changes that should trigger kernel rebuild:
- Kernel version in target.yaml
- Config fragments in `targets/<target>/kernel/config/`
- Patches in `../target/linux/generic/` and `../target/linux/<target>/patches-*`
- DTS files
- Kernel files overlay

### Toolchain (Not Yet Implemented)
Changes that should trigger toolchain rebuild:
- GCC/binutils/musl versions
- Toolchain patches
- Target architecture settings

### Dependency Cascading (Not Yet Implemented)
Currently, if a dependency like libubox changes, packages depending on it are NOT automatically rebuilt. This requires tracking the hash of dependencies in each package's hash computation.

### OpenWrt Reference
OpenWrt's approach (`../include/package.mk` line 114):
```make
STAMP_PREPARED=$(PKG_BUILD_DIR)/.prepared_$(shell $(call find_md5,${CURDIR} $(PKG_FILE_DEPENDS),))
```

## Common Issues and Solutions

### Cross-compilation
- Always set `PKG_CONFIG_SYSROOT_DIR` for pkg-config to find headers
- Use staging directory for build dependencies
- Check `../include/package.mk` for how OpenWrt sets CFLAGS/LDFLAGS

### Patches
- Patch format: unified diff with `--- a/file` and `+++ b/file` headers
- Empty context lines MUST have a single space character (` `) not be empty
- Use `patch -p1` (strips `a/` and `b/` prefixes)

### APK v3
- Uses `apk mkpkg` to create packages (different from v2)
- Uses `apk extract` to view/extract package contents (not tar)
- Uses `apk mkndx` to create repository index (not `apk index`)
- Requires PKGINFO file with specific format
- See `apk.py` for metadata generation

```bash
# View APK contents
apk extract --list package.apk

# Extract APK to directory
apk extract --destination /tmp/out package.apk

# Create repository index
apk mkndx -o packages.adb *.apk
```

## Testing

```bash
# Test boot with QEMU (after building armsr-armv8)
# First decompress the image
gunzip -k output/images/combined.img.gz

# Boot with QEMU (EFI disk image)
qemu-system-aarch64 -M virt -cpu cortex-a57 -m 1G \
  -bios /usr/share/qemu-efi-aarch64/QEMU_EFI.fd \
  -drive file=output/images/combined.img,format=raw \
  -nographic

# Or use initramfs (no disk, boots faster for testing)
qemu-system-aarch64 -M virt -cpu cortex-a57 -m 1G \
  -kernel output/images/initramfs.bin \
  -nographic -append "console=ttyAMA0"
```

## Current Work

Check BUILD_SYSTEM_STATUS.md for detailed TODO list and known issues.
