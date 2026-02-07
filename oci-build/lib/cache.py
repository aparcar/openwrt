# SPDX-License-Identifier: GPL-2.0-only
"""
Two-tier content-addressable build cache.

Architecture (inspired by Bazel's Remote Execution API):

  Action Cache (AC):  build_identity_hash -> OutputManifest
  Content Store (CAS): sha256_hash -> artifact_blob

The AC answers "have I built this exact configuration before?"
The CAS answers "do I have this artifact's content?"

Early cutoff optimization (inspired by Nix CA derivations):
  When a dependency is rebuilt but produces identical output,
  downstream packages skip their rebuild entirely.

Directory layout:
  <cache_root>/
    ac/         - Action cache entries (JSON manifests)
    cas/        - Content-addressable blobs
    stages/     - Stage-level output hashes
    mtime.json  - Timestamp index for fast change detection
"""

import hashlib
import json
import os
import shutil
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional


@dataclass
class OutputManifest:
    """Describes the outputs of a build action."""

    build_identity: str
    outputs: dict[str, str]  # relative_path -> content_hash
    timestamp: float = field(default_factory=time.time)
    stage: str = ""
    package: str = ""
    duration_seconds: float = 0.0

    def content_hash(self) -> str:
        """Hash of all output content hashes (for early cutoff)."""
        h = hashlib.sha256()
        for path in sorted(self.outputs.keys()):
            h.update(f"{path}:{self.outputs[path]}\n".encode("utf-8"))
        return h.hexdigest()

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, indent=2)

    @classmethod
    def from_json(cls, data: str) -> "OutputManifest":
        d = json.loads(data)
        return cls(**d)


class ContentStore:
    """Content-addressable storage for build artifacts.

    Stores blobs keyed by their SHA256 hash. Supports both individual
    files and directory trees (stored as tar archives).

    Deduplication is automatic: identical content shares storage.
    """

    def __init__(self, root: str):
        self._root = root
        os.makedirs(root, exist_ok=True)

    def _blob_path(self, content_hash: str) -> str:
        # Two-level directory structure to avoid filesystem limits
        return os.path.join(self._root, content_hash[:2], content_hash[2:4], content_hash)

    def has(self, content_hash: str) -> bool:
        """Check if a blob exists in the store."""
        return os.path.exists(self._blob_path(content_hash))

    def store_file(self, source_path: str, content_hash: Optional[str] = None) -> str:
        """Store a file in the CAS. Returns content hash."""
        if content_hash is None:
            from .hasher import hash_file
            content_hash = hash_file(source_path)

        blob_path = self._blob_path(content_hash)
        if os.path.exists(blob_path):
            return content_hash

        os.makedirs(os.path.dirname(blob_path), exist_ok=True)
        # Atomic write via temp + rename
        tmp_path = blob_path + f".tmp.{os.getpid()}"
        shutil.copy2(source_path, tmp_path)
        os.rename(tmp_path, blob_path)
        return content_hash

    def retrieve_file(self, content_hash: str, dest_path: str) -> bool:
        """Retrieve a blob from the CAS to a destination path."""
        blob_path = self._blob_path(content_hash)
        if not os.path.exists(blob_path):
            return False

        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        shutil.copy2(blob_path, dest_path)
        return True

    def store_directory(self, source_dir: str) -> str:
        """Store a directory as a tar archive in the CAS."""
        import tarfile
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".tar.zst", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            with tarfile.open(tmp_path, "w:xz") as tar:
                tar.add(source_dir, arcname=".")
            from .hasher import hash_file
            content_hash = hash_file(tmp_path)
            self.store_file(tmp_path, content_hash)
            return content_hash
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    def retrieve_directory(self, content_hash: str, dest_dir: str) -> bool:
        """Retrieve a directory archive from the CAS."""
        import tarfile
        import tempfile

        blob_path = self._blob_path(content_hash)
        if not os.path.exists(blob_path):
            return False

        os.makedirs(dest_dir, exist_ok=True)
        with tarfile.open(blob_path, "r:*") as tar:
            tar.extractall(dest_dir)
        return True

    def gc(self, keep_hashes: set[str]) -> int:
        """Garbage collect blobs not in keep_hashes. Returns bytes freed."""
        freed = 0
        for dirpath, dirnames, filenames in os.walk(self._root):
            for fname in filenames:
                if fname.endswith(".tmp"):
                    continue
                fpath = os.path.join(dirpath, fname)
                if fname not in keep_hashes:
                    freed += os.path.getsize(fpath)
                    os.unlink(fpath)
        return freed

    @property
    def root(self) -> str:
        return self._root


