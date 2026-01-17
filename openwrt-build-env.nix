# OpenWrt Build Environment - Nix Docker Image
#
# This Nix file creates a Docker container based on Ubuntu with all the
# tools and dependencies required to build OpenWrt.
#
# Usage:
#   nix-build openwrt-build-env.nix
#   docker load < result
#   docker run -it --rm -v $(pwd):/openwrt openwrt-build-env
#
# Or with flakes:
#   nix build .#openwrt-build-env
#
{ pkgs ? import <nixpkgs> { system = "x86_64-linux"; } }:

let
  # OpenWrt tools versions from tools/ folder
  # These are the versions that OpenWrt builds internally
  toolVersions = {
    "7z" = "25.01";
    autoconf = "2.72";
    autoconf-archive = "2023.02.20";
    automake = "1.18.1";
    bash = "5.3";
    bc = "1.08.1";
    bison = "3.8.2";
    bzip2 = "1.0.8";
    ccache = "4.12.1";
    cmake = "4.2.0";
    coreutils = "9.6";
    cpio = "2.15";
    dosfstools = "4.2";
    e2fsprogs = "1.47.3";
    elfutils = "0.192";
    expat = "2.7.3";
    fakeroot = "1.37.1.2";
    findutils = "4.10.0";
    flex = "2.6.4";
    gmp = "6.3.0";
    isl = "0.27";
    libdeflate = "1.25";
    liblzo = "2.10";
    libtool = "2.5.4";
    lz4 = "1.10.0";
    lzma = "4.65";
    lzop = "1.04";
    m4 = "1.4.20";
    meson = "1.6.1";
    mkimage = "2025.10";
    mold = "2.40.4";
    mpc = "1.3.1";
    mpfr = "4.2.2";
    mtd-utils = "2.3.0";
    mtools = "4.0.49";
    ninja = "1.13.2";
    patch = "2.8";
    patchelf = "0.18.0";
    pkgconf = "2.5.1";
    quilt = "0.69";
    sed = "4.9";
    squashfs4 = "4.7.4";
    tar = "1.35";
    util-linux = "2.41.3";
    xxhash = "0.8.3";
    xz = "5.8.2";
    zip = "3.0";
    zlib = "1.3.1";
    zstd = "1.5.7";
  };

  # Ubuntu 24.04 (Noble Numbat) base image
  ubuntuBase = pkgs.dockerTools.pullImage {
    imageName = "ubuntu";
    imageDigest = "sha256:72297848456d5d37d1262630108ab308d3e9ec7ed1c3286a32fe09856619a782";
    sha256 = "sha256-GaGwxp8B1MlB5VXYVj29VDvuDVX5rLXLfwkH3Z0qJcE=";
    finalImageName = "ubuntu";
    finalImageTag = "24.04";
  };

  # Script to setup the build environment
  setupScript = pkgs.writeShellScriptBin "setup-openwrt-env" ''
    #!/bin/bash
    set -e

    echo "OpenWrt Build Environment"
    echo "========================="
    echo ""
    echo "This container includes all dependencies needed to build OpenWrt."
    echo ""
    echo "To build OpenWrt:"
    echo "  1. Clone OpenWrt: git clone https://github.com/openwrt/openwrt.git"
    echo "  2. cd openwrt"
    echo "  3. ./scripts/feeds update -a"
    echo "  4. ./scripts/feeds install -a"
    echo "  5. make menuconfig"
    echo "  6. make -j\$(nproc)"
    echo ""
    echo "Tool versions (from OpenWrt tools/ folder):"
    echo "  autoconf: ${toolVersions.autoconf}"
    echo "  automake: ${toolVersions.automake}"
    echo "  cmake: ${toolVersions.cmake}"
    echo "  ninja: ${toolVersions.ninja}"
    echo "  meson: ${toolVersions.meson}"
    echo ""
  '';

  # Create the Docker image
  dockerImage = pkgs.dockerTools.buildImage {
    name = "openwrt-build-env";
    tag = "latest";

    fromImage = ubuntuBase;

    # Copy configuration and setup files
    copyToRoot = pkgs.buildEnv {
      name = "openwrt-build-env-root";
      paths = [ setupScript ];
      pathsToLink = [ "/bin" ];
    };

    # Run commands to install packages
    runAsRoot = ''
      #!${pkgs.runtimeShell}
      set -e

      # Set environment variables
      export DEBIAN_FRONTEND=noninteractive
      export TZ=UTC

      # Update package lists
      apt-get update

      # Install essential build tools and OpenWrt dependencies
      # Based on prereq-build.mk requirements and tools/ folder
      apt-get install -y --no-install-recommends \
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
        libncurses5-dev \
        libncursesw5-dev \
        libssl-dev \
        python3 \
        python3-dev \
        python3-distutils-extra \
        python3-setuptools \
        python3-pip \
        rsync \
        swig \
        unzip \
        zlib1g-dev \
        file \
        wget \
        curl \
        ca-certificates \
        xsltproc \
        zip \
        \
        # Additional tools from prereq-build.mk
        coreutils \
        diffutils \
        findutils \
        gzip \
        bzip2 \
        xz-utils \
        tar \
        patch \
        make \
        perl \
        perl-modules \
        libdata-dump-perl \
        \
        # For menuconfig
        libncurses-dev \
        \
        # For building packages
        autoconf \
        automake \
        autopoint \
        libtool \
        libtool-bin \
        pkg-config \
        cmake \
        ninja-build \
        meson \
        \
        # For kernel building
        bc \
        kmod \
        cpio \
        \
        # For firmware building
        fakeroot \
        device-tree-compiler \
        u-boot-tools \
        \
        # For documentation
        asciidoc \
        binutils-dev \
        \
        # For development
        ccache \
        ecj \
        fastjar \
        java-propose-classpath \
        \
        # Image tools
        qemu-utils \
        sharutils \
        subversion \
        uglifyjs \
        \
        # Additional compression tools
        lzop \
        p7zip-full \
        zstd \
        \
        # Filesystem tools
        squashfs-tools \
        e2fsprogs \
        dosfstools \
        mtools \
        mtd-utils \
        \
        # ELF tools
        libelf-dev \
        elfutils \
        patchelf \
        \
        # Additional libraries
        libz-dev \
        zlib1g-dev \
        libexpat1-dev \
        liblzo2-dev \
        liblz4-dev \
        libdeflate-dev \
        libxxhash-dev \
        \
        # For signing
        libssl-dev \
        \
        # For LLVM/BPF
        llvm \
        clang \
        \
        # For quilt patching
        quilt \
        \
        # Misc utilities
        time \
        util-linux \
        uuid-dev \
        libpam-dev

      # Install Python packages needed for build
      pip3 install --break-system-packages \
        pyelftools \
        jsonschema \
        jinja2 \
        pyyaml

      # Setup symlinks for compatibility
      ln -sf /usr/bin/python3 /usr/bin/python || true

      # Clean up to reduce image size
      apt-get clean
      rm -rf /var/lib/apt/lists/*
      rm -rf /tmp/*
      rm -rf /var/tmp/*

      # Create a non-root user for building (optional but recommended)
      useradd -m -s /bin/bash builder || true

      # Set working directory
      mkdir -p /openwrt
      chown builder:builder /openwrt
    '';

    config = {
      Cmd = [ "/bin/bash" ];
      WorkingDir = "/openwrt";
      Env = [
        "FORCE_UNSAFE_CONFIGURE=1"
        "LANG=C.UTF-8"
        "LC_ALL=C.UTF-8"
        "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
      ];
      Labels = {
        "org.opencontainers.image.title" = "OpenWrt Build Environment";
        "org.opencontainers.image.description" = "Ubuntu-based Docker image with all OpenWrt build dependencies";
        "org.opencontainers.image.source" = "https://github.com/openwrt/openwrt";
        "maintainer" = "OpenWrt Developers";
      };
      Volumes = {
        "/openwrt" = { };
      };
    };
  };

in
dockerImage
