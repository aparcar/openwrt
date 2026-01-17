# OpenWrt Build Environment - Nix Shell
#
# This file provides a development environment with all OpenWrt build dependencies.
#
# Usage:
#   nix-shell
#
# Or with direnv:
#   echo "use nix" > .envrc && direnv allow
#
{ pkgs ? import <nixpkgs> { } }:

pkgs.mkShell {
  name = "openwrt-build-env";

  buildInputs = with pkgs; [
    # Essential build tools (from prereq-build.mk)
    gnumake
    gcc
    glibc
    glibc.static
    binutils
    coreutils
    findutils
    diffutils
    patch
    gawk
    gettext
    gnused
    gnugrep

    # Compression tools (from tools/ folder)
    # 7z: 25.01, bzip2: 1.0.8, lz4: 1.10.0, xz: 5.8.2, zstd: 1.5.7
    gzip
    bzip2
    xz
    zstd
    lz4
    lzop
    p7zip
    zip
    unzip

    # Build system tools
    # autoconf: 2.72, automake: 1.18.1, cmake: 4.2.0, ninja: 1.13.2, meson: 1.6.1
    autoconf
    automake
    libtool
    pkg-config
    cmake
    ninja
    meson

    # Parser generators
    # flex: 2.6.4, bison: 3.8.2, m4: 1.4.20
    flex
    bison
    m4

    # Python (>= 3.7 required)
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
    perlPackages.FileCopy
    perlPackages.FileCompare
    perlPackages.ThreadQueue
    perlPackages.IPCCmd

    # Version control
    git
    subversion

    # Download tools
    wget
    curl
    rsync

    # ncurses for menuconfig
    ncurses
    ncurses.dev

    # Libraries
    # zlib: 1.3.1, expat: 2.7.3
    zlib
    zlib.dev
    openssl
    openssl.dev
    expat
    expat.dev
    lzo  # liblzo: 2.10
    libelf
    elfutils  # elfutils: 0.192
    xxHash  # xxhash: 0.8.3
    libdeflate  # libdeflate: 1.25
    gmp  # gmp: 6.3.0
    mpfr  # mpfr: 4.2.2
    libmpc  # mpc: 1.3.1
    isl  # isl: 0.27

    # Filesystem tools
    # squashfs4: 4.7.4, e2fsprogs: 1.47.3, dosfstools: 4.2, mtd-utils: 2.3.0
    squashfsTools
    e2fsprogs
    dosfstools
    mtools  # mtools: 4.0.49
    mtd-utils

    # ELF tools
    # patchelf: 0.18.0
    patchelf

    # Build helpers
    # fakeroot: 1.37.1.2, cpio: 2.15
    fakeroot
    file
    which
    time
    bc  # bc: 1.08.1
    kmod
    cpio

    # Device tree
    dtc

    # U-boot tools (mkimage: 2025.10)
    ubootTools

    # Build optimization
    # ccache: 4.12.1, mold: 2.40.4
    ccache
    mold

    # Patching tools
    # quilt: 0.69
    quilt

    # Documentation
    asciidoc

    # SWIG for bindings
    swig

    # Misc utilities
    # util-linux: 2.41.3, tar: 1.35, sed: 4.9
    util-linux

    # LLVM for BPF (llvm-bpf: 21.1.6)
    llvmPackages.llvm
    llvmPackages.clang

    # For signing
    libressl  # libressl: 4.2.1

    # For erofs (erofs-utils: 1.8.10)
    erofs-utils

    # pkgconf: 2.5.1
    pkgconf

    # Sparse for static analysis (sparse: 0.6.4)
    sparse
  ];

  shellHook = ''
    echo "=========================================="
    echo "OpenWrt Build Environment"
    echo "=========================================="
    echo ""
    echo "This shell includes all dependencies needed to build OpenWrt."
    echo ""
    echo "Tool versions from OpenWrt tools/ folder:"
    echo "  autoconf: 2.72        automake: 1.18.1"
    echo "  cmake: 4.2.0          ninja: 1.13.2"
    echo "  meson: 1.6.1          ccache: 4.12.1"
    echo "  flex: 2.6.4           bison: 3.8.2"
    echo "  zlib: 1.3.1           zstd: 1.5.7"
    echo ""
    echo "To build OpenWrt:"
    echo "  1. ./scripts/feeds update -a"
    echo "  2. ./scripts/feeds install -a"
    echo "  3. make menuconfig"
    echo "  4. make -j\$(nproc)"
    echo ""
  '';

  # Environment variables for OpenWrt build
  FORCE_UNSAFE_CONFIGURE = "1";

  # Ensure correct locale
  LANG = "C.UTF-8";
  LC_ALL = "C.UTF-8";
}
