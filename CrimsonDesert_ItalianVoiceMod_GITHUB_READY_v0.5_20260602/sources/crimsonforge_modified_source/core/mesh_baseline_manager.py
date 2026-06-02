"""Persistent snapshot manager for mesh PACs before first modification.

Why this exists
---------------
"Import OBJ + Patch to Game" used to read donor vertex data (bone
weights, packed normals, material IDs, …) from the LIVE PAC on
disk. That works the first time, but every subsequent patch reads
the already-modified bytes and compounds tiny drifts until the
mesh in-game shatters. Reverting to a pristine OBJ does not help
because the donor source is STILL the corrupted live PAC — only
Steam's "Verify Integrity of Game Files" restores the original.

This manager breaks the feedback loop by snapshotting the original
PAC bytes the FIRST time the user patches a mesh. Every rebuild
from that point on sources donor data from the snapshot, not from
the live archive. Patching 1, 2, or N times produces byte-
identical results because the input is stable.

Location on disk
----------------
``~/.crimsonforge/mesh_baselines/``

  <safe-key>.bin        — the raw PAC bytes, verbatim
  <safe-key>.meta.json  — snapshot date, original SHA-1, source
                          VFS path, source PAZ, user-added notes

The "safe key" is a filesystem-safe hash of the VFS path so we
support long Windows paths + non-ASCII characters (Pearl Abyss
uses Korean in some file names).

Integrity checks
----------------
Every read verifies the SHA-1 of the bytes against the meta.json
hash. A mismatch means someone corrupted the baseline folder, in
which case we log loud and refuse to serve it — the user is asked
to re-snapshot (typically via "Verify Integrity" in Steam +
re-patching).

Thread safety
-------------
All filesystem writes are atomic via :func:`utils.platform_utils.
atomic_write`, so a crash mid-snapshot leaves the previous
baseline intact. A process-local lock serialises same-key writes
so two concurrent patches of the same PAC can't corrupt each
other's snapshot.

Non-goals
---------
This is NOT a generic backup system. It stores only PAC bytes for
the purpose of stable donor-vertex lookup. For full archive
backups see :class:`core.backup_manager.BackupManager`.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from utils.logger import get_logger
from utils.platform_utils import atomic_write

logger = get_logger("core.mesh_baseline_manager")


# Keep the schema explicit — every field has a reason to exist and
# we pin the schema version so a future format change (e.g. adding
# compression) can migrate cleanly.
_SCHEMA_VERSION = 1


@dataclass
class MeshBaseline:
    """In-memory representation of one stored snapshot."""
    vfs_path: str            # canonical VFS path (lowercased)
    original_sha1: str       # SHA-1 of the bytes (integrity guard)
    byte_size: int           # duplicate of len(bytes) for quick scan
    source_paz: str          # the PAZ the bytes were read from (for debugging)
    snapshot_unix: float     # time.time() at snapshot
    notes: str = ""          # user-supplied, optional


class MeshBaselineManager:
    """Snapshot + serve original PAC bytes keyed by VFS path.

    Construct with the cache directory (defaults to
    ``~/.crimsonforge/mesh_baselines``). All methods are safe to
    call with empty / missing baselines — they return ``None`` or
    do nothing rather than raising.
    """

    _DEFAULT_DIR = Path.home() / ".crimsonforge" / "mesh_baselines"

    def __init__(self, cache_dir: Optional[Path] = None):
        self._cache_dir = Path(cache_dir) if cache_dir else self._DEFAULT_DIR
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        # Process-local lock map. One lock per safe-key so unrelated
        # snapshots don't block each other, but same-key writes
        # are serialised.
        self._locks: dict[str, threading.Lock] = {}
        self._locks_lock = threading.Lock()

    # ── key derivation ─────────────────────────────────

    @staticmethod
    def _canonical_key(vfs_path: str) -> str:
        """Normalise a VFS path into a stable cache key.

        We lowercase (VFS paths are case-insensitive on Windows,
        which is where this code runs), forward-slash separate,
        and strip leading slashes so ``\\cd_phm_00.pac`` and
        ``cd_phm_00.pac`` share one baseline.
        """
        if not vfs_path:
            return ""
        norm = vfs_path.replace("\\", "/").lstrip("/").strip().lower()
        return norm

    @staticmethod
    def _safe_filename(key: str) -> str:
        """Convert a canonical key into a filesystem-safe basename.

        Windows limits basenames to ~255 chars and forbids
        ``<>:"|?*`` plus reserved names (CON, PRN, …). We hash the
        key and prepend a short, readable stem from the tail of
        the path so users browsing the folder can still spot which
        mesh is which.
        """
        if not key:
            return "_empty"
        tail = key.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        # Keep alphanumerics + a few safe punctuators.
        stem_chars = "".join(
            c if (c.isalnum() or c in "_-") else "_"
            for c in tail
        )[:40]
        digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]
        return f"{stem_chars}__{digest}"

    def _bin_path(self, key: str) -> Path:
        return self._cache_dir / f"{self._safe_filename(key)}.bin"

    def _meta_path(self, key: str) -> Path:
        return self._cache_dir / f"{self._safe_filename(key)}.meta.json"

    def _lock_for(self, key: str) -> threading.Lock:
        with self._locks_lock:
            lock = self._locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._locks[key] = lock
            return lock

    # ── snapshot / get / delete ───────────────────────

    def snapshot(
        self,
        vfs_path: str,
        data: bytes,
        source_paz: str = "",
        notes: str = "",
        force: bool = False,
    ) -> bool:
        """Store ``data`` as the baseline for ``vfs_path``.

        Returns ``True`` if the snapshot was written, ``False`` if
        a snapshot already exists and ``force`` is not set (the
        common case — we only want to capture ORIGINAL bytes, so
        we refuse to overwrite by default).

        Raises :class:`ValueError` only for unusable input (empty
        path). Filesystem errors propagate so callers can surface
        them in the UI.
        """
        if not vfs_path:
            raise ValueError("vfs_path must be non-empty")
        if not isinstance(data, (bytes, bytearray)):
            raise TypeError("data must be bytes")

        key = self._canonical_key(vfs_path)
        lock = self._lock_for(key)
        with lock:
            if not force and self.has_baseline(vfs_path):
                logger.debug(
                    "baseline already exists for %s — not overwriting",
                    vfs_path,
                )
                return False

            sha1 = hashlib.sha1(data).hexdigest()
            meta = {
                "schema_version": _SCHEMA_VERSION,
                "vfs_path": key,
                "original_sha1": sha1,
                "byte_size": len(data),
                "source_paz": source_paz,
                "snapshot_unix": time.time(),
                "notes": notes,
            }

            bin_p = self._bin_path(key)
            meta_p = self._meta_path(key)
            atomic_write(bin_p, bytes(data))
            atomic_write(
                meta_p,
                json.dumps(meta, indent=2, ensure_ascii=False)
                .encode("utf-8"),
            )
            logger.info(
                "baseline snapshot: %s (%d bytes, sha1=%s)",
                vfs_path, len(data), sha1[:12],
            )
            return True

    def has_baseline(self, vfs_path: str) -> bool:
        """Return True if a baseline exists + its meta.json parses.

        We intentionally don't verify the SHA-1 on this call (it
        would be O(n) for huge meshes and the typical caller is
        just deciding whether to take a snapshot). :meth:`get_bytes`
        does the integrity check at read time.
        """
        key = self._canonical_key(vfs_path)
        if not key:
            return False
        bin_p = self._bin_path(key)
        meta_p = self._meta_path(key)
        if not (bin_p.is_file() and meta_p.is_file()):
            return False
        try:
            with meta_p.open("r", encoding="utf-8") as f:
                meta = json.load(f)
            return (
                meta.get("schema_version") == _SCHEMA_VERSION
                and meta.get("vfs_path") == key
                and isinstance(meta.get("original_sha1"), str)
            )
        except (OSError, json.JSONDecodeError):
            return False

    def get_bytes(self, vfs_path: str, verify: bool = True) -> Optional[bytes]:
        """Return the baseline bytes, or ``None`` if no snapshot.

        With ``verify=True`` (the default) the SHA-1 of the on-disk
        bytes is re-computed and compared against the stored hash.
        A mismatch logs a loud warning and returns ``None`` so the
        caller falls back to the live PAC instead of serving
        corrupted baseline bytes.
        """
        key = self._canonical_key(vfs_path)
        if not key:
            return None
        bin_p = self._bin_path(key)
        meta_p = self._meta_path(key)
        if not (bin_p.is_file() and meta_p.is_file()):
            return None

        try:
            with meta_p.open("r", encoding="utf-8") as f:
                meta = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("baseline meta read failed: %s", exc)
            return None

        try:
            with bin_p.open("rb") as f:
                data = f.read()
        except OSError as exc:
            logger.warning("baseline read failed: %s", exc)
            return None

        if verify:
            expected = meta.get("original_sha1", "")
            actual = hashlib.sha1(data).hexdigest()
            if expected != actual:
                logger.error(
                    "BASELINE INTEGRITY FAILURE for %s: "
                    "expected sha1=%s, got %s — refusing to serve "
                    "corrupted baseline",
                    vfs_path, expected[:12], actual[:12],
                )
                return None
        return data

    def get_meta(self, vfs_path: str) -> Optional[MeshBaseline]:
        """Return metadata without loading the bytes (fast)."""
        key = self._canonical_key(vfs_path)
        if not key:
            return None
        meta_p = self._meta_path(key)
        if not meta_p.is_file():
            return None
        try:
            with meta_p.open("r", encoding="utf-8") as f:
                m = json.load(f)
        except (OSError, json.JSONDecodeError):
            return None
        return MeshBaseline(
            vfs_path=m.get("vfs_path", key),
            original_sha1=m.get("original_sha1", ""),
            byte_size=int(m.get("byte_size", 0)),
            source_paz=m.get("source_paz", ""),
            snapshot_unix=float(m.get("snapshot_unix", 0.0)),
            notes=m.get("notes", ""),
        )

    def delete(self, vfs_path: str) -> bool:
        """Remove a snapshot. Returns True if anything was deleted."""
        key = self._canonical_key(vfs_path)
        if not key:
            return False
        deleted = False
        for p in (self._bin_path(key), self._meta_path(key)):
            try:
                if p.is_file():
                    p.unlink()
                    deleted = True
            except OSError as exc:
                logger.warning("baseline delete failed: %s (%s)", p, exc)
        if deleted:
            logger.info("baseline deleted: %s", vfs_path)
        return deleted

    def clear_all(self) -> int:
        """Delete every baseline. Returns the number of snapshots removed."""
        if not self._cache_dir.is_dir():
            return 0
        count = 0
        for p in list(self._cache_dir.iterdir()):
            try:
                if p.is_file() and p.suffix in (".bin", ".meta.json"):
                    p.unlink()
                    if p.suffix == ".bin":
                        count += 1
            except OSError as exc:
                logger.warning("baseline clear failed: %s (%s)", p, exc)
        return count

    def list_baselines(self) -> list[MeshBaseline]:
        """Return metadata for every stored snapshot (for UI listing)."""
        out: list[MeshBaseline] = []
        if not self._cache_dir.is_dir():
            return out
        for p in self._cache_dir.glob("*.meta.json"):
            try:
                with p.open("r", encoding="utf-8") as f:
                    m = json.load(f)
            except (OSError, json.JSONDecodeError):
                continue
            out.append(MeshBaseline(
                vfs_path=m.get("vfs_path", ""),
                original_sha1=m.get("original_sha1", ""),
                byte_size=int(m.get("byte_size", 0)),
                source_paz=m.get("source_paz", ""),
                snapshot_unix=float(m.get("snapshot_unix", 0.0)),
                notes=m.get("notes", ""),
            ))
        out.sort(key=lambda b: b.snapshot_unix)
        return out

    @property
    def cache_dir(self) -> Path:
        return self._cache_dir

    # ── high-level helper ─────────────────────────────

    def get_or_snapshot(
        self,
        vfs_path: str,
        live_read: "callable[[], bytes]",
        source_paz: str = "",
    ) -> bytes:
        """Return the baseline bytes for ``vfs_path`` if present,
        otherwise call ``live_read()`` to fetch from the live
        archive, snapshot the result, and return those bytes.

        This is the **one-line call site** the mesh-patch flow
        should use in place of a raw ``vfs.read_entry_data(entry)``
        so every patch is protected against compound-corruption.

        Parameters
        ----------
        vfs_path
            Canonical VFS path of the PAC.
        live_read
            Zero-arg callable that returns the current live bytes.
            Only invoked on the first patch.
        source_paz
            PAZ filename (for debugging metadata); not used for
            correctness.
        """
        cached = self.get_bytes(vfs_path, verify=True)
        if cached is not None:
            return cached
        data = live_read()
        try:
            self.snapshot(vfs_path, data, source_paz=source_paz)
        except Exception as exc:  # pragma: no cover - defensive
            # A snapshot failure should NEVER crash the patch —
            # we'd rather process with the live bytes than abort.
            logger.warning(
                "baseline snapshot failed (continuing with live bytes): %s",
                exc,
            )
        return data
