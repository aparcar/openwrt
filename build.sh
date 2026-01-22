#!/bin/bash
#
# OpenWrt PoC Build System - Local Build Script
#
# Usage:
#   ./build.sh                    # Full build (toolchain + firmware)
#   ./build.sh toolchain          # Build toolchain only
#   ./build.sh firmware           # Build firmware only (requires toolchain)
#   ./build.sh shell              # Interactive shell with toolchain
#   ./build.sh clean              # Clean build artifacts
#
# Environment variables:
#   TARGET          - Target to build (default: mediatek-filogic)
#   PROFILE         - Device profile (default: openwrt_one)
#   JOBS            - Number of parallel jobs (default: $(nproc))
#   VERBOSE         - Set to 1 for verbose output
#   SKIP_DOCKER     - Set to 1 to run without Docker (requires local deps)
#   USE_CCACHE      - Set to 1 to enable ccache for compilation
#

set -e

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$SCRIPT_DIR"
CONFIG_FILE="${CONFIG_FILE:-${SCRIPT_DIR}/owrt/config.yaml}"
USE_CONFIG_YAML=0

# Default values (can be overridden by config.yaml or environment)
TARGET="${TARGET:-armsr-armv8}"
PROFILE="${PROFILE:-generic}"
JOBS="${JOBS:-$(nproc)}"
VERBOSE="${VERBOSE:-0}"
USE_CCACHE="${USE_CCACHE:-0}"
CCACHE_DIR="${CCACHE_DIR:-${SCRIPT_DIR}/build/.ccache}"

# Load config.yaml if it exists (unless TARGET/PROFILE are explicitly set via env)
load_config_yaml() {
    if [[ -f "${CONFIG_FILE}" ]]; then
        USE_CONFIG_YAML=1

        # Only load target from config.yaml if not set via environment
        if [[ -z "${TARGET_ENV_SET:-}" ]]; then
            local yaml_target
            yaml_target=$(python3 -c "import yaml; print(yaml.safe_load(open('${CONFIG_FILE}'))['target'])" 2>/dev/null || true)
            if [[ -n "${yaml_target}" ]]; then
                TARGET="${yaml_target}"
            fi
        fi

        # Only load profile from config.yaml if not set via environment
        if [[ -z "${PROFILE_ENV_SET:-}" ]]; then
            local yaml_profile
            yaml_profile=$(python3 -c "import yaml; print(yaml.safe_load(open('${CONFIG_FILE}')).get('profile', 'generic'))" 2>/dev/null || true)
            if [[ -n "${yaml_profile}" ]]; then
                PROFILE="${yaml_profile}"
            fi
        fi

        # Load build options
        local yaml_jobs yaml_verbose
        yaml_jobs=$(python3 -c "import yaml; c=yaml.safe_load(open('${CONFIG_FILE}')); print(c.get('build', {}).get('jobs', ''))" 2>/dev/null || true)
        yaml_verbose=$(python3 -c "import yaml; c=yaml.safe_load(open('${CONFIG_FILE}')); print('1' if c.get('build', {}).get('verbose', False) else '')" 2>/dev/null || true)

        if [[ -n "${yaml_jobs}" && -z "${JOBS_ENV_SET:-}" ]]; then
            JOBS="${yaml_jobs}"
        fi
        if [[ -n "${yaml_verbose}" && "${VERBOSE}" != "1" ]]; then
            VERBOSE=1
        fi
    fi
}

# Check if env vars were explicitly set (before loading config)
[[ -n "${TARGET+x}" ]] && [[ "${TARGET}" != "armsr-armv8" ]] && TARGET_ENV_SET=1
[[ -n "${PROFILE+x}" ]] && [[ "${PROFILE}" != "generic" ]] && PROFILE_ENV_SET=1
[[ -n "${JOBS+x}" ]] && JOBS_ENV_SET=1

# Load config.yaml
load_config_yaml

# Compute hashes using CLI (single source of truth for CI and local builds)
# These are computed early so we know which Docker images to use
get_base_hash() {
    cd "${SCRIPT_DIR}" && python3 -m owrt base hash 2>/dev/null || \
        sha256sum "${SCRIPT_DIR}/docker/Dockerfile" | cut -c1-12
}

get_toolchain_hash() {
    local target="$1"
    cd "${SCRIPT_DIR}" && python3 -m owrt toolchain hash "${target}" -f hash 2>/dev/null || \
        echo "unknown"
}

