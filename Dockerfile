# OpenWrt Build Environment Dockerfile
#
# This Dockerfile creates an Ubuntu-based container with all tools
# and dependencies required to build OpenWrt.
#
# Build:
#   docker build -t openwrt-build-env .
#
# Run:
#   docker run -it --rm -v $(pwd):/openwrt -w /openwrt openwrt-build-env
#
# Multi-architecture build:
#   docker buildx build --platform linux/amd64,linux/arm64 -t openwrt-build-env .
#

FROM ubuntu:24.04

LABEL org.opencontainers.image.title="OpenWrt Build Environment"
LABEL org.opencontainers.image.description="Ubuntu-based Docker image with all OpenWrt build dependencies"
LABEL org.opencontainers.image.source="https://github.com/openwrt/openwrt"
LABEL maintainer="OpenWrt Developers"

# Tool versions from OpenWrt tools/ folder (for reference)
# These are built by OpenWrt during compilation, but system tools are used for bootstrapping
#
# 7z: 25.01              autoconf: 2.72           autoconf-archive: 2023.02.20
# automake: 1.18.1       bash: 5.3                bc: 1.08.1
# bison: 3.8.2           bzip2: 1.0.8             ccache: 4.12.1
# cmake: 4.2.0           coreutils: 9.6           cpio: 2.15
# dosfstools: 4.2        e2fsprogs: 1.47.3        elfutils: 0.192
# expat: 2.7.3           fakeroot: 1.37.1.2       findutils: 4.10.0
# flex: 2.6.4            gmp: 6.3.0               isl: 0.27
# libdeflate: 1.25       liblzo: 2.10             libtool: 2.5.4
# lz4: 1.10.0            m4: 1.4.20               meson: 1.6.1
# mold: 2.40.4           mpc: 1.3.1               mpfr: 4.2.2
# mtd-utils: 2.3.0       mtools: 4.0.49           ninja: 1.13.2
# patch: 2.8             patchelf: 0.18.0         pkgconf: 2.5.1
# quilt: 0.69            sed: 4.9                 squashfs4: 4.7.4
# tar: 1.35              util-linux: 2.41.3       xxhash: 0.8.3
# xz: 5.8.2              zip: 3.0                 zlib: 1.3.1
# zstd: 1.5.7            llvm-bpf: 21.1.6         libressl: 4.2.1

# Prevent interactive prompts
ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=UTC

# Install all OpenWrt build dependencies in a single layer
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Essential build tools (from prereq-build.mk)
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
    # ncurses for menuconfig
    libncurses5-dev \
    libncursesw5-dev \
    libncurses-dev \
    # SSL/TLS
    libssl-dev \
    # Python (>= 3.7 required)
    python3 \
    python3-dev \
    python3-distutils-extra \
    python3-setuptools \
    python3-pip \
    # Sync tools
    rsync \
    subversion \
    # Build tools
    swig \
    unzip \
    # Compression libraries
    zlib1g-dev \
    # Utilities
    file \
    wget \
    curl \
    ca-certificates \
    xsltproc \
    zip \
    # GNU tools (from prereq-build.mk)
    coreutils \
    diffutils \
    findutils \
    gzip \
    bzip2 \
    xz-utils \
    tar \
    patch \
    make \
    # Perl with required modules
    perl \
    perl-modules \
    libdata-dump-perl \
    # Build system tools
    autoconf \
    automake \
    autopoint \
    libtool \
    libtool-bin \
    pkg-config \
    cmake \
    ninja-build \
    meson \
    # Kernel building
    bc \
    kmod \
    cpio \
    # Firmware building
    fakeroot \
    device-tree-compiler \
    u-boot-tools \
    # Documentation
    asciidoc \
    binutils-dev \
    # Development tools
    ccache \
    ecj \
    fastjar \
    java-propose-classpath \
    # Image tools
    qemu-utils \
    sharutils \
    uglifyjs \
    # Additional compression tools
    lzop \
    p7zip-full \
    zstd \
    # Filesystem tools
    squashfs-tools \
    e2fsprogs \
    dosfstools \
    mtools \
    mtd-utils \
    # ELF tools
    libelf-dev \
    elfutils \
    patchelf \
    # Additional libraries
    libz-dev \
    libexpat1-dev \
    liblzo2-dev \
    liblz4-dev \
    libdeflate-dev \
    libxxhash-dev \
    # LLVM/BPF
    llvm \
    # Quilt for patching
    quilt \
    # Misc utilities
    time \
    util-linux \
    uuid-dev \
    libpam-dev \
    # Needed for some packages
    libgmp-dev \
    libmpfr-dev \
    libmpc-dev \
    && \
    # Install Python packages
    pip3 install --break-system-packages \
        pyelftools \
        jsonschema \
        jinja2 \
        pyyaml \
    && \
    # Create python symlink
    ln -sf /usr/bin/python3 /usr/bin/python || true \
    && \
    # Clean up
    apt-get clean \
    && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# Create non-root user for building
RUN useradd -m -s /bin/bash builder

# Create working directory
RUN mkdir -p /openwrt && chown builder:builder /openwrt

# Set environment variables for OpenWrt build
ENV FORCE_UNSAFE_CONFIGURE=1
ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8

# Volume for OpenWrt source
VOLUME ["/openwrt"]

# Working directory
WORKDIR /openwrt

# Default command
CMD ["/bin/bash"]

# Healthcheck
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD gcc --version && make --version && python3 --version || exit 1
