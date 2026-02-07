# SPDX-License-Identifier: GPL-2.0-only
# OpenWrt OCI Build System
"""
Multi-stage OCI-based build system for OpenWrt.

Stages: tools -> toolchain -> kernel -> packages -> images

Key features:
- Content-addressable store (CAS) for build artifacts
- Merkle-tree based change detection
- Two-tier caching: timestamp pre-check + hash verification
- Parallel package builds in topological waves
- Early cutoff: skip rebuild cascades when output is unchanged
- Incremental package builds (Alpine-style)
- Reproducible hermetic container builds
"""

__version__ = "0.1.0"

STAGES = ("tools", "toolchain", "kernel", "packages", "images")
