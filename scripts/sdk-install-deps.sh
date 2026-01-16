#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-only
#
# Copyright (C) 2024 OpenWrt.org
#
# SDK Prebuilt Dependency Installer
#
# This script installs prebuilt package dependencies into the staging
# directory, allowing individual packages to be built without compiling
# all transitive dependencies from source.
#
# Similar to Alpine Linux, this tool prefers -dev packages (development
# headers and libraries) over full runtime packages when available.
#
# Usage: sdk-install-deps.sh [options] <package-name|package-path>
#
# Options:
#   -r, --recursive     Install transitive dependencies (default: direct only)
#   -d, --dev-only      Only install -dev packages (skip runtime)
#   -f, --force         Reinstall even if already present
#   -n, --dry-run       Show what would be installed without installing
#   -v, --verbose       Verbose output
#   -h, --help          Show this help message
#
# Sources (checked in order):
#   1. Local cache: bin/packages/<arch>/
#   2. Configured repositories from repositories.conf
#
# Development Package Support:
#   The installer automatically prefers <pkg>-dev packages when available.
#   These contain only development files (headers, .a, .pc) needed for builds.
#

set -e

TOPDIR="${TOPDIR:-$(cd "$(dirname "$0")/.." && pwd)}"
STAGING_DIR="${STAGING_DIR:-$TOPDIR/staging_dir/target-*}"
PKG_INFO_DIR="${PKG_INFO_DIR:-$STAGING_DIR/pkginfo}"
BIN_DIR="${BIN_DIR:-$TOPDIR/bin}"
TMP_DIR="${TMP_DIR:-$TOPDIR/tmp}"
DL_DIR="${DL_DIR:-$TOPDIR/dl}"

# Expand staging dir glob
STAGING_DIR=$(echo $STAGING_DIR)

# Options
RECURSIVE=0
FORCE=0
DRY_RUN=0
VERBOSE=0
DEV_ONLY=0

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${BLUE}[INFO]${NC} $*"
}

log_success() {
    echo -e "${GREEN}[OK]${NC} $*"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $*"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $*" >&2
}

log_verbose() {
    if [ "$VERBOSE" -eq 1 ]; then
        echo -e "${BLUE}[DEBUG]${NC} $*"
    fi
}

usage() {
    cat << EOF
SDK Prebuilt Dependency Installer

Usage: $(basename "$0") [options] <package-name|package-path>

Options:
  -r, --recursive     Install transitive dependencies (default: direct only)
  -d, --dev-only      Only install -dev packages (skip runtime packages)
  -f, --force         Reinstall even if already present
  -n, --dry-run       Show what would be installed without installing
  -v, --verbose       Verbose output
  -h, --help          Show this help message

Examples:
  $(basename "$0") curl                    # Install deps for curl (prefers -dev)
  $(basename "$0") -d libopenssl           # Install only libopenssl-dev
  $(basename "$0") -r nginx                # Install all transitive deps for nginx
  $(basename "$0") package/network/utils/curl  # Use package path

Development Packages (-dev):
  Similar to Alpine Linux, this tool prefers -dev packages when available.
  -dev packages contain only development files (headers, static libs, .pc files)
  needed for compiling other packages, not runtime libraries.

Sources checked (in order):
  1. Local cache: bin/packages/<arch>/
  2. Configured repositories from repositories.conf
EOF
    exit 0
}

# Parse command line arguments
parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            -r|--recursive)
                RECURSIVE=1
                shift
                ;;
            -d|--dev-only)
                DEV_ONLY=1
                shift
                ;;
            -f|--force)
                FORCE=1
                shift
                ;;
            -n|--dry-run)
                DRY_RUN=1
                shift
                ;;
            -v|--verbose)
                VERBOSE=1
                shift
                ;;
            -h|--help)
                usage
                ;;
            -*)
                log_error "Unknown option: $1"
                usage
                ;;
            *)
                PACKAGE="$1"
                shift
                ;;
        esac
    done

    if [ -z "$PACKAGE" ]; then
        log_error "No package specified"
        usage
    fi
}

