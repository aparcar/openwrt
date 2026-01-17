# OpenWrt Build Environment - Pure Nix Docker Image
#
# This creates a fully reproducible Docker image using only Nix packages,
# without relying on Ubuntu or any external base image.
#
# Usage:
#   nix-build nix/docker-image.nix
#   docker load < result
#   docker run -it --rm -v $(pwd):/openwrt openwrt-build-env-nix
#
{ pkgs ? import <nixpkgs> {
    system = "x86_64-linux";
    config.allowUnfree = true;
  }
}:

let
  # OpenWrt tools versions from tools/ folder
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
    m4 = "1.4.20";
    meson = "1.6.1";
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

  # All packages needed for OpenWrt build
  buildPackages = with pkgs; [
    # Base system
    bashInteractive
    coreutils
    findutils
    diffutils
    gnused
    gnugrep
    gawk
    gnutar
    gzip
    which
    file
    less
    procps
    util-linux

    # Build essentials
    gnumake
    gcc
    glibc
    glibc.static
    binutils
    binutils-unwrapped
    patch
    gettext

    # Compression
    bzip2
    xz
    zstd
    lz4
    lzop
    p7zip
    zip
    unzip

    # Build systems
    autoconf
    automake
    libtool
    pkg-config
    cmake
    ninja
    meson

    # Parsers
    flex
    bison
    m4

    # Python
    python3
    python3Packages.setuptools
    python3Packages.pyelftools
    python3Packages.jsonschema
    python3Packages.jinja2
    python3Packages.pyyaml

    # Perl
    perl
    perlPackages.DataDumper
    perlPackages.FindBin

    # VCS
    git
    subversion

    # Network
    wget
    curl
    rsync
    cacert

    # ncurses
    ncurses
    ncurses.dev

    # Libraries
    zlib
    zlib.dev
    zlib.static
    openssl
    openssl.dev
    expat
    expat.dev
    lzo
    lzo.dev
    libelf
    elfutils
    elfutils.dev
    xxHash
    xxHash.dev
    libdeflate
    gmp
    gmp.dev
    mpfr
    mpfr.dev
    libmpc
    isl

    # Filesystem tools
    squashfsTools
    e2fsprogs
    dosfstools
    mtools
    mtd-utils

    # ELF tools
    patchelf

    # Build helpers
    fakeroot
    time
    bc
    kmod
    cpio

    # Device tree
    dtc

    # U-boot
    ubootTools

    # Optimization
    ccache
    mold

    # Patching
    quilt

    # Docs
    asciidoc

    # SWIG
    swig

    # LLVM/Clang for BPF
    llvmPackages.llvm
    llvmPackages.clang
    llvmPackages.bintools

    # Static analysis
    sparse

    # erofs
    erofs-utils

    # SSL
    libressl

    # pkg-config alternative
    pkgconf

    # For running configure scripts
    stdenv.cc

    # Needed for FHS compatibility
    (pkgs.buildFHSEnv {
      name = "openwrt-fhs";
      targetPkgs = pkgs: [ ];
    })
  ];

  # Create /etc files
  etcFiles = pkgs.runCommand "etc-files" { } ''
    mkdir -p $out/etc

    # passwd
    cat > $out/etc/passwd << 'EOF'
    root:x:0:0:root:/root:/bin/bash
    builder:x:1000:1000:OpenWrt Builder:/home/builder:/bin/bash
    nobody:x:65534:65534:Nobody:/nonexistent:/bin/false
    EOF

    # group
    cat > $out/etc/group << 'EOF'
    root:x:0:
    builder:x:1000:
    nogroup:x:65534:
    EOF

    # shadow (empty passwords)
    cat > $out/etc/shadow << 'EOF'
    root::0:0:99999:7:::
    builder::0:0:99999:7:::
    EOF

    # nsswitch.conf
    cat > $out/etc/nsswitch.conf << 'EOF'
    passwd: files
    group: files
    shadow: files
    hosts: files dns
    EOF
  '';

  # Entry point script
  entrypoint = pkgs.writeShellScriptBin "entrypoint" ''
    #!/bin/bash
    echo "=========================================="
    echo "OpenWrt Build Environment (Pure Nix)"
    echo "=========================================="
    echo ""
    echo "Tool versions from OpenWrt tools/ folder:"
    echo "  autoconf: ${toolVersions.autoconf}     automake: ${toolVersions.automake}"
    echo "  cmake: ${toolVersions.cmake}          ninja: ${toolVersions.ninja}"
    echo "  meson: ${toolVersions.meson}          ccache: ${toolVersions.ccache}"
    echo "  flex: ${toolVersions.flex}           bison: ${toolVersions.bison}"
    echo "  zlib: ${toolVersions.zlib}           zstd: ${toolVersions.zstd}"
    echo ""
    echo "To build OpenWrt:"
    echo "  1. ./scripts/feeds update -a"
    echo "  2. ./scripts/feeds install -a"
    echo "  3. make menuconfig"
    echo "  4. make -j\$(nproc)"
    echo ""
    exec "$@"
  '';

in
pkgs.dockerTools.buildLayeredImage {
  name = "openwrt-build-env-nix";
  tag = "latest";

  contents = buildPackages ++ [
    etcFiles
    entrypoint
    pkgs.dockerTools.caCertificates
    pkgs.dockerTools.fakeNss
  ];

  extraCommands = ''
    # Create necessary directories
    mkdir -p tmp root home/builder openwrt
    chmod 1777 tmp

    # Create symlinks for common locations
    mkdir -p usr/bin usr/lib usr/include
    ln -sf /bin/env usr/bin/env || true

    # Ensure python symlink
    ln -sf ${pkgs.python3}/bin/python3 usr/bin/python || true
    ln -sf ${pkgs.python3}/bin/python3 usr/bin/python3 || true
  '';

  config = {
    Cmd = [ "/bin/bash" ];
    Entrypoint = [ "${entrypoint}/bin/entrypoint" ];
    WorkingDir = "/openwrt";
    Env = [
      "PATH=/bin:/usr/bin:/sbin:/usr/sbin:${pkgs.lib.makeBinPath buildPackages}"
      "FORCE_UNSAFE_CONFIGURE=1"
      "LANG=C.UTF-8"
      "LC_ALL=C.UTF-8"
      "HOME=/root"
      "SSL_CERT_FILE=${pkgs.cacert}/etc/ssl/certs/ca-bundle.crt"
      "NIX_SSL_CERT_FILE=${pkgs.cacert}/etc/ssl/certs/ca-bundle.crt"
    ];
    Labels = {
      "org.opencontainers.image.title" = "OpenWrt Build Environment (Pure Nix)";
      "org.opencontainers.image.description" = "Fully reproducible Nix-based Docker image for building OpenWrt";
      "org.opencontainers.image.source" = "https://github.com/openwrt/openwrt";
    };
    Volumes = {
      "/openwrt" = { };
    };
  };
}