# Docker image names (computed lazily to allow TARGET override)
get_base_image() {
    echo "openwrt-poc-base:$(get_base_hash)"
}

get_toolchain_image() {
    echo "openwrt-toolchain:${TARGET}-$(get_toolchain_hash "${TARGET}")"
}

# Build directories
BUILD_DIR="${SCRIPT_DIR}/build"
DL_DIR="${SCRIPT_DIR}/dl"
OUTPUT_DIR="${SCRIPT_DIR}/output"
TOOLCHAIN_DIR="${BUILD_DIR}/toolchain"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[OK]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check if Docker is available
check_docker() {
    if [[ "${SKIP_DOCKER}" == "1" ]]; then
        return 1
    fi
    if command -v docker &> /dev/null; then
        return 0
    else
        log_warn "Docker not found, running without containerization"
        return 1
    fi
}

# Get ccache flag for Python CLI
get_ccache_flag() {
    if [[ "${USE_CCACHE}" == "1" ]]; then
        echo "--ccache"
    fi
}

# Get ccache Docker options (volume mount and env vars)
get_ccache_docker_opts() {
    if [[ "${USE_CCACHE}" == "1" ]]; then
        mkdir -p "${CCACHE_DIR}"
        echo "-v ${CCACHE_DIR}:/ccache -e CCACHE_DIR=/ccache -e CCACHE_BASEDIR=/openwrt"
    fi
}

# Build the base Docker image
build_base_image() {
    log_info "Building base Docker image..."
    docker build \
        -t "${BASE_IMAGE}" \
        -f "${SCRIPT_DIR}/docker/Dockerfile" \
        "${SCRIPT_DIR}/docker"
    log_success "Base image built: ${BASE_IMAGE}"
}

# Check if base image exists
ensure_base_image() {
    if ! docker image inspect "${BASE_IMAGE}" &> /dev/null; then
        build_base_image
    else
        log_info "Using existing base image: ${BASE_IMAGE}"
    fi
}

# Build the toolchain
build_toolchain() {
    log_info "Building toolchain for ${TARGET}..."

    mkdir -p "${DL_DIR}" "${TOOLCHAIN_DIR}"

    local verbose_flag=""
    if [[ "${VERBOSE}" == "1" ]]; then
        verbose_flag="-v"
    fi

    if check_docker; then
        ensure_base_image

        # Create directories before mounting (prevents Docker creating them as root)
        mkdir -p "${BUILD_DIR}" "${DL_DIR}"

        docker run --rm \
            -u "$(id -u):$(id -g)" \
            -v "${ROOT_DIR}:/openwrt" \
            -v "${BUILD_DIR}:/build" \
            -w /openwrt \
            -e BUILD_DIR=/build \
            -e DL_DIR=/build/dl \
            "${BASE_IMAGE}" \
            python3 -m owrt ${verbose_flag} -j "${JOBS}" toolchain build "${TARGET}"
    else
        cd "${SCRIPT_DIR}"
        BUILD_DIR="${BUILD_DIR}" DL_DIR="${DL_DIR}" \
            python3 -m owrt ${verbose_flag} -j "${JOBS}" toolchain build "${TARGET}"
    fi

    log_success "Toolchain built successfully"
}

# Build toolchain Docker image (with pre-built toolchain)
build_toolchain_image() {
    log_info "Building toolchain Docker image..."

    ensure_base_image

    # Create temporary Dockerfile
    local dockerfile=$(mktemp)
    cat > "${dockerfile}" <<EOF
ARG BASE_IMAGE
FROM \${BASE_IMAGE}

ARG TARGET
COPY --chown=builder:builder build/toolchain/\${TARGET} /build/toolchain/\${TARGET}

ENV PATH="/build/toolchain/\${TARGET}/bin:\${PATH}"
EOF

    docker build \
        --build-arg BASE_IMAGE="${BASE_IMAGE}" \
        --build-arg TARGET="${TARGET}" \
        -f "${dockerfile}" \
        -t "${TOOLCHAIN_IMAGE}" \
        "${SCRIPT_DIR}"

    rm -f "${dockerfile}"
    log_success "Toolchain image built: ${TOOLCHAIN_IMAGE}"
}

