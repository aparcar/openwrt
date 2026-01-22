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

FROM ubuntu:24.04

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
    mkdir -p /build /output /openwrt /toolchain && \
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

WORKDIR /build
USER builder

# Default command
CMD ["/bin/bash"]