# Get package name from path or name
resolve_package_name() {
    local pkg="$1"

    # If it's a path, extract the package name
    if [[ "$pkg" == *"/"* ]]; then
        # Try to find the Makefile
        local makefile=""
        if [ -f "$TOPDIR/$pkg/Makefile" ]; then
            makefile="$TOPDIR/$pkg/Makefile"
        elif [ -f "$pkg/Makefile" ]; then
            makefile="$pkg/Makefile"
        fi

        if [ -n "$makefile" ]; then
            # Extract PKG_NAME from Makefile
            pkg=$(grep -m1 "^PKG_NAME:" "$makefile" 2>/dev/null | cut -d'=' -f2 | tr -d ' ') || true
            if [ -z "$pkg" ]; then
                # Fall back to directory name
                pkg=$(basename "$1")
            fi
        else
            pkg=$(basename "$1")
        fi
    fi

    echo "$pkg"
}

# Get dependencies for a package using DUMP mode
get_package_deps() {
    local pkg="$1"
    local deps=""

    log_verbose "Getting dependencies for: $pkg"

    # Try to find the package directory
    local pkg_dir=""
    for search_path in \
        "$TOPDIR/package"/*/"$pkg" \
        "$TOPDIR/package"/*/*/"$pkg" \
        "$TOPDIR/feeds"/*/"$pkg" \
        "$TOPDIR/feeds"/*/*/"$pkg" \
        "$TOPDIR/feeds"/*/*/*/"$pkg"
    do
        if [ -d "$search_path" ] && [ -f "$search_path/Makefile" ]; then
            pkg_dir="$search_path"
            break
        fi
    done

    if [ -z "$pkg_dir" ]; then
        log_warn "Cannot find package directory for: $pkg"
        return 1
    fi

    log_verbose "Found package at: $pkg_dir"

    # Use make DUMP=1 to get package info
    local dump_output
    dump_output=$(make -C "$pkg_dir" DUMP=1 2>/dev/null) || {
        log_warn "Failed to dump package info for: $pkg"
        return 1
    }

    # Extract dependencies from dump output
    deps=$(echo "$dump_output" | grep "^Depends:" | head -1 | cut -d':' -f2- | tr ',' '\n' | \
           sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' | \
           grep -v '^$' | \
           sed -e 's/^+//' -e 's/@.*//' | \
           grep -v '^@' | \
           sort -u)

    echo "$deps"
}

# Check if a dependency is already installed in staging
is_dep_installed() {
    local dep="$1"

    if [ -f "$PKG_INFO_DIR/$dep.version" ]; then
        return 0
    fi
    return 1
}

# Check if package name is a -dev package
is_dev_package() {
    [[ "$1" == *-dev ]]
}

# Get the -dev variant of a package name
get_dev_name() {
    local pkg="$1"
    if is_dev_package "$pkg"; then
        echo "$pkg"
    else
        echo "${pkg}-dev"
    fi
}

# Get the base (non-dev) package name
get_base_name() {
    local pkg="$1"
    if is_dev_package "$pkg"; then
        echo "${pkg%-dev}"
    else
        echo "$pkg"
    fi
}

