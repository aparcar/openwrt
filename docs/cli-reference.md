# CLI Reference

The `owrt` command-line interface provides all build system functionality.

## Global Options

```bash
python3 -m owrt [OPTIONS] COMMAND [ARGS]
```

| Option | Description |
|--------|-------------|
| `-v, --verbose` | Enable verbose output |
| `-j, --jobs N` | Number of parallel jobs (default: CPU count) |
| `--ccache/--no-ccache` | Use ccache for compilation |
| `--docker/--no-docker` | Force Docker mode on/off (auto-detected) |

## Build Commands

### build

Build complete firmware for a target.

```bash
python3 -m owrt build TARGET [OPTIONS]
```

| Option | Description |
|--------|-------------|
| `-p, --profile NAME` | Device profile (default: first available) |
| `-A, --all-profiles` | Build all profiles for this target |
| `-P, --packages PKG` | Additional packages (can be repeated) |
| `-a, --all-packages` | Build all available packages (buildbot mode) |
| `-c, --continue-on-error` | Continue on package failure |
| `-f, --force` | Force rebuild |

Examples:
```bash
# Build default profile
python3 -m owrt build armsr-armv8

# Build specific profile
python3 -m owrt build mediatek-filogic -p bananapi_bpi-r3

# Build all profiles
python3 -m owrt build armsr-armv8 -A

# Build with extra packages
python3 -m owrt build armsr-armv8 -P luci -P tcpdump
```

### firmware

Build firmware using `config.yaml`.

```bash
python3 -m owrt firmware [OPTIONS]
```

| Option | Description |
|--------|-------------|
| `-c, --config-file PATH` | Path to config.yaml |
| `-f, --force` | Force rebuild |

Example:
```bash
# Create config
python3 -m owrt config init armsr-armv8 -o config.yaml

# Edit config.yaml...

# Build
python3 -m owrt firmware
```

### package

Build a single package.

```bash
python3 -m owrt package TARGET PACKAGE [OPTIONS]
```

| Option | Description |
|--------|-------------|
| `-f, --force` | Force rebuild |
| `--no-apk` | Skip APK creation |

Example:
```bash
python3 -m owrt package armsr-armv8 busybox
python3 -m owrt package armsr-armv8 curl -f
```

### image

Generate firmware images.

```bash
python3 -m owrt image TARGET [OPTIONS]
```

| Option | Description |
|--------|-------------|
| `-p, --profile NAME` | Device profile |

## Toolchain Commands

### toolchain build

Build the cross-compilation toolchain.

```bash
python3 -m owrt toolchain build TARGET [OPTIONS]
```

| Option | Description |
|--------|-------------|
| `-f, --force` | Force rebuild |
| `-n, --dry-run` | Show build plan only |

Example:
```bash
python3 -m owrt toolchain build armsr-armv8
python3 -m owrt toolchain build mediatek-filogic -v
```

### toolchain info

Show toolchain configuration.

```bash
python3 -m owrt toolchain info TARGET
```

### toolchain hash

Compute toolchain cache key.

```bash
python3 -m owrt toolchain hash TARGET [OPTIONS]
```

| Option | Description |
|--------|-------------|
| `-f, --format FMT` | Output format: tag, hash, json |

### toolchain clean

Clean toolchain build artifacts.

```bash
python3 -m owrt toolchain clean TARGET
```

## Kernel Commands

### kernel build

Build the Linux kernel.

```bash
python3 -m owrt kernel build TARGET [OPTIONS]
```

| Option | Description |
|--------|-------------|
| `-f, --force` | Force rebuild |

### kernel menuconfig

Run kernel configuration menu.

```bash
python3 -m owrt kernel menuconfig TARGET
```

### kernel dtbs

Build Device Tree Blobs.

```bash
python3 -m owrt kernel dtbs TARGET
```

### kernel modules

Package kernel modules as APKs.

```bash
python3 -m owrt kernel modules TARGET
```

## Host Tool Commands

### tool list

List available host tools.

```bash
python3 -m owrt tool list
```

### tool info

Show tool information.

