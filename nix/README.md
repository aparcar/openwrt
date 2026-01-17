# OpenWrt Build Environment - Nix

This directory contains Nix configurations for creating reproducible OpenWrt
build environments, either as Docker containers or native development shells.

## Files

- `docker-image.nix` - Pure Nix-based Docker image (fully reproducible)
- `../flake.nix` - Nix flakes configuration (modern approach)
- `../shell.nix` - Traditional Nix shell for development
- `../openwrt-build-env.nix` - Ubuntu-based Docker image built with Nix
- `../Dockerfile` - Standard Dockerfile (no Nix required)

## Quick Start

### Option 1: Nix Flakes (Recommended)

```bash
# Build Docker image
nix build .#openwrt-docker-image
docker load < result
docker run -it --rm -v $(pwd):/openwrt openwrt-build-env

# Or enter development shell directly (no Docker)
nix develop
```

### Option 2: Traditional Nix

```bash
# Enter development shell
nix-shell

# Or build Docker image
nix-build openwrt-build-env.nix
docker load < result
docker run -it --rm -v $(pwd):/openwrt openwrt-build-env
```

### Option 3: Pure Nix Docker Image

```bash
nix-build nix/docker-image.nix
docker load < result
docker run -it --rm -v $(pwd):/openwrt openwrt-build-env-nix
```

### Option 4: Standard Docker (No Nix Required)

```bash
docker build -t openwrt-build-env .
docker run -it --rm -v $(pwd):/openwrt openwrt-build-env
```

## Tool Versions

The following tool versions are from the OpenWrt `tools/` folder. OpenWrt
builds these tools during compilation, but the host system tools are used
for bootstrapping:

| Tool | Version | Tool | Version |
|------|---------|------|---------|
| 7z | 25.01 | autoconf | 2.72 |
| automake | 1.18.1 | bash | 5.3 |
| bc | 1.08.1 | bison | 3.8.2 |
| bzip2 | 1.0.8 | ccache | 4.12.1 |
| cmake | 4.2.0 | coreutils | 9.6 |
| cpio | 2.15 | dosfstools | 4.2 |
| e2fsprogs | 1.47.3 | elfutils | 0.192 |
| expat | 2.7.3 | fakeroot | 1.37.1.2 |
| findutils | 4.10.0 | flex | 2.6.4 |
| gmp | 6.3.0 | isl | 0.27 |
| libdeflate | 1.25 | liblzo | 2.10 |
| libressl | 4.2.1 | libtool | 2.5.4 |
| llvm-bpf | 21.1.6 | lz4 | 1.10.0 |
| m4 | 1.4.20 | meson | 1.6.1 |
| mold | 2.40.4 | mpc | 1.3.1 |
| mpfr | 4.2.2 | mtd-utils | 2.3.0 |
| mtools | 4.0.49 | ninja | 1.13.2 |
| patch | 2.8 | patchelf | 0.18.0 |
| pkgconf | 2.5.1 | quilt | 0.69 |
| sed | 4.9 | squashfs4 | 4.7.4 |
| tar | 1.35 | util-linux | 2.41.3 |
| xxhash | 0.8.3 | xz | 5.8.2 |
| zip | 3.0 | zlib | 1.3.1 |
| zstd | 1.5.7 | | |

## Building OpenWrt

Once in the container or development shell:

```bash
# Update and install package feeds
./scripts/feeds update -a
./scripts/feeds install -a

# Configure (select target, packages, etc.)
make menuconfig

# Build (use all available cores)
make -j$(nproc)

# Build with verbose output for debugging
make -j1 V=s
```

## Requirements

- **For Nix options**: Nix package manager (https://nixos.org/download.html)
- **For Docker options**: Docker or Podman
- **Disk space**: At least 20GB free for build artifacts
- **RAM**: At least 4GB, 8GB+ recommended

## Reproducibility

The pure Nix Docker image (`nix/docker-image.nix`) provides full
reproducibility - the same inputs will always produce the same image.
This is ideal for CI/CD pipelines and ensuring consistent build environments.

The Ubuntu-based images are not fully reproducible but may have better
compatibility with some build scripts that expect FHS-compliant systems.
