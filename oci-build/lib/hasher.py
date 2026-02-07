# SPDX-License-Identifier: GPL-2.0-only
"""
Fast content hashing with Merkle tree support.

Two-tier change detection:
1. Timestamp pre-check (microseconds) - skip hashing if mtime unchanged
2. SHA256 hash verification (milliseconds) - only for changed files

Merkle trees enable O(log n) change detection for directory hierarchies.
"""

import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Optional


# 64KB read buffer for hashing large files
_HASH_BUF_SIZE = 65536


def hash_bytes(data: bytes) -> str:
    """SHA256 hash of raw bytes, returned as hex string."""
    return hashlib.sha256(data).hexdigest()


def hash_string(s: str) -> str:
    """SHA256 hash of a UTF-8 string."""
    return hash_bytes(s.encode("utf-8"))


def hash_file(path: str) -> str:
    """SHA256 hash of a file's contents."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(_HASH_BUF_SIZE)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def hash_file_with_mtime(
    path: str, mtime_cache: Optional[dict] = None
) -> tuple[str, float]:
    """Hash a file, using mtime cache to skip re-hashing unchanged files.

    Returns (hash, mtime) tuple. If mtime_cache is provided and the file's
    mtime matches the cached value, returns the cached hash without reading
    the file (tier-1 fast path).
    """
    st = os.stat(path)
    mtime = st.st_mtime_ns  # nanosecond precision

    if mtime_cache is not None:
        cached = mtime_cache.get(path)
        if cached and cached[1] == mtime:
            return cached[0], mtime

    file_hash = hash_file(path)

    if mtime_cache is not None:
        mtime_cache[path] = (file_hash, mtime)

    return file_hash, mtime


class MerkleTree:
    """Content-addressable Merkle tree for directory hierarchies.

    Each directory node's hash is computed from its children's hashes,
    enabling O(log n) change detection by comparing root hashes.
    """

    __slots__ = ("root_hash", "nodes", "_mtime_cache")

    def __init__(self, mtime_cache: Optional[dict] = None):
        self.root_hash: Optional[str] = None
        self.nodes: dict[str, str] = {}  # path -> hash
        self._mtime_cache = mtime_cache if mtime_cache is not None else {}

    def compute(
        self,
        directory: str,
        exclude: Optional[set[str]] = None,
        include_patterns: Optional[list[str]] = None,
    ) -> str:
        """Compute Merkle tree hash for a directory.

        Args:
            directory: Root directory to hash.
            exclude: Set of directory/file names to skip.
            include_patterns: If set, only include files matching these suffixes.

        Returns:
            Root hash of the Merkle tree.
        """
        if exclude is None:
            exclude = set()
        self.root_hash = self._hash_dir(directory, exclude, include_patterns)
        return self.root_hash

    def _hash_dir(
        self,
        directory: str,
        exclude: set[str],
        include_patterns: Optional[list[str]],
    ) -> str:
        h = hashlib.sha256()
        entries = []

        try:
            dir_entries = sorted(os.scandir(directory), key=lambda e: e.name)
        except PermissionError:
            return hash_string(f"permission_denied:{directory}")

        for entry in dir_entries:
            if entry.name in exclude:
                continue
            if entry.name.startswith("."):
                continue

            if entry.is_dir(follow_symlinks=False):
                child_hash = self._hash_dir(
                    entry.path, exclude, include_patterns
                )
                entries.append(f"d:{entry.name}:{child_hash}")
                self.nodes[entry.path] = child_hash
            elif entry.is_file(follow_symlinks=False):
                if include_patterns:
                    if not any(entry.name.endswith(p) for p in include_patterns):
                        continue
                file_hash, _ = hash_file_with_mtime(
                    entry.path, self._mtime_cache
                )
                entries.append(f"f:{entry.name}:{file_hash}")
                self.nodes[entry.path] = file_hash
            elif entry.is_symlink():
                target = os.readlink(entry.path)
                link_hash = hash_string(target)
                entries.append(f"l:{entry.name}:{link_hash}")
                self.nodes[entry.path] = link_hash

        combined = "\n".join(entries)
        h.update(combined.encode("utf-8"))
        dir_hash = h.hexdigest()
        self.nodes[directory] = dir_hash
        return dir_hash

    def diff(self, other: "MerkleTree") -> list[str]:
        """Find paths that differ between two Merkle trees."""
        changed = []
        all_paths = set(self.nodes.keys()) | set(other.nodes.keys())
        for path in sorted(all_paths):
            h1 = self.nodes.get(path)
            h2 = other.nodes.get(path)
            if h1 != h2:
                changed.append(path)
        return changed


class BuildIdentity:
    """Computes a unique hash identifying a build action and all its inputs.

    The build identity is a SHA256 hash of:
    - Source file tree hash (Merkle root)
    - Build configuration hash
    - Dependency output hashes (transitive)
    - Toolchain/compiler version identifier

    This enables cache lookups: same identity = same output (reproducible).
    """

    __slots__ = ("_components",)

    def __init__(self):
        self._components: dict[str, str] = {}

    def add_source(self, name: str, directory: str, **kwargs) -> "BuildIdentity":
        """Add a source directory's Merkle hash."""
        tree = MerkleTree()
        tree.compute(directory, **kwargs)
        self._components[f"src:{name}"] = tree.root_hash
        return self

    def add_file(self, name: str, path: str) -> "BuildIdentity":
        """Add a single file's hash."""
        self._components[f"file:{name}"] = hash_file(path)
        return self

    def add_config(self, name: str, config: dict) -> "BuildIdentity":
        """Add a configuration dict's hash."""
        canonical = json.dumps(config, sort_keys=True, separators=(",", ":"))
        self._components[f"cfg:{name}"] = hash_string(canonical)
        return self

    def add_dep(self, name: str, dep_hash: str) -> "BuildIdentity":
        """Add a dependency's output hash."""
        self._components[f"dep:{name}"] = dep_hash
        return self

    def add_string(self, name: str, value: str) -> "BuildIdentity":
        """Add an arbitrary string component."""
        self._components[f"str:{name}"] = hash_string(value)
        return self

    def hexdigest(self) -> str:
        """Compute the final build identity hash."""
        h = hashlib.sha256()
        for key in sorted(self._components.keys()):
            h.update(f"{key}={self._components[key]}\n".encode("utf-8"))
        return h.hexdigest()

    def components(self) -> dict[str, str]:
        """Return a copy of all identity components (for debugging)."""
        return dict(self._components)


class MtimeIndex:
    """Persistent mtime index for tier-1 fast change detection.

    Stores (path -> (hash, mtime_ns)) mappings on disk as JSON.
    On subsequent builds, files whose mtime hasn't changed can skip
    re-hashing entirely (sub-microsecond per file).
    """

    def __init__(self, index_path: str):
        self._path = index_path
        self._data: dict[str, tuple[str, int]] = {}
        self._dirty = False

    def load(self) -> "MtimeIndex":
        """Load index from disk."""
        if os.path.exists(self._path):
            with open(self._path, "r") as f:
                raw = json.load(f)
            self._data = {k: tuple(v) for k, v in raw.items()}
        return self

    def save(self) -> None:
        """Persist index to disk."""
        if not self._dirty:
            return
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        with open(self._path, "w") as f:
            json.dump(self._data, f, separators=(",", ":"))
        self._dirty = False

    def get(self, path: str) -> Optional[tuple[str, int]]:
        return self._data.get(path)

    def put(self, path: str, file_hash: str, mtime_ns: int) -> None:
        self._data[path] = (file_hash, mtime_ns)
        self._dirty = True

    def as_cache_dict(self) -> dict:
        """Return dict compatible with hash_file_with_mtime's mtime_cache."""
        return self._data