# Find package in local cache
# Prefers -dev packages when available (Alpine Linux style)
find_local_package() {
    local pkg="$1"
    local pkg_file=""
    local dev_pkg=$(get_dev_name "$pkg")
    local base_pkg=$(get_base_name "$pkg")

    # Search in bin/packages/
    for arch_dir in "$BIN_DIR/packages"/*; do
        if [ -d "$arch_dir" ]; then
            for feed_dir in "$arch_dir"/*; do
                if [ -d "$feed_dir" ]; then
                    # First, try to find -dev package (preferred for SDK builds)
                    if [ "$DEV_ONLY" -eq 1 ] || [ "$pkg" != "$dev_pkg" ]; then
                        pkg_file=$(find "$feed_dir" -maxdepth 1 \
                            \( -name "${dev_pkg}_*.ipk" -o -name "${dev_pkg}-*.apk" \) \
                            -type f 2>/dev/null | head -1)
                        if [ -n "$pkg_file" ]; then
                            log_verbose "Found -dev package: $pkg_file"
                            echo "$pkg_file"
                            return 0
                        fi
                    fi

                    # If no -dev package and not dev-only mode, try base package
                    if [ "$DEV_ONLY" -eq 0 ]; then
                        pkg_file=$(find "$feed_dir" -maxdepth 1 \
                            \( -name "${base_pkg}_*.ipk" -o -name "${base_pkg}-*.apk" -o \
                               -name "${base_pkg}[0-9]*_*.ipk" -o -name "${base_pkg}[0-9]*-*.apk" \) \
                            -type f 2>/dev/null | head -1)
                        if [ -n "$pkg_file" ]; then
                            log_verbose "Found base package: $pkg_file"
                            echo "$pkg_file"
                            return 0
                        fi
                    fi
                fi
            done
        fi
    done

    return 1
}

# Download package from repository
# Prefers -dev packages when available
download_package() {
    local pkg="$1"
    local repos_conf="$TOPDIR/repositories.conf"
    local dev_pkg=$(get_dev_name "$pkg")
    local base_pkg=$(get_base_name "$pkg")

    if [ ! -f "$repos_conf" ]; then
        log_verbose "No repositories.conf found"
        return 1
    fi

    log_verbose "Searching repositories for: $pkg"

    # Determine which packages to search for
    local search_pkgs=""
    if [ "$DEV_ONLY" -eq 1 ]; then
        search_pkgs="$dev_pkg"
    else
        # Prefer -dev, fall back to base
        search_pkgs="$dev_pkg $base_pkg"
    fi

    # Parse repositories.conf and try each repo
    while IFS= read -r line; do
        # Skip comments and empty lines
        [[ "$line" =~ ^[[:space:]]*# ]] && continue
        [[ -z "${line// }" ]] && continue

        local repo_url="$line"
        log_verbose "Trying repository: $repo_url"

        # Try to download package index first
        local index_url="${repo_url}/Packages.gz"
        local index_file="$TMP_DIR/repo_index_$(echo "$repo_url" | md5sum | cut -d' ' -f1).gz"

        if wget -q -O "$index_file" "$index_url" 2>/dev/null; then
            # Search for packages in order of preference
            for search_pkg in $search_pkgs; do
                local pkg_path
                pkg_path=$(zcat "$index_file" 2>/dev/null | \
                    grep -A1 "^Package: $search_pkg\$" | \
                    grep "^Filename:" | \
                    head -1 | \
                    cut -d' ' -f2)

                if [ -n "$pkg_path" ]; then
                    local pkg_url="${repo_url}/${pkg_path}"
                    local pkg_file="$DL_DIR/$(basename "$pkg_path")"

                    log_info "Downloading: $pkg_url"
                    if wget -q -O "$pkg_file" "$pkg_url" 2>/dev/null; then
                        echo "$pkg_file"
                        rm -f "$index_file" 2>/dev/null
                        return 0
                    fi
                fi
            done
        fi

        rm -f "$index_file" 2>/dev/null
    done < "$repos_conf"

    return 1
}

# Extract package to staging directory
install_to_staging() {
    local pkg_file="$1"
    local pkg_name="$2"

    log_verbose "Installing $pkg_file to staging"

    local extract_dir="$TMP_DIR/sdk-extract-$$"
    mkdir -p "$extract_dir"

    # Determine package format
    case "$pkg_file" in
        *.ipk)
            # IPK is a gzipped ar archive
            cd "$extract_dir"
            ar -x "$pkg_file" 2>/dev/null || {
                log_error "Failed to extract IPK: $pkg_file"
                rm -rf "$extract_dir"
                return 1
            }

            # Extract data.tar.* to staging
            local data_tar=$(ls data.tar.* 2>/dev/null | head -1)
            if [ -n "$data_tar" ]; then
                tar -xf "$data_tar" -C "$STAGING_DIR" 2>/dev/null || true
            fi

            # Extract control info
            local control_tar=$(ls control.tar.* 2>/dev/null | head -1)
            if [ -n "$control_tar" ]; then
                mkdir -p "$extract_dir/control"
                tar -xf "$control_tar" -C "$extract_dir/control" 2>/dev/null || true

                # Get version from control file
                if [ -f "$extract_dir/control/control" ]; then
                    local version=$(grep "^Version:" "$extract_dir/control/control" | cut -d' ' -f2)
                    local abi_version=$(grep "^ABIVersion:" "$extract_dir/control/control" | cut -d' ' -f2)

                    mkdir -p "$PKG_INFO_DIR"
                    if [ -n "$abi_version" ]; then
                        echo "$abi_version" > "$PKG_INFO_DIR/$pkg_name.version"
                    elif [ -n "$version" ]; then
                        echo "$version" > "$PKG_INFO_DIR/$pkg_name.version"
                    fi
                fi
            fi
            ;;
        *.apk)
            # APK format - use tar directly
            tar -xf "$pkg_file" -C "$STAGING_DIR" 2>/dev/null || {
                log_error "Failed to extract APK: $pkg_file"
                rm -rf "$extract_dir"
                return 1
            }

            # Create version marker
            mkdir -p "$PKG_INFO_DIR"
            # Extract version from filename
            local version=$(basename "$pkg_file" | sed -n 's/.*-\([0-9].*\)\.apk$/\1/p')
            if [ -n "$version" ]; then
                echo "$version" > "$PKG_INFO_DIR/$pkg_name.version"
            fi
            ;;
        *)
            log_error "Unknown package format: $pkg_file"
            rm -rf "$extract_dir"
            return 1
            ;;
    esac

    rm -rf "$extract_dir"
    return 0
}

# Main installation logic
install_dependency() {
    local dep="$1"

    # Skip virtual dependencies (starting with @)
    if [[ "$dep" == @* ]]; then
        log_verbose "Skipping virtual dependency: $dep"
        return 0
    fi

    # Skip if already installed (unless force)
    if [ "$FORCE" -eq 0 ] && is_dep_installed "$dep"; then
        log_verbose "Already installed: $dep"
        return 0
    fi

    log_info "Installing dependency: $dep"

    if [ "$DRY_RUN" -eq 1 ]; then
        log_info "[DRY-RUN] Would install: $dep"
        return 0
    fi

    # Try local cache first
    local pkg_file
    pkg_file=$(find_local_package "$dep")

    if [ -z "$pkg_file" ]; then
        # Try to download from repository
        pkg_file=$(download_package "$dep")
    fi

    if [ -z "$pkg_file" ]; then
        log_warn "Cannot find package: $dep (will need to build from source)"
        return 1
    fi

    # Install to staging
    if install_to_staging "$pkg_file" "$dep"; then
        log_success "Installed: $dep"
        return 0
    else
        log_error "Failed to install: $dep"
        return 1
    fi
}

# Process all dependencies for a package
process_package() {
    local pkg="$1"
    local processed=""
    local to_process="$pkg"
    local failed=""

    log_info "Processing dependencies for: $pkg"

    while [ -n "$to_process" ]; do
        local current=$(echo "$to_process" | head -1)
        to_process=$(echo "$to_process" | tail -n +2)

        # Skip if already processed
        if echo "$processed" | grep -qx "$current"; then
            continue
        fi
        processed="$processed
$current"

        # Get dependencies
        local deps
        deps=$(get_package_deps "$current") || continue

        for dep in $deps; do
            # Clean up dependency name
            dep=$(echo "$dep" | sed -e 's/^+//' -e 's/[(<>=].*//')

            [ -z "$dep" ] && continue
            [[ "$dep" == @* ]] && continue

            # Install the dependency
            if ! install_dependency "$dep"; then
                failed="$failed $dep"
            fi

            # Add to processing queue if recursive
            if [ "$RECURSIVE" -eq 1 ]; then
                if ! echo "$processed" | grep -qx "$dep"; then
                    to_process="$to_process
$dep"
                fi
            fi
        done
    done

    if [ -n "$failed" ]; then
        log_warn "Some dependencies could not be installed:$failed"
        log_info "Build these from source with: make package/<name>/compile"
        return 1
    fi

    log_success "All dependencies installed for: $pkg"
    return 0
}

# Main entry point
main() {
    parse_args "$@"

    # Ensure directories exist
    mkdir -p "$TMP_DIR" "$DL_DIR" "$PKG_INFO_DIR"

    # Resolve package name
    local pkg_name
    pkg_name=$(resolve_package_name "$PACKAGE")

    log_info "SDK Dependency Installer"
    log_info "Package: $pkg_name"
    log_info "Staging: $STAGING_DIR"
    [ "$RECURSIVE" -eq 1 ] && log_info "Mode: Recursive"
    [ "$DEV_ONLY" -eq 1 ] && log_info "Mode: Dev-only (-dev packages only)"
    [ "$DRY_RUN" -eq 1 ] && log_info "Mode: Dry-run"
    echo ""

    process_package "$pkg_name"
}

main "$@"
