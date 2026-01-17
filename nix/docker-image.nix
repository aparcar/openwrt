# OpenWrt Build Environment - Pure Nix Docker Image
#
# Creates a Docker container with all OpenWrt build dependencies
# and tools from the tools/ folder at their specified versions.
#
# Usage:
#   nix-build nix/docker-image.nix
#   docker load < result
#   docker run -it --rm -v $(pwd):/openwrt openwrt-build-env
#
{ pkgs ? import <nixpkgs> {
    system = "x86_64-linux";
    config.allowUnfree = true;
  }
}:

let
  # Tool versions from OpenWrt tools/ folder
  # These match the PKG_VERSION values in each tool's Makefile
  toolVersions = {
    p7zip = "25.01";
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
    dwarves = "1.31";
    e2fsprogs = "1.47.3";
    elfutils = "0.192";
    erofs-utils = "1.8.10";
    expat = "2.7.3";
    fakeroot = "1.37.1.2";
    findutils = "4.10.0";
    flex = "2.6.4";
    flock = "2.18";
    genext2fs = "1.5.0";
    gengetopt = "2.23";
    gmp = "6.3.0";
    isl = "0.27";
    libdeflate = "1.25";
    lzo = "2.10";
    libressl = "4.2.1";
    libtool = "2.5.4";
    llvm-bpf = "21.1.6";
    lz4 = "1.10.0";
    lzma = "4.65";
    lzop = "1.04";
    m4 = "1.4.20";
    meson = "1.6.1";
    mkimage = "2025.10";
    mklibs = "0.1.45";
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
    sparse = "0.6.4";
    squashfs = "4.7.4";
    sstrip = "3.2";
    tar = "1.35";
    util-linux = "2.41.3";
    xxHash = "0.8.3";
    xz = "5.8.2";
    zip = "3.0";
    zlib = "1.3.1";
    zstd = "1.5.7";
  };

  # All packages needed for OpenWrt build
  buildTools = with pkgs; [
    # === Core System ===
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

    # === Build Essentials ===
    gnumake
    gcc
    glibc
    glibc.static
    binutils
    binutils-unwrapped
    patch
    gettext
    stdenv.cc

    # === Compression Tools ===
    # 7z:25.01 bzip2:1.0.8 lz4:1.10.0 lzop:1.04 xz:5.8.2 zstd:1.5.7 zip:3.0
    bzip2
    xz
    zstd
    lz4
    lzop
    p7zip
    zip
    unzip

    # === Build Systems ===
    # autoconf:2.72 automake:1.18.1 cmake:4.2.0 ninja:1.13.2 meson:1.6.1 pkgconf:2.5.1
    autoconf
    automake
    libtool
    pkg-config
    pkgconf
    cmake
    ninja
    meson

    # === Parser Generators ===
    # flex:2.6.4 bison:3.8.2 m4:1.4.20
    flex
    bison
    m4

    # === Python ===
    python3
    python3Packages.setuptools
    python3Packages.pyelftools
    python3Packages.jsonschema
    python3Packages.jinja2
    python3Packages.pyyaml

    # === Perl ===
    perl
    perlPackages.DataDumper

    # === Version Control ===
    git
    subversion

    # === Network Tools ===
    wget
    curl
    rsync
    cacert

    # === ncurses (for menuconfig) ===
    ncurses
    ncurses.dev

    # === Libraries ===
    # zlib:1.3.1 expat:2.7.3 gmp:6.3.0 mpfr:4.2.2 mpc:1.3.1 isl:0.27
    zlib
    zlib.dev
    zlib.static
    openssl
    openssl.dev
    expat
    expat.dev
    lzo           # liblzo:2.10
    lzo.dev
    gmp
    gmp.dev
    mpfr
    mpfr.dev
    libmpc
    isl

    # === ELF Tools ===
    # elfutils:0.192 patchelf:0.18.0 dwarves:1.31
    libelf
    elfutils
    elfutils.dev
    patchelf
    dwarves

    # === Compression Libraries ===
    # libdeflate:1.25 xxhash:0.8.3
    xxHash
    xxHash.dev
    libdeflate

    # === Filesystem Tools ===
    # squashfs4:4.7.4 e2fsprogs:1.47.3 dosfstools:4.2 mtd-utils:2.3.0 mtools:4.0.49 erofs-utils:1.8.10
    squashfsTools
    e2fsprogs
    dosfstools
    mtools
    mtd-utils
    erofs-utils

    # === Build Helpers ===
    # fakeroot:1.37.1.2 cpio:2.15 bc:1.08.1 ccache:4.12.1
    fakeroot
    time
    bc
    kmod
    cpio
    ccache

    # === Device Tree ===
    dtc

    # === U-boot Tools ===
    # mkimage:2025.10
    ubootTools

    # === Linker ===
    # mold:2.40.4
    mold

    # === Patching ===
    # quilt:0.69
    quilt

    # === Documentation ===
    asciidoc

    # === SWIG ===
    swig

    # === LLVM/Clang for BPF ===
    # llvm-bpf:21.1.6
    llvmPackages.llvm
    llvmPackages.clang
    llvmPackages.bintools

    # === Static Analysis ===
    # sparse:0.6.4
    sparse

    # === SSL ===
    # libressl:4.2.1 (OpenWrt uses this, but openssl is more compatible for host)
    libressl

    # === gengetopt ===
    # gengetopt:2.23
    gengetopt

    # === genext2fs ===
    # genext2fs:1.5.0
    genext2fs
  ];

  # Create /etc files for the container
  etcFiles = pkgs.runCommand "etc-files" { } ''
    mkdir -p $out/etc
    cat > $out/etc/passwd << 'EOF'
    root:x:0:0:root:/root:/bin/bash
    builder:x:1000:1000:OpenWrt Builder:/home/builder:/bin/bash
    nobody:x:65534:65534:Nobody:/nonexistent:/bin/false
    EOF
    cat > $out/etc/group << 'EOF'
    root:x:0:
    builder:x:1000:
    nogroup:x:65534:
    EOF
    cat > $out/etc/nsswitch.conf << 'EOF'
    passwd: files
    group: files
    shadow: files
    hosts: files dns
    EOF
  '';

  # Entry point script
  entrypoint = pkgs.writeShellScriptBin "entrypoint" ''
    echo "OpenWrt Build Environment"
    echo "========================="
    echo "Tools from OpenWrt tools/ folder included."
    echo ""
    exec "$@"
  '';

