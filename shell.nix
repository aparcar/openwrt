# OpenWrt Build Environment - Nix Shell
#
# Usage: nix-shell
#
{ pkgs ? import <nixpkgs> { } }:

pkgs.mkShell {
  name = "openwrt-build-env";

  buildInputs = with pkgs; [
    # Core
    bashInteractive coreutils findutils diffutils gnused gnugrep gawk
    gnutar gzip which file less procps util-linux

    # Build essentials
    gnumake gcc glibc glibc.static binutils patch gettext

    # Compression (7z:25.01 bzip2:1.0.8 lz4:1.10.0 xz:5.8.2 zstd:1.5.7 zip:3.0)
    bzip2 xz zstd lz4 lzop p7zip zip unzip

    # Build systems (autoconf:2.72 automake:1.18.1 cmake:4.2.0 ninja:1.13.2 meson:1.6.1)
    autoconf automake libtool pkg-config pkgconf cmake ninja meson

    # Parsers (flex:2.6.4 bison:3.8.2 m4:1.4.20)
    flex bison m4

    # Python
    python3 python3Packages.setuptools python3Packages.pyelftools
    python3Packages.jsonschema python3Packages.jinja2 python3Packages.pyyaml

    # Perl
    perl perlPackages.DataDumper

    # VCS & network
    git subversion wget curl rsync

    # ncurses (for menuconfig)
    ncurses ncurses.dev

    # Libraries (zlib:1.3.1 expat:2.7.3 gmp:6.3.0 mpfr:4.2.2 mpc:1.3.1 isl:0.27)
    zlib zlib.dev zlib.static openssl openssl.dev expat expat.dev
    lzo lzo.dev gmp gmp.dev mpfr mpfr.dev libmpc isl

    # ELF tools (elfutils:0.192 patchelf:0.18.0 dwarves:1.31)
    libelf elfutils elfutils.dev patchelf dwarves

    # Compression libs (libdeflate:1.25 xxhash:0.8.3)
    xxHash xxHash.dev libdeflate

    # Filesystem (squashfs4:4.7.4 e2fsprogs:1.47.3 dosfstools:4.2 mtd-utils:2.3.0 erofs-utils:1.8.10)
    squashfsTools e2fsprogs dosfstools mtools mtd-utils erofs-utils

    # Build helpers (fakeroot:1.37.1.2 cpio:2.15 bc:1.08.1 ccache:4.12.1)
    fakeroot time bc kmod cpio ccache

    # Device tree & U-boot (mkimage:2025.10)
    dtc ubootTools

    # Misc (mold:2.40.4 quilt:0.69 sparse:0.6.4 gengetopt:2.23 genext2fs:1.5.0)
    mold quilt asciidoc swig sparse libressl gengetopt genext2fs

    # LLVM/BPF (llvm-bpf:21.1.6)
    llvmPackages.llvm llvmPackages.clang llvmPackages.bintools
  ];

  FORCE_UNSAFE_CONFIGURE = "1";
  LANG = "C.UTF-8";

  shellHook = ''
    echo "OpenWrt Build Environment"
  '';
}
