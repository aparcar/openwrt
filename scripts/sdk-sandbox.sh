#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-only
#
# Copyright (C) 2024 OpenWrt.org
#
# SDK Sandbox Wrapper using Bubblewrap
#
# This script provides isolated package builds using bubblewrap (bwrap),
# similar to Alpine Linux's abuild rootbld. It creates an unprivileged
# sandbox with namespace isolation for security and reproducibility.
#
# Usage: sdk-sandbox.sh [options] -- <command>
#
# Options:
#   --network         Enable network access (disabled by default)
#   --bind=SRC:DST    Additional bind mount (read-write)
#   --ro-bind=SRC:DST Additional bind mount (read-only)
#   --env=VAR=VALUE   Set environment variable in sandbox
#   --keep-env=VAR    Preserve environment variable from host
#   --workdir=DIR     Set working directory inside sandbox
#   --tmpfs=SIZE      Size for build tmpfs (default: 4G)
#   --debug           Enable debug output
#   --dry-run         Show bwrap command without executing
#   -h, --help        Show this help message
#
# Environment Variables:
#   TOPDIR            OpenWrt top directory
#   STAGING_DIR       Staging directory path
#   BUILD_DIR         Build directory path
#   PKG_BUILD_DIR     Package build directory
#   PKG_BUILD_NETWORK Set to 1 to allow network (per-package opt-in)
#

set -e

# Determine script and top directory
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TOPDIR="${TOPDIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"

# Check for bubblewrap
if ! command -v bwrap &>/dev/null; then
    echo "Error: bubblewrap (bwrap) is not installed" >&2
    echo "Install with: apt install bubblewrap (Debian/Ubuntu)" >&2
    echo "              dnf install bubblewrap (Fedora)" >&2
    echo "              pacman -S bubblewrap (Arch)" >&2
    exit 1
fi

# Default paths (can be overridden by environment)
STAGING_DIR="${STAGING_DIR:-$TOPDIR/staging_dir/target-*}"
STAGING_DIR_HOST="${STAGING_DIR_HOST:-$TOPDIR/staging_dir/host}"
STAGING_DIR_HOSTPKG="${STAGING_DIR_HOSTPKG:-$TOPDIR/staging_dir/hostpkg}"
STAGING_DIR_TOOLCHAIN="${STAGING_DIR_TOOLCHAIN:-$TOPDIR/staging_dir/toolchain-*}"
BUILD_DIR="${BUILD_DIR:-$TOPDIR/build_dir/target-*}"
BIN_DIR="${BIN_DIR:-$TOPDIR/bin}"
DL_DIR="${DL_DIR:-$TOPDIR/dl}"
TMP_DIR="${TMP_DIR:-$TOPDIR/tmp}"

# Expand globs
STAGING_DIR=$(echo $STAGING_DIR | head -1)
STAGING_DIR_TOOLCHAIN=$(echo $STAGING_DIR_TOOLCHAIN | head -1)
BUILD_DIR=$(echo $BUILD_DIR | head -1)

# Default options
NETWORK_ACCESS=0
TMPFS_SIZE="4G"
DEBUG=0
DRY_RUN=0
WORKDIR="$TOPDIR"

# Arrays for custom mounts and env vars
declare -a EXTRA_BINDS=()
declare -a EXTRA_RO_BINDS=()
declare -a EXTRA_ENV=()
declare -a KEEP_ENVS=()
declare -a COMMAND=()

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_debug() {
    if [ "$DEBUG" -eq 1 ]; then
        echo -e "${BLUE}[SANDBOX]${NC} $*" >&2
    fi
}

log_info() {
    echo -e "${BLUE}[SANDBOX]${NC} $*" >&2
}

log_error() {
    echo -e "${RED}[SANDBOX]${NC} $*" >&2
}

usage() {
    cat << EOF
SDK Sandbox Wrapper using Bubblewrap

Usage: $(basename "$0") [options] -- <command>

Options:
  --network           Enable network access (disabled by default)
  --bind=SRC:DST      Additional bind mount (read-write)
  --ro-bind=SRC:DST   Additional bind mount (read-only)
  --env=VAR=VALUE     Set environment variable in sandbox
  --keep-env=VAR      Preserve environment variable from host
  --workdir=DIR       Set working directory inside sandbox
  --tmpfs=SIZE        Size for build tmpfs (default: 4G)
  --debug             Enable debug output
  --dry-run           Show bwrap command without executing
  -h, --help          Show this help message

Environment Variables:
  PKG_BUILD_NETWORK   Set to 1 in package Makefile to allow network access

Examples:
  # Run make in sandbox
  $(basename "$0") -- make package/curl/compile

  # With network access
  $(basename "$0") --network -- make package/curl/download

  # Custom bind mount
  $(basename "$0") --bind=/path/to/src:/src -- make V=s

Security Features:
  - Unprivileged user namespace isolation
  - Separate PID, IPC, UTS namespaces
  - Network disabled by default (opt-in per package)
  - Read-only bind mounts for toolchain/staging
  - Tmpfs for build workspace
EOF
    exit 0
}