# Check if toolchain is built
ensure_toolchain() {
    local tc_bin="${TOOLCHAIN_DIR}/${TARGET}/bin"
    if [[ ! -d "${tc_bin}" ]]; then
        log_warn "Toolchain not found, building..."
        build_toolchain
    else
        log_info "Using existing toolchain: ${tc_bin}"
    fi
}

# Ensure toolchain Docker image exists
ensure_toolchain_image() {
    ensure_toolchain

    if ! docker image inspect "${TOOLCHAIN_IMAGE}" &> /dev/null; then
        build_toolchain_image
    else
        log_info "Using existing toolchain image: ${TOOLCHAIN_IMAGE}"
    fi
}

# Build host tools (apk, lua)
build_host_tools() {
    log_info "Building host tools..."

    mkdir -p "${DL_DIR}" "${BUILD_DIR}/host-tools" "${BUILD_DIR}/host-staging"

    local verbose_flag=""
    if [[ "${VERBOSE}" == "1" ]]; then
        verbose_flag="-v"
    fi

    if check_docker; then
        ensure_base_image

        docker run --rm \
            -u "$(id -u):$(id -g)" \
            -v "${ROOT_DIR}:/openwrt" \
            -v "${BUILD_DIR}:/build" \
            -w /openwrt \
            -e BUILD_DIR=/build \
            -e DL_DIR=/build/dl \
            "${BASE_IMAGE}" \
            python3 -m owrt ${verbose_flag} -j "${JOBS}" tool build-all -b /build
    else
        cd "${SCRIPT_DIR}"
        BUILD_DIR="${BUILD_DIR}" DL_DIR="${DL_DIR}" \
            python3 -m owrt ${verbose_flag} -j "${JOBS}" tool build-all -b "${BUILD_DIR}"
    fi

    log_success "Host tools built successfully"
}

# Check if host tools are built
ensure_host_tools() {
    local apk_bin="${BUILD_DIR}/host-staging/bin/apk"
    if [[ ! -f "${apk_bin}" ]]; then
        log_warn "Host tools not found, building..."
        build_host_tools
    else
        log_info "Using existing host tools: ${BUILD_DIR}/host-staging/bin"
    fi
}

# Build firmware
build_firmware() {
    log_info "Building firmware for ${TARGET} (profile: ${PROFILE})..."

    mkdir -p "${DL_DIR}" "${OUTPUT_DIR}"

    local verbose_flag=""
    if [[ "${VERBOSE}" == "1" ]]; then
        verbose_flag="-v"
    fi

    # Use config.yaml-based build if config file exists
    local config_flag=""
    if [[ "${USE_CONFIG_YAML}" == "1" ]]; then
        config_flag="-c ${CONFIG_FILE}"
        log_info "Using config.yaml for build configuration"
    fi

    if check_docker; then
        ensure_toolchain_image
        ensure_host_tools

        # Generate Ninja file
        log_info "Generating Ninja build file..."
        docker run --rm \
            -u "$(id -u):$(id -g)" \
            -v "${ROOT_DIR}:/openwrt" \
            -v "${BUILD_DIR}:/build" \
            -w /openwrt \
            -e BUILD_DIR=/build \
            "${TOOLCHAIN_IMAGE}" \
            python3 -m owrt ninja generate "${TARGET}" --profile "${PROFILE}"

        # Run Ninja build
        log_info "Running Ninja build..."
        local ccache_flag=$(get_ccache_flag)
        docker run --rm \
            -u "$(id -u):$(id -g)" \
            -v "${ROOT_DIR}:/openwrt" \
            -v "${BUILD_DIR}:/build" \
            -v "${OUTPUT_DIR}:/build/output" \
            $(get_ccache_docker_opts) \
            -w /openwrt \
            -e BUILD_DIR=/build \
            -e DL_DIR=/build/dl \
            -e OUTPUT_DIR=/build/output \
            "${TOOLCHAIN_IMAGE}" \
            python3 -m owrt ${verbose_flag} ${ccache_flag} -j "${JOBS}" ninja run "${TARGET}" --profile "${PROFILE}"
    else
        ensure_toolchain
        ensure_host_tools

        cd "${SCRIPT_DIR}"

        # Generate Ninja file
        log_info "Generating Ninja build file..."
        python3 -m owrt ninja generate "${TARGET}" --profile "${PROFILE}"

        # Run Ninja build
        log_info "Running Ninja build..."
        local ccache_flag=$(get_ccache_flag)
        BUILD_DIR="${BUILD_DIR}" DL_DIR="${DL_DIR}" OUTPUT_DIR="${OUTPUT_DIR}" CCACHE_DIR="${CCACHE_DIR}" \
            python3 -m owrt ${verbose_flag} ${ccache_flag} -j "${JOBS}" ninja run "${TARGET}" --profile "${PROFILE}"
    fi

    log_success "Firmware built successfully"
    log_info "Output: ${OUTPUT_DIR}"
}

