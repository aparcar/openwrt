# OpenWrt Build Environment Nix Flake
#
# This flake provides a Docker image and development shell for building OpenWrt.
#
# Usage:
#   # Build the Docker image
#   nix build .#openwrt-docker-image
#   docker load < result
#   docker run -it --rm -v $(pwd):/openwrt openwrt-build-env
#
#   # Or enter a development shell directly (no Docker needed)
#   nix develop
#
{
  description = "OpenWrt Build Environment";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-24.05";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = import nixpkgs {
          inherit system;
          config.allowUnfree = true;
        };

        # OpenWrt tools versions from tools/ folder for reference
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

        # Common build inputs for OpenWrt
        commonBuildInputs = with pkgs; [
          # Essential build tools
          gnumake
          gcc
          glibc
          binutils
          coreutils
          findutils
          diffutils
          patch
          gawk
          gettext
          gnused
          gnugrep

          # Compression tools
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
          autoconf
          automake
          libtool
          pkg-config
          cmake
          ninja
          meson

          # Parser generators
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
          zlib
          zlib.dev
          openssl
          openssl.dev
          expat
          expat.dev
          lzo
          libelf
          elfutils

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
          file
          which
          time

          # For kernel building
          bc
          kmod
          cpio

          # Device tree
          dtc

          # U-boot tools
          ubootTools

          # Misc
          ccache
          quilt
          asciidoc
          swig
          util-linux

          # LLVM for BPF
          llvmPackages.llvm
          llvmPackages.clang
        ];

        # Ubuntu base for Docker image
        ubuntuBase = pkgs.dockerTools.pullImage {
          imageName = "ubuntu";
          imageDigest = "sha256:72297848456d5d37d1262630108ab308d3e9ec7ed1c3286a32fe09856619a782";
          sha256 = "sha256-GaGwxp8B1MlB5VXYVj29VDvuDVX5rLXLfwkH3Z0qJcE=";
          finalImageName = "ubuntu";
          finalImageTag = "24.04";
        };

        # Build the Docker image
        dockerImage = pkgs.dockerTools.buildImage {
          name = "openwrt-build-env";
          tag = "latest";

          fromImage = ubuntuBase;

          runAsRoot = ''
            #!${pkgs.runtimeShell}
            set -e

            export DEBIAN_FRONTEND=noninteractive
            export TZ=UTC

            apt-get update

            apt-get install -y --no-install-recommends \
              build-essential clang flex bison g++ gawk \
              gcc-multilib g++-multilib gettext git \
              libncurses5-dev libncursesw5-dev libssl-dev \
              python3 python3-dev python3-setuptools python3-pip \
              rsync swig unzip zlib1g-dev file wget curl ca-certificates \
              xsltproc zip coreutils diffutils findutils gzip bzip2 \
              xz-utils tar patch make perl perl-modules libdata-dump-perl \
              libncurses-dev autoconf automake autopoint libtool libtool-bin \
              pkg-config cmake ninja-build meson bc kmod cpio fakeroot \
              device-tree-compiler u-boot-tools asciidoc binutils-dev ccache \
              ecj fastjar java-propose-classpath qemu-utils sharutils subversion \
              uglifyjs lzop p7zip-full zstd squashfs-tools e2fsprogs dosfstools \
              mtools mtd-utils libelf-dev elfutils patchelf libz-dev zlib1g-dev \
              libexpat1-dev liblzo2-dev liblz4-dev libdeflate-dev libxxhash-dev \
              libssl-dev llvm clang quilt time util-linux uuid-dev libpam-dev

            pip3 install --break-system-packages \
              pyelftools jsonschema jinja2 pyyaml

            ln -sf /usr/bin/python3 /usr/bin/python || true

            apt-get clean
            rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

            useradd -m -s /bin/bash builder || true
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
            };
            Volumes = { "/openwrt" = { }; };
          };
        };

      in
      {
        packages = {
          openwrt-docker-image = dockerImage;
          default = dockerImage;
        };

        # Development shell - enter with: nix develop
        devShells.default = pkgs.mkShell {
          name = "openwrt-build-env";

          buildInputs = commonBuildInputs;

          shellHook = ''
            echo "=========================================="
            echo "OpenWrt Build Environment"
            echo "=========================================="
            echo ""
            echo "This shell includes all dependencies needed to build OpenWrt."
            echo ""
            echo "To build OpenWrt:"
            echo "  1. ./scripts/feeds update -a"
            echo "  2. ./scripts/feeds install -a"
            echo "  3. make menuconfig"
            echo "  4. make -j$(nproc)"
            echo ""
            echo "Environment ready!"
            echo ""
          '';

          # Environment variables for OpenWrt build
          FORCE_UNSAFE_CONFIGURE = "1";
        };
      }
    );
}