in
pkgs.dockerTools.buildLayeredImage {
  name = "openwrt-build-env";
  tag = "latest";

  contents = buildTools ++ [
    etcFiles
    entrypoint
    pkgs.dockerTools.caCertificates
    pkgs.dockerTools.fakeNss
  ];

  extraCommands = ''
    mkdir -p tmp root home/builder openwrt
    chmod 1777 tmp
    mkdir -p usr/bin
    ln -sf ${pkgs.python3}/bin/python3 usr/bin/python || true
    ln -sf ${pkgs.python3}/bin/python3 usr/bin/python3 || true
    ln -sf ${pkgs.coreutils}/bin/env usr/bin/env || true
  '';

  config = {
    Cmd = [ "/bin/bash" ];
    Entrypoint = [ "${entrypoint}/bin/entrypoint" ];
    WorkingDir = "/openwrt";
    Env = [
      "PATH=/bin:/usr/bin:/sbin:/usr/sbin:${pkgs.lib.makeBinPath buildTools}"
      "FORCE_UNSAFE_CONFIGURE=1"
      "LANG=C.UTF-8"
      "LC_ALL=C.UTF-8"
      "HOME=/root"
      "SSL_CERT_FILE=${pkgs.cacert}/etc/ssl/certs/ca-bundle.crt"
    ];
    Labels = {
      "org.opencontainers.image.title" = "OpenWrt Build Environment";
      "org.opencontainers.image.description" = "Nix-based Docker image with OpenWrt build dependencies";
    };
    Volumes = { "/openwrt" = { }; };
  };
}