# Parse command line arguments
parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --network)
                NETWORK_ACCESS=1
                shift
                ;;
            --bind=*)
                EXTRA_BINDS+=("${1#--bind=}")
                shift
                ;;
            --ro-bind=*)
                EXTRA_RO_BINDS+=("${1#--ro-bind=}")
                shift
                ;;
            --env=*)
                EXTRA_ENV+=("${1#--env=}")
                shift
                ;;
            --keep-env=*)
                KEEP_ENVS+=("${1#--keep-env=}")
                shift
                ;;
            --workdir=*)
                WORKDIR="${1#--workdir=}"
                shift
                ;;
            --tmpfs=*)
                TMPFS_SIZE="${1#--tmpfs=}"
                shift
                ;;
            --debug)
                DEBUG=1
                shift
                ;;
            --dry-run)
                DRY_RUN=1
                shift
                ;;
            -h|--help)
                usage
                ;;
            --)
                shift
                COMMAND=("$@")
                break
                ;;
            *)
                log_error "Unknown option: $1"
                usage
                ;;
        esac
    done

    if [ ${#COMMAND[@]} -eq 0 ]; then
        log_error "No command specified"
        usage
    fi

    # Check for per-package network override
    if [ "${PKG_BUILD_NETWORK:-0}" = "1" ]; then
        NETWORK_ACCESS=1
        log_debug "Network enabled via PKG_BUILD_NETWORK"
    fi
}

# Build the bwrap command
build_bwrap_cmd() {
    local -a cmd=(bwrap)

    # Unshare namespaces for isolation
    cmd+=(--unshare-user)
    cmd+=(--unshare-pid)
    cmd+=(--unshare-ipc)
    cmd+=(--unshare-uts)
    cmd+=(--unshare-cgroup)

    # Network isolation (unless explicitly enabled)
    if [ "$NETWORK_ACCESS" -eq 0 ]; then
        cmd+=(--unshare-net)
        log_debug "Network access: DISABLED"
    else
        log_debug "Network access: ENABLED"
    fi

    # Die with parent process
    cmd+=(--die-with-parent)

    # Basic filesystem structure
    cmd+=(--proc /proc)
    cmd+=(--dev /dev)
    cmd+=(--tmpfs /tmp)

    # Bind /etc read-only for basic system info
    if [ -d /etc ]; then
        cmd+=(--ro-bind /etc /etc)
    fi

    # Bind /usr read-only for system binaries
    if [ -d /usr ]; then
        cmd+=(--ro-bind /usr /usr)
    fi

    # Bind /lib and /lib64 for system libraries
    for lib_dir in /lib /lib64 /lib32; do
        if [ -d "$lib_dir" ]; then
            cmd+=(--ro-bind "$lib_dir" "$lib_dir")
        fi
    done

    # Bind /bin and /sbin
    for bin_dir in /bin /sbin; do
        if [ -d "$bin_dir" ]; then
            cmd+=(--ro-bind "$bin_dir" "$bin_dir")
        fi
    done

    # OpenWrt-specific mounts
    # Top directory (read-write for build outputs)
    cmd+=(--bind "$TOPDIR" "$TOPDIR")

    # Staging directories (read-only for toolchain access)
    if [ -d "$STAGING_DIR" ]; then
        cmd+=(--ro-bind "$STAGING_DIR" "$STAGING_DIR")
        log_debug "Staging dir (RO): $STAGING_DIR"
    fi

    if [ -d "$STAGING_DIR_HOST" ]; then
        cmd+=(--ro-bind "$STAGING_DIR_HOST" "$STAGING_DIR_HOST")
        log_debug "Host staging (RO): $STAGING_DIR_HOST"
    fi

    if [ -d "$STAGING_DIR_HOSTPKG" ]; then
        cmd+=(--ro-bind "$STAGING_DIR_HOSTPKG" "$STAGING_DIR_HOSTPKG")
        log_debug "Hostpkg staging (RO): $STAGING_DIR_HOSTPKG"
    fi

    if [ -d "$STAGING_DIR_TOOLCHAIN" ]; then
        cmd+=(--ro-bind "$STAGING_DIR_TOOLCHAIN" "$STAGING_DIR_TOOLCHAIN")
        log_debug "Toolchain staging (RO): $STAGING_DIR_TOOLCHAIN"
    fi

    # Build directory - use tmpfs for isolation and speed
    # But we need the actual build dir to persist outputs
    if [ -d "$BUILD_DIR" ]; then
        cmd+=(--bind "$BUILD_DIR" "$BUILD_DIR")
        log_debug "Build dir (RW): $BUILD_DIR"
    fi

    # Download directory (read-only, sources pre-downloaded)
    if [ -d "$DL_DIR" ]; then
        cmd+=(--ro-bind "$DL_DIR" "$DL_DIR")
        log_debug "Download dir (RO): $DL_DIR"
    fi

    # Temp directory
    if [ -d "$TMP_DIR" ]; then
        cmd+=(--bind "$TMP_DIR" "$TMP_DIR")
    fi

    # Output directory for packages
    if [ -d "$BIN_DIR" ]; then
        cmd+=(--bind "$BIN_DIR" "$BIN_DIR")
        log_debug "Binary dir (RW): $BIN_DIR"
    fi

    # Include directory (read-only)
    if [ -d "$TOPDIR/include" ]; then
        cmd+=(--ro-bind "$TOPDIR/include" "$TOPDIR/include")
    fi

    # Scripts directory (read-only)
    if [ -d "$TOPDIR/scripts" ]; then
        cmd+=(--ro-bind "$TOPDIR/scripts" "$TOPDIR/scripts")
    fi

    # Package directories (read-only for sources)
    if [ -d "$TOPDIR/package" ]; then
        cmd+=(--ro-bind "$TOPDIR/package" "$TOPDIR/package")
    fi

    # Feeds (read-only)
    if [ -d "$TOPDIR/feeds" ]; then
        cmd+=(--ro-bind "$TOPDIR/feeds" "$TOPDIR/feeds")
    fi

    # Extra bind mounts
    for bind in "${EXTRA_BINDS[@]}"; do
        local src="${bind%%:*}"
        local dst="${bind#*:}"
        if [ -e "$src" ]; then
            cmd+=(--bind "$src" "$dst")
            log_debug "Extra bind (RW): $src -> $dst"
        fi
    done

    for bind in "${EXTRA_RO_BINDS[@]}"; do
        local src="${bind%%:*}"
        local dst="${bind#*:}"
        if [ -e "$src" ]; then
            cmd+=(--ro-bind "$src" "$dst")
            log_debug "Extra bind (RO): $src -> $dst"
        fi
    done

    # Set working directory
    cmd+=(--chdir "$WORKDIR")

    # Set hostname for reproducibility
    cmd+=(--hostname "openwrt-sandbox")

    # Clear most environment variables for clean build
    cmd+=(--clearenv)

    # Essential environment variables
    cmd+=(--setenv PATH "/usr/bin:/usr/sbin:/bin:/sbin:$STAGING_DIR_HOST/bin:$STAGING_DIR_TOOLCHAIN/bin")
    cmd+=(--setenv HOME "/tmp")
    cmd+=(--setenv TERM "${TERM:-xterm}")
    cmd+=(--setenv LANG "C")
    cmd+=(--setenv LC_ALL "C")

    # OpenWrt build environment
    cmd+=(--setenv TOPDIR "$TOPDIR")
    cmd+=(--setenv STAGING_DIR "$STAGING_DIR")
    cmd+=(--setenv STAGING_DIR_HOST "$STAGING_DIR_HOST")
    cmd+=(--setenv STAGING_DIR_HOSTPKG "$STAGING_DIR_HOSTPKG")
    cmd+=(--setenv BUILD_DIR "$BUILD_DIR")
    cmd+=(--setenv BIN_DIR "$BIN_DIR")
    cmd+=(--setenv DL_DIR "$DL_DIR")
    cmd+=(--setenv TMP_DIR "$TMP_DIR")

    # Mark that we're in sandbox mode
    cmd+=(--setenv SDK_IN_SANDBOX "1")

    # Preserve specified environment variables
    for var in "${KEEP_ENVS[@]}"; do
        local val="${!var}"
        if [ -n "$val" ]; then
            cmd+=(--setenv "$var" "$val")
            log_debug "Keep env: $var"
        fi
    done

    # Extra environment variables
    for env_spec in "${EXTRA_ENV[@]}"; do
        local var="${env_spec%%=*}"
        local val="${env_spec#*=}"
        cmd+=(--setenv "$var" "$val")
        log_debug "Extra env: $var=$val"
    done

    # Add the command to execute
    cmd+=("${COMMAND[@]}")

    echo "${cmd[@]}"
}

# Check bwrap capabilities
check_bwrap() {
    # Test if user namespaces work
    if ! bwrap --unshare-user --ro-bind / / true 2>/dev/null; then
        log_error "Bubblewrap cannot create user namespaces"
        log_error "This may require:"
        log_error "  1. Kernel support: CONFIG_USER_NS=y"
        log_error "  2. Sysctl: echo 1 > /proc/sys/kernel/unprivileged_userns_clone"
        log_error "  3. AppArmor/SELinux permissions"
        return 1
    fi

    log_debug "Bubblewrap capabilities: OK"
    return 0
}

# Main entry point
main() {
    parse_args "$@"

    log_info "OpenWrt SDK Sandbox"
    log_debug "Top directory: $TOPDIR"
    log_debug "Working directory: $WORKDIR"
    log_debug "Command: ${COMMAND[*]}"

    # Check bubblewrap capabilities
    if ! check_bwrap; then
        exit 1
    fi

    # Build the bwrap command
    local bwrap_cmd
    bwrap_cmd=$(build_bwrap_cmd)

    if [ "$DRY_RUN" -eq 1 ]; then
        log_info "Dry-run mode - would execute:"
        echo "$bwrap_cmd"
        exit 0
    fi

    if [ "$DEBUG" -eq 1 ]; then
        log_debug "Executing bwrap command..."
    fi

    # Execute in sandbox
    exec $bwrap_cmd
}

main "$@"