# Build individual package(s)
# All packages are built with fakechroot isolation for maximum security
build_package() {
    local packages=("$@")

    if [[ ${#packages[@]} -eq 0 ]]; then
        log_error "Package name required: ./build.sh package <name> [name2] ..."
        exit 1
    fi

    log_info "Building packages: ${packages[*]} for ${TARGET}..."

    local verbose_flag=""
    if [[ "${VERBOSE}" == "1" ]]; then
        verbose_flag="-v"
    fi

    local ccache_flag=$(get_ccache_flag)

    if check_docker; then
        ensure_toolchain_image
        ensure_host_tools

        for pkg in "${packages[@]}"; do
            log_info "Building package: ${pkg}"
            docker run --rm \
                -u "$(id -u):$(id -g)" \
                -v "${ROOT_DIR}:/openwrt" \
                -v "${BUILD_DIR}:/build" \
                $(get_ccache_docker_opts) \
                -w /openwrt \
                -e BUILD_DIR=/build \
                -e DL_DIR=/build/dl \
                "${TOOLCHAIN_IMAGE}" \
                python3 -m owrt ${verbose_flag} ${ccache_flag} -j "${JOBS}" package "${TARGET}" "${pkg}"
        done
    else
        ensure_toolchain
        ensure_host_tools

        cd "${SCRIPT_DIR}"
        for pkg in "${packages[@]}"; do
            log_info "Building package: ${pkg}"
            BUILD_DIR="${BUILD_DIR}" DL_DIR="${DL_DIR}" CCACHE_DIR="${CCACHE_DIR}" \
                python3 -m owrt ${verbose_flag} ${ccache_flag} -j "${JOBS}" package "${TARGET}" "${pkg}"
        done
    fi

    log_success "Package(s) built successfully"
}

# Interactive shell
run_shell() {
    log_info "Starting interactive shell with toolchain..."

    if check_docker; then
        ensure_toolchain_image

        docker run -it --rm \
            -u "$(id -u):$(id -g)" \
            -v "${ROOT_DIR}:/openwrt" \
            -v "${BUILD_DIR}:/build" \
            -v "${OUTPUT_DIR}:/build/output" \
            -w /openwrt \
            -e BUILD_DIR=/build \
            -e DL_DIR=/build/dl \
            -e OUTPUT_DIR=/build/output \
            "${TOOLCHAIN_IMAGE}" \
            /bin/bash
    else
        ensure_toolchain

        export PATH="${TOOLCHAIN_DIR}/${TARGET}/bin:${PATH}"
        export BUILD_DIR="${BUILD_DIR}"
        export DL_DIR="${DL_DIR}"
        export OUTPUT_DIR="${OUTPUT_DIR}"

        log_info "Toolchain added to PATH. Type 'exit' to leave."
        cd "${SCRIPT_DIR}"
        exec bash
    fi
}

# Show build info
show_info() {
    echo ""
    log_info "Build configuration:"
    if [[ "${USE_CONFIG_YAML}" == "1" ]]; then
        echo "  Config:      ${CONFIG_FILE}"
    else
        echo "  Config:      (none - using defaults)"
    fi
    echo "  Target:      ${TARGET}"
    echo "  Profile:     ${PROFILE}"
    echo "  Jobs:        ${JOBS}"
    echo "  Build dir:   ${BUILD_DIR}"
    echo "  Download:    ${DL_DIR}"
    echo "  Output:      ${OUTPUT_DIR}"
    echo ""

    if check_docker; then
        echo "  Docker:      yes"
        echo "  Base image:  ${BASE_IMAGE}"
        echo "  TC image:    ${TOOLCHAIN_IMAGE}"
    else
        echo "  Docker:      no (native build)"
    fi

    if [[ "${USE_CCACHE}" == "1" ]]; then
        echo "  ccache:      enabled (${CCACHE_DIR})"
    else
        echo "  ccache:      disabled"
    fi
    echo ""

    # Check toolchain status
    local tc_gcc="${TOOLCHAIN_DIR}/${TARGET}/bin"/*-gcc
    if compgen -G "${tc_gcc}" > /dev/null 2>&1; then
        log_success "Toolchain: installed"
        ls ${TOOLCHAIN_DIR}/${TARGET}/bin/*-gcc 2>/dev/null | head -1 | xargs -r sh -c '$0 --version | head -1'
    else
        log_warn "Toolchain: not built"
    fi
    echo ""
}

# Clean build artifacts
clean() {
    log_info "Cleaning build artifacts..."

    local what="${1:-all}"

    case "${what}" in
        toolchain)
            rm -rf "${TOOLCHAIN_DIR}/${TARGET}"
            docker rmi "${TOOLCHAIN_IMAGE}" 2>/dev/null || true
            log_success "Toolchain cleaned"
            ;;
        tools)
            rm -rf "${BUILD_DIR}/host-tools" "${BUILD_DIR}/host-staging"
            log_success "Host tools cleaned"
            ;;
        build)
            rm -rf "${BUILD_DIR}/kernel" "${BUILD_DIR}/packages" "${BUILD_DIR}/staging" "${BUILD_DIR}/rootfs"
            rm -rf "${OUTPUT_DIR}"
            log_success "Build artifacts cleaned"
            ;;
        stamps)
            # Get architecture for target
            local arch
            arch=$(python3 -c "
import sys
sys.path.insert(0, '${SCRIPT_DIR}')
from owrt.config import Config
c = Config.load_target('${TARGET}')
print(c.arch)
" 2>/dev/null || echo "aarch64")
            # Clean target stamps (toolchain, kernel, image)
            rm -rf "${BUILD_DIR}/${TARGET}/stamp"
            # Clean package stamps (per-architecture)
            rm -rf "${BUILD_DIR}/packages/${arch}/stamp"
            # Clean APK packages and repos (both target and arch dirs)
            rm -rf "${BUILD_DIR}/apk-packages/${TARGET}" "${BUILD_DIR}/apk-packages/${arch}"
            rm -rf "${BUILD_DIR}/apk-repo/${TARGET}" "${BUILD_DIR}/apk-repo/${arch}"
            log_success "All stamps and APK directories cleaned for ${TARGET} (arch: ${arch})"
            ;;
        all)
            rm -rf "${BUILD_DIR}" "${OUTPUT_DIR}"
            docker rmi "${TOOLCHAIN_IMAGE}" 2>/dev/null || true
            log_success "All build artifacts cleaned"
            ;;
        docker)
            docker rmi "${BASE_IMAGE}" "${TOOLCHAIN_IMAGE}" 2>/dev/null || true
            log_success "Docker images cleaned"
            ;;
        downloads)
            rm -rf "${DL_DIR}"
            log_success "Downloads cleaned"
            ;;
        package)
            local pkg_name="$2"
            if [[ -z "${pkg_name}" ]]; then
                log_error "Package name required: clean package <name>"
                exit 1
            fi
            # Get architecture for target
            local arch
            arch=$(python3 -c "
import sys
sys.path.insert(0, '${SCRIPT_DIR}')
from owrt.config import Config
c = Config.load_target('${TARGET}')
print(c.arch)
" 2>/dev/null || echo "aarch64")
            # Clean package build directory
            rm -rf "${BUILD_DIR}/packages/${arch}/${pkg_name}"
            # Clean package stamps
            rm -rf "${BUILD_DIR}/packages/${arch}/stamp/${pkg_name}."*
            # Clean APK files
            rm -rf "${BUILD_DIR}/apk-packages/${TARGET}/${pkg_name}-"*.apk
            rm -rf "${BUILD_DIR}/apk-packages/${arch}/${pkg_name}-"*.apk
            rm -rf "${BUILD_DIR}/apk-repo/${TARGET}/${arch}/${pkg_name}-"*.apk
            rm -rf "${BUILD_DIR}/apk-repo/${arch}/${arch}/${pkg_name}-"*.apk
            log_success "Package ${pkg_name} cleaned for ${TARGET} (arch: ${arch})"
            ;;
        rootfs)
            rm -rf "${BUILD_DIR}/rootfs/${TARGET}"
            rm -rf "${BUILD_DIR}/${TARGET}/stamp/image.stamp"
            rm -rf "${BUILD_DIR}/${TARGET}/stamp/apk-index.stamp"
            log_success "Rootfs cleaned for ${TARGET}"
            ;;
        *)
            log_error "Unknown clean target: ${what}"
            log_info "Valid targets: toolchain, tools, build, stamps, package <name>, rootfs, all, docker, downloads"
            exit 1
            ;;
    esac
}

# Print usage
usage() {
    cat <<EOF
OpenWrt PoC Build System - Local Build Script

Usage: $0 [command] [options]

Commands:
  all             Full build: toolchain + tools + firmware (default)
  toolchain       Build the cross-compilation toolchain
  tools           Build host tools (lua, apk-tools)
  package <name>  Build individual package(s)
  firmware        Build firmware (requires toolchain + tools)
  shell           Start interactive shell with toolchain
  info            Show build configuration
  clean [what]    Clean build artifacts (toolchain|tools|build|stamps|package <name>|rootfs|all|docker|downloads)
  help            Show this help message

Options:
  -t, --target    Target name (default: armsr-armv8)
  -p, --profile   Device profile (default: generic)
  -j, --jobs      Parallel jobs (default: \$(nproc))
  -v, --verbose   Enable verbose output
  --ccache        Enable ccache for compilation

Environment Variables:
  TARGET          Target to build (same as -t)
  PROFILE         Device profile (same as -p)
  JOBS            Parallel jobs (same as -j)
  VERBOSE         Set to 1 for verbose output
  SKIP_DOCKER     Set to 1 to run without Docker
  USE_CCACHE      Set to 1 to enable ccache (same as --ccache)
  CCACHE_DIR      ccache directory (default: build/.ccache)

Examples:
  $0                                         # Full build with defaults (armsr-armv8)
  $0 toolchain                               # Build toolchain only
  $0 -t mediatek-filogic firmware            # Build firmware for OpenWrt One
  $0 -v all                                  # Verbose full build
  $0 package libmnl nftables                 # Build packages
  $0 shell                                   # Interactive shell with toolchain
  $0 clean all                               # Clean everything

Security:
  All packages are built with fakechroot isolation for maximum security.
  Each package runs in an isolated filesystem with dependencies installed
  via APK. Packages cannot see or modify other packages' build artifacts.

EOF
}

# Parse command line arguments
# Stops parsing options after the first positional argument (command)
parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            -t|--target)
                TARGET="$2"
                shift 2
                ;;
            -p|--profile)
                PROFILE="$2"
                shift 2
                ;;
            -j|--jobs)
                JOBS="$2"
                shift 2
                ;;
            -v|--verbose)
                VERBOSE=1
                shift
                ;;
            --ccache)
                USE_CCACHE=1
                shift
                ;;
            -h|--help)
                usage
                exit 0
                ;;
            -*)
                # If we've seen a command, pass unknown options through to it
                if [[ ${#POSITIONAL[@]} -gt 0 ]]; then
                    POSITIONAL+=("$1")
                    shift
                else
                    log_error "Unknown option: $1"
                    usage
                    exit 1
                fi
                ;;
            *)
                # Positional argument (command or command args)
                POSITIONAL+=("$1")
                shift
                ;;
        esac
    done
}

# Compute Docker image names (must be called after TARGET is finalized)
compute_image_names() {
    BASE_IMAGE="openwrt-poc-base:$(get_base_hash)"
    TOOLCHAIN_IMAGE="openwrt-toolchain:${TARGET}-$(get_toolchain_hash "${TARGET}")"
}

# Main entry point
main() {
    POSITIONAL=()
    parse_args "$@"
    set -- "${POSITIONAL[@]}"

    # Compute image names after TARGET is finalized
    compute_image_names

    local cmd="${1:-all}"
    shift || true

    case "${cmd}" in
        all)
            show_info
            build_toolchain
            build_host_tools
            build_firmware
            ;;
        toolchain)
            show_info
            build_toolchain
            ;;
        tools)
            show_info
            build_host_tools
            ;;
        package)
            show_info
            build_package "$@"
            ;;
        firmware)
            show_info
            build_firmware
            ;;
        shell)
            run_shell
            ;;
        info)
            show_info
            ;;
        clean)
            clean "$@"
            ;;
        help|--help|-h)
            usage
            ;;
        *)
            log_error "Unknown command: ${cmd}"
            usage
            exit 1
            ;;
    esac
}

main "$@"