class ActionCache:
    """Maps build identity hashes to output manifests.

    Before executing any build action, the orchestrator checks the AC:
    - Hit: skip build, retrieve outputs from CAS
    - Miss: execute build, store outputs in CAS, record in AC
    """

    def __init__(self, root: str):
        self._root = root
        os.makedirs(root, exist_ok=True)

    def _entry_path(self, build_identity: str) -> str:
        return os.path.join(self._root, build_identity[:2], f"{build_identity}.json")

    def lookup(self, build_identity: str) -> Optional[OutputManifest]:
        """Look up a build identity in the action cache."""
        path = self._entry_path(build_identity)
        if not os.path.exists(path):
            return None
        with open(path, "r") as f:
            return OutputManifest.from_json(f.read())

    def store(self, manifest: OutputManifest) -> None:
        """Store a build result in the action cache."""
        path = self._entry_path(manifest.build_identity)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp_path = path + f".tmp.{os.getpid()}"
        with open(tmp_path, "w") as f:
            f.write(manifest.to_json())
        os.rename(tmp_path, path)

    def invalidate(self, build_identity: str) -> bool:
        """Remove an action cache entry."""
        path = self._entry_path(build_identity)
        if os.path.exists(path):
            os.unlink(path)
            return True
        return False

    def list_entries(self) -> list[str]:
        """List all build identities in the cache."""
        entries = []
        for dirpath, _, filenames in os.walk(self._root):
            for fname in filenames:
                if fname.endswith(".json"):
                    entries.append(fname[:-5])
        return entries


class BuildCache:
    """Unified build cache combining Action Cache and Content Store.

    This is the main interface used by the stage executor and package builder.
    """

    def __init__(self, cache_root: str):
        self.root = cache_root
        self.ac = ActionCache(os.path.join(cache_root, "ac"))
        self.cas = ContentStore(os.path.join(cache_root, "cas"))
        self._stage_hashes_dir = os.path.join(cache_root, "stages")
        os.makedirs(self._stage_hashes_dir, exist_ok=True)

    def check_action(self, build_identity: str) -> Optional[OutputManifest]:
        """Check if a build action has cached results with valid CAS blobs."""
        manifest = self.ac.lookup(build_identity)
        if manifest is None:
            return None

        # Verify all outputs still exist in CAS
        for rel_path, content_hash in manifest.outputs.items():
            if not self.cas.has(content_hash):
                self.ac.invalidate(build_identity)
                return None

        return manifest

    def store_action(
        self,
        build_identity: str,
        output_files: dict[str, str],
        stage: str = "",
        package: str = "",
        duration: float = 0.0,
    ) -> OutputManifest:
        """Store build outputs in CAS and record in AC.

        Args:
            build_identity: Hash of all build inputs.
            output_files: Dict of relative_path -> absolute_path for outputs.
            stage: Stage name for metadata.
            package: Package name for metadata.
            duration: Build duration in seconds.

        Returns:
            The stored OutputManifest.
        """
        outputs = {}
        for rel_path, abs_path in output_files.items():
            if os.path.isdir(abs_path):
                content_hash = self.cas.store_directory(abs_path)
            else:
                content_hash = self.cas.store_file(abs_path)
            outputs[rel_path] = content_hash

        manifest = OutputManifest(
            build_identity=build_identity,
            outputs=outputs,
            stage=stage,
            package=package,
            duration_seconds=duration,
        )
        self.ac.store(manifest)
        return manifest

    def restore_outputs(
        self, manifest: OutputManifest, dest_root: str
    ) -> bool:
        """Restore all outputs from a cached manifest to dest_root."""
        for rel_path, content_hash in manifest.outputs.items():
            dest_path = os.path.join(dest_root, rel_path)
            if rel_path.endswith("/"):
                if not self.cas.retrieve_directory(content_hash, dest_path):
                    return False
            else:
                if not self.cas.retrieve_file(content_hash, dest_path):
                    return False
        return True

    def get_stage_hash(self, stage: str) -> Optional[str]:
        """Get the last successful output hash for a stage."""
        path = os.path.join(self._stage_hashes_dir, f"{stage}.hash")
        if os.path.exists(path):
            with open(path, "r") as f:
                return f.read().strip()
        return None

    def set_stage_hash(self, stage: str, output_hash: str) -> None:
        """Record the output hash for a completed stage."""
        path = os.path.join(self._stage_hashes_dir, f"{stage}.hash")
        with open(path, "w") as f:
            f.write(output_hash)

    def early_cutoff_check(
        self, stage: str, new_output_hash: str
    ) -> bool:
        """Early cutoff: returns True if output unchanged (skip dependents).

        Inspired by Nix CA derivations. If rebuilding a stage produces
        bit-identical output, downstream stages don't need rebuilding.
        """
        previous = self.get_stage_hash(stage)
        return previous is not None and previous == new_output_hash
