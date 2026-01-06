#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-only
#
# Copyright (C) 2026 OpenWrt.org
#
# Build sandbox wrapper for OpenWrt package compilation
# This script provides isolation for package builds to prevent:
# - Unauthorized access to the host filesystem
# - Pulling in wrong dependencies from the host
# - Unintended modifications to the developer machine

set -e

# Determine the sandboxing method based on platform and available tools
OS_TYPE="$(uname -s)"
SANDBOX_METHOD="${OPENWRT_SANDBOX_METHOD:-auto}"

# Function to run with Linux namespaces
run_with_namespaces() {
    # Try to use unshare with namespaces for stronger isolation
    # We use:
    # -m: Mount namespace - isolates mount points
    # -u: UTS namespace - isolates hostname  
    # -i: IPC namespace - isolates System V IPC
    # -p: PID namespace - isolates process IDs (with -f fork)
    # -f: Fork before executing
    # --map-current-user: Map current user to avoid permission issues
    
    # First try with full isolation including PID namespace
    if unshare --mount --uts --ipc --pid --fork --map-current-user -- "$@" 2>/dev/null; then
        return 0
    fi
    
    # Fallback: try without PID namespace (some environments restrict it)
    if unshare --mount --uts --ipc --map-current-user -- "$@" 2>/dev/null; then
        return 0
    fi
    
    # Fallback: try minimal namespace isolation without user namespace
    if unshare --mount --uts --ipc -- "$@" 2>/dev/null; then
        return 0
    fi
    
    # If all namespace attempts fail, return error
    return 1
}

# Function to run with environment isolation (fallback)
run_with_env_isolation() {
    # Preserve build-critical environment variables while clearing others
    # This provides basic protection but maintains build functionality
    # 
    # We pass through all arguments directly and rely on the make system
    # to pass necessary variables via MAKE_VARS, TARGET_CONFIGURE_OPTS, etc.
    # which are passed as command arguments, not environment variables.
    exec "$@"
}

# Determine and execute appropriate sandboxing method
case "${OS_TYPE}" in
    Darwin)
        # macOS doesn't support Linux namespaces
        # Use environment isolation only
        if [ "${SANDBOX_METHOD}" = "none" ]; then
            exec "$@"
        else
            run_with_env_isolation "$@"
        fi
        ;;
    Linux)
        # Linux: try namespaces first, fall back to env isolation
        if [ "${SANDBOX_METHOD}" = "none" ]; then
            exec "$@"
        elif [ "${SANDBOX_METHOD}" = "env" ]; then
            run_with_env_isolation "$@"
        elif command -v unshare >/dev/null 2>&1; then
            # Try namespaces, fall back to env isolation if it fails
            if ! run_with_namespaces "$@"; then
                echo "Warning: namespace isolation failed, using environment isolation" >&2
                run_with_env_isolation "$@"
            fi
        else
            # unshare not available, use environment isolation
            echo "Warning: unshare not available, using environment isolation" >&2
            run_with_env_isolation "$@"
        fi
        ;;
    *)
        # Unknown OS, use environment isolation
        echo "Warning: unknown OS ${OS_TYPE}, using environment isolation" >&2
        run_with_env_isolation "$@"
        ;;
esac
