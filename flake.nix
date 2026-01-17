{
  description = "OpenWrt Build Environment";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-24.05";
  };

  outputs = { self, nixpkgs }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs { inherit system; config.allowUnfree = true; };

      # Tool versions from OpenWrt tools/ folder
      toolVersions = {
        p7zip = "25.01"; autoconf = "2.72"; automake = "1.18.1";
        bash = "5.3"; bc = "1.08.1"; bison = "3.8.2"; bzip2 = "1.0.8";
        ccache = "4.12.1"; cmake = "4.2.0"; coreutils = "9.6"; cpio = "2.15";
        dosfstools = "4.2"; dwarves = "1.31"; e2fsprogs = "1.47.3";
        elfutils = "0.192"; erofs-utils = "1.8.10"; expat = "2.7.3";
        fakeroot = "1.37.1.2"; findutils = "4.10.0"; flex = "2.6.4";
        genext2fs = "1.5.0"; gengetopt = "2.23"; gmp = "6.3.0"; isl = "0.27";
        libdeflate = "1.25"; lzo = "2.10"; libressl = "4.2.1"; libtool = "2.5.4";
        llvm-bpf = "21.1.6"; lz4 = "1.10.0"; lzop = "1.04"; m4 = "1.4.20";
        meson = "1.6.1"; mkimage = "2025.10"; mold = "2.40.4"; mpc = "1.3.1";
        mpfr = "4.2.2"; mtd-utils = "2.3.0"; mtools = "4.0.49"; ninja = "1.13.2";
        patch = "2.8"; patchelf = "0.18.0"; pkgconf = "2.5.1"; quilt = "0.69";
        sed = "4.9"; sparse = "0.6.4"; squashfs = "4.7.4"; tar = "1.35";
        util-linux = "2.41.3"; xxHash = "0.8.3"; xz = "5.8.2"; zip = "3.0";
        zlib = "1.3.1"; zstd = "1.5.7";
      };

      buildTools = with pkgs; [
        # Core
        bashInteractive coreutils findutils diffutils gnused gnugrep gawk
        gnutar gzip which file less procps util-linux

        # Build essentials
        gnumake gcc glibc glibc.static binutils patch gettext stdenv.cc

        # Compression
        bzip2 xz zstd lz4 lzop p7zip zip unzip

        # Build systems
        autoconf automake libtool pkg-config pkgconf cmake ninja meson

        # Parsers
        flex bison m4

        # Python
        python3 python3Packages.setuptools python3Packages.pyelftools
        python3Packages.jsonschema python3Packages.jinja2 python3Packages.pyyaml

        # Perl
        perl perlPackages.DataDumper

        # VCS & network
        git subversion wget curl rsync cacert

        # ncurses
        ncurses ncurses.dev

        # Libraries
        zlib zlib.dev zlib.static openssl openssl.dev expat expat.dev
        lzo lzo.dev gmp gmp.dev mpfr mpfr.dev libmpc isl

        # ELF tools
        libelf elfutils elfutils.dev patchelf dwarves

        # Compression libs
        xxHash xxHash.dev libdeflate

        # Filesystem tools
        squashfsTools e2fsprogs dosfstools mtools mtd-utils erofs-utils

        # Build helpers
        fakeroot time bc kmod cpio ccache

        # Device tree & U-boot
        dtc ubootTools

        # Misc
        mold quilt asciidoc swig sparse libressl gengetopt genext2fs

        # LLVM/BPF
        llvmPackages.llvm llvmPackages.clang llvmPackages.bintools
      ];

      etcFiles = pkgs.runCommand "etc-files" { } ''
        mkdir -p $out/etc
        cat > $out/etc/passwd << 'EOF'
        root:x:0:0:root:/root:/bin/bash
        builder:x:1000:1000:builder:/home/builder:/bin/bash
        EOF
        cat > $out/etc/group << 'EOF'
        root:x:0:
        builder:x:1000:
        EOF
      '';

      entrypoint = pkgs.writeShellScriptBin "entrypoint" ''
        exec "$@"
      '';

    in {
      packages.${system} = {
        default = pkgs.dockerTools.buildLayeredImage {
          name = "openwrt-build-env";
          tag = "latest";
          contents = buildTools ++ [
            etcFiles entrypoint
            pkgs.dockerTools.caCertificates
            pkgs.dockerTools.fakeNss
          ];
          extraCommands = ''
            mkdir -p tmp root home/builder openwrt usr/bin
            chmod 1777 tmp
            ln -sf ${pkgs.python3}/bin/python3 usr/bin/python || true
            ln -sf ${pkgs.coreutils}/bin/env usr/bin/env || true
          '';
          config = {
            Cmd = [ "/bin/bash" ];
            Entrypoint = [ "${entrypoint}/bin/entrypoint" ];
            WorkingDir = "/openwrt";
            Env = [
              "PATH=/bin:/usr/bin:${pkgs.lib.makeBinPath buildTools}"
              "FORCE_UNSAFE_CONFIGURE=1"
              "LANG=C.UTF-8"
              "HOME=/root"
              "SSL_CERT_FILE=${pkgs.cacert}/etc/ssl/certs/ca-bundle.crt"
            ];
            Volumes = { "/openwrt" = { }; };
          };
        };
      };

      devShells.${system}.default = pkgs.mkShell {
        buildInputs = buildTools;
        FORCE_UNSAFE_CONFIGURE = "1";
        shellHook = ''
          echo "OpenWrt Build Environment"
        '';
      };
    };
}
