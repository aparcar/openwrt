# OpenWrt Modern Build System - Base Build Environment
#
# This is the base image with all host tools needed to:
# 1. Build the OpenWrt cross-toolchain from source
# 2. Build the kernel and packages
# 3. Generate firmware images
#
# Usage:
#   1. Build toolchain: docker run -v openwrt:/openwrt owrt-build toolchain build <target>
#   2. Build firmware:  docker run -v openwrt:/openwrt owrt-build build <target>
#
# The toolchain is built from OpenWrt sources (not pre-built) because
# OpenWrt applies custom patches to GCC, binutils, and musl.
#
# Host tools (apk, mkimage, fwtool, squashfs4, mtd-utils, etc.) are built
# during image creation using the owrt tool system.

FROM ubuntu:24.04 AS base

LABEL maintainer="OpenWrt Developers"
LABEL description="OpenWrt PoC Build Environment"

# Avoid interactive prompts
ENV DEBIAN_FRONTEND=noninteractive

# Install all host dependencies needed to build OpenWrt toolchain and packages
RUN apt-get update && apt-get install -y \
    # Essential build tools
    build-essential \
    clang \
    flex \
    bison \
    g++ \
    gawk \
    gcc-multilib \
    g++-multilib \
    gettext \
    git \
    libelf-dev \
    libncurses5-dev \
    libssl-dev \
    python3 \
    python3-pip \
    python3-setuptools \
    python3-venv \
    rsync \
    swig \
    unzip \
    zlib1g-dev \
    file \
    wget \
    curl \
    xz-utils \
    # For toolchain build (GCC bootstrap)
    texinfo \
    help2man \
    gperf \
    autoconf \
    automake \
    libtool \
    libgmp-dev \
    libmpfr-dev \
    libmpc-dev \
    libisl-dev \
    libzstd-dev \
    zstd \
    # For kernel build
    bc \
    kmod \
    cpio \
    # For device tree
    device-tree-compiler \
    # For image generation
    squashfs-tools \
    # Note: mtd-utils is built from source with OpenWrt patches (adds -E option to ubinize)
    u-boot-tools \
    # For mtd-utils build
    liblzma-dev \
    uuid-dev \
    # For U-Boot build
    xxd \
    e2fsprogs \
    dosfstools \
    mtools \
    fdisk \
    gdisk \
    parted \
    # For package management and isolation
    fakeroot \
    fakechroot \
    proot \
    # Build systems
    ninja-build \
    cmake \
    meson \
    pkg-config \
    # Compilation cache
    ccache \
    # Python dependencies
    python3-yaml \
    python3-jsonschema \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user for builds
RUN useradd -m -s /bin/bash builder && \
    mkdir -p /build /build/dl /build/host-staging /output /openwrt /toolchain && \
    chown -R builder:builder /build /output /openwrt /toolchain

# Set up Python virtual environment
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install pyyaml jsonschema click rich

# Set build environment variables
ENV FORCE_UNSAFE_CONFIGURE=1
ENV LC_ALL=C
ENV LANG=C
ENV OPENWRT_DIR=/openwrt
ENV TOOLCHAIN_DIR=/toolchain
ENV BUILD_DIR=/build

# ============================================================================
# Build host tools stage
# ============================================================================
FROM base AS host-tools-builder

# Copy only what's needed for building host tools
COPY --chown=builder:builder owrt /openwrt/owrt
COPY --chown=builder:builder toolchain /openwrt/toolchain

WORKDIR /openwrt
USER builder

# Build all host tools using the owrt tool system
# These tools are architecture-independent and used for:
# - apk: APK package manager (v3 format)
# - mkimage: U-Boot image creation
# - fwtool: Firmware metadata
# - squashfs4: SquashFS filesystem creation
# - mtd-utils: Flash utilities with OpenWrt patches
# - lua: Scripting for build system
RUN python3 -m owrt -v tool build-all

# ============================================================================
# Final image with host tools included
# ============================================================================
FROM base

# Copy built host tools from builder stage
COPY --from=host-tools-builder --chown=builder:builder /build/host-staging /build/host-staging

# Add host tools to PATH
ENV PATH="/build/host-staging/bin:${PATH}"

WORKDIR /build
USER builder

# Verify host tools are available
RUN apk --version && echo "Host tools installed successfully"

# Default command
CMD ["/bin/bash"]