```bash
python3 -m owrt tool info TOOL_NAME
```

### tool build

Build a specific host tool.

```bash
python3 -m owrt tool build TOOL_NAME [OPTIONS]
```

| Option | Description |
|--------|-------------|
| `-f, --force` | Force rebuild |
| `-b, --build-dir PATH` | Build directory |

### tool build-all

Build all host tools.

```bash
python3 -m owrt tool build-all [OPTIONS]
```

| Option | Description |
|--------|-------------|
| `-f, --force` | Force rebuild |
| `-b, --build-dir PATH` | Build directory |

## Ninja Commands

For parallel builds using Ninja.

### ninja generate

Generate Ninja build file.

```bash
python3 -m owrt ninja generate TARGET [OPTIONS]
```

| Option | Description |
|--------|-------------|
| `-p, --profile NAME` | Device profile |
| `-P, --packages PKG` | Additional packages |

### ninja run

Run Ninja build.

```bash
python3 -m owrt ninja run TARGET [OPTIONS]
```

| Option | Description |
|--------|-------------|
| `-p, --profile NAME` | Device profile |
| `-P, --packages PKG` | Additional packages |
| `-t, --targets NAME` | Specific Ninja targets |
| `-f, --force` | Regenerate Ninja file |

### ninja plan

Show build plan (dry-run).

```bash
python3 -m owrt ninja plan TARGET [OPTIONS]
```

### ninja graph

Generate dependency graph.

```bash
python3 -m owrt ninja graph TARGET [OPTIONS]
```

| Option | Description |
|--------|-------------|
| `-o, --output PATH` | Output DOT file |

## Configuration Commands

### config init

Initialize a new config.yaml.

```bash
python3 -m owrt config init TARGET [OPTIONS]
```

| Option | Description |
|--------|-------------|
| `-p, --profile NAME` | Device profile |
| `-o, --output PATH` | Output file |

### config show

Show current configuration.

```bash
python3 -m owrt config show [OPTIONS]
```

| Option | Description |
|--------|-------------|
| `-c, --config-file PATH` | Path to config.yaml |

## Test Commands

### test qemu

Run QEMU-based smoke tests.

```bash
python3 -m owrt test qemu TARGET [OPTIONS]
```

| Option | Description |
|--------|-------------|
| `-f, --firmware PATH` | Path to firmware image (required) |
| `-t, --timeout SECS` | Boot timeout (default: 180) |
| `--json` | Output results as JSON |

Example:
```bash
python3 -m owrt test qemu armsr-armv8 \
    -f build/output/images/openwrt-*-initramfs-kernel.bin
```

### test list-targets

List supported QEMU test targets.

```bash
python3 -m owrt test list-targets
```

### test run

Run tests on firmware.

```bash
python3 -m owrt test run TARGET [OPTIONS]
```

| Option | Description |
|--------|-------------|
| `-f, --firmware PATH` | Firmware path (auto-detected) |
| `-d, --tests-dir PATH` | openwrt-tests directory |
| `--full` | Run full test suite |

## Utility Commands

### info

Show target information.

```bash
python3 -m owrt info TARGET
```

### download

Download package sources.

```bash
python3 -m owrt download TARGET [OPTIONS]
```

| Option | Description |
|--------|-------------|
| `-p, --package NAME` | Download specific package only |
| `-j, --jobs N` | Parallel downloads (default: 16) |

### apk-index

Generate APK repository index.

```bash
python3 -m owrt apk-index TARGET
```

### clean

Clean build artifacts.

```bash
python3 -m owrt clean TARGET [OPTIONS]
```

| Option | Description |
|--------|-------------|
| `--all` | Also clean toolchain |

## Environment Variables

| Variable | Description |
|----------|-------------|
| `BUILD_DIR` | Build output directory (default: ./build) |
| `DL_DIR` | Download cache directory (default: ./dl) |
| `OUTPUT_DIR` | Output directory for images |
| `OPENWRT_DIR` | OpenWrt source directory |

## Exit Codes

| Code | Description |
|------|-------------|
| 0 | Success |
| 1 | General error |
| 2 | Invalid arguments |
