"""Regression tests for :mod:`core.mesh_baseline_manager`.

We prove the three properties that make this module useful:

  1. **Idempotence** — snapshotting the same key twice keeps the
     FIRST bytes. This is what prevents the "patch twice = mesh
     shattered" corruption loop from the v1.22.9 bug report.
  2. **Integrity** — a corrupted .bin file refuses to deserve
     (SHA-1 mismatch returns None + logs) so callers fall back
     to the live PAC instead of feeding bogus donor data to the
     rebuilder.
  3. **Surgical edits** — delete / clear_all / list_baselines
     manipulate exactly the targeted snapshots, no collateral
     damage.

Plus a full set of edge-case coverage on the key-normalisation
path (Windows slashes, lowercasing, unicode) because a key collision
would silently serve the wrong PAC's bytes.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from core.mesh_baseline_manager import (   # noqa: E402
    MeshBaseline,
    MeshBaselineManager,
)


# ── Helpers ───────────────────────────────────────────────────────

def _mgr(tmp: Path) -> MeshBaselineManager:
    return MeshBaselineManager(cache_dir=tmp)


class _BaseCase(unittest.TestCase):
    """Shared setup — each test runs in its own tmp cache dir."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.mgr = _mgr(self.tmp)


# ── Snapshot + round-trip ────────────────────────────────────────

class SnapshotCore(_BaseCase):

    def test_first_snapshot_writes_bytes_and_meta(self):
        path = "character/cd_phm_00.pac"
        data = b"PAC\x00" * 256
        assert self.mgr.snapshot(path, data, source_paz="char.paz") is True
        # Bytes round-trip byte-identical.
        self.assertEqual(self.mgr.get_bytes(path), data)
        # Meta carries the right fields.
        meta = self.mgr.get_meta(path)
        self.assertIsNotNone(meta)
        self.assertEqual(meta.byte_size, len(data))
        self.assertEqual(meta.source_paz, "char.paz")
        self.assertEqual(meta.original_sha1, hashlib.sha1(data).hexdigest())

    def test_second_snapshot_is_a_no_op_by_default(self):
        path = "foo.pac"
        self.mgr.snapshot(path, b"ORIGINAL")
        result = self.mgr.snapshot(path, b"LATER_BYTES")
        self.assertFalse(result)
        # Stored bytes are still the FIRST ones — the whole point.
        self.assertEqual(self.mgr.get_bytes(path), b"ORIGINAL")

    def test_force_overwrite(self):
        path = "foo.pac"
        self.mgr.snapshot(path, b"A")
        assert self.mgr.snapshot(path, b"B", force=True) is True
        self.assertEqual(self.mgr.get_bytes(path), b"B")

    def test_empty_path_raises(self):
        with self.assertRaises(ValueError):
            self.mgr.snapshot("", b"x")

    def test_non_bytes_raises(self):
        with self.assertRaises(TypeError):
            self.mgr.snapshot("a.pac", "not bytes")  # type: ignore[arg-type]


# ── Key normalisation ────────────────────────────────────────────

class KeyNormalisation(_BaseCase):

    def test_windows_and_posix_slashes_share_one_key(self):
        self.mgr.snapshot("character/cd.pac", b"X")
        # Windows-style slashes should map to the same snapshot.
        self.assertEqual(self.mgr.get_bytes("character\\cd.pac"), b"X")
        self.assertTrue(self.mgr.has_baseline("character\\cd.pac"))

    def test_leading_slash_stripped(self):
        self.mgr.snapshot("/character/cd.pac", b"X")
        self.assertEqual(self.mgr.get_bytes("character/cd.pac"), b"X")

    def test_case_insensitive(self):
        self.mgr.snapshot("Character/CD.pac", b"X")
        self.assertEqual(self.mgr.get_bytes("character/cd.pac"), b"X")

    def test_different_paths_do_not_collide(self):
        self.mgr.snapshot("a/b.pac", b"A")
        self.mgr.snapshot("c/d.pac", b"D")
        self.assertEqual(self.mgr.get_bytes("a/b.pac"), b"A")
        self.assertEqual(self.mgr.get_bytes("c/d.pac"), b"D")

    def test_unicode_path_stored_and_retrieved(self):
        path = "character/이름.pac"
        data = b"K0RR3AN"
        self.mgr.snapshot(path, data)
        self.assertEqual(self.mgr.get_bytes(path), data)


# ── Integrity guard ──────────────────────────────────────────────

class IntegrityGuard(_BaseCase):

    def test_corrupt_bin_returns_none(self):
        path = "guard.pac"
        self.mgr.snapshot(path, b"ORIGINAL_BYTES")
        # Overwrite the on-disk .bin with bogus content.
        bin_path = next(self.tmp.glob("guard*.bin"))
        bin_path.write_bytes(b"TAMPERED")
        # SHA-1 now mismatches the recorded one in meta.json.
        self.assertIsNone(self.mgr.get_bytes(path, verify=True))

    def test_corrupt_bin_served_without_verify(self):
        # Opt-out verify path — useful for forensic inspection.
        path = "guard.pac"
        self.mgr.snapshot(path, b"ORIGINAL")
        bin_path = next(self.tmp.glob("guard*.bin"))
        bin_path.write_bytes(b"MANGLED")
        self.assertEqual(self.mgr.get_bytes(path, verify=False), b"MANGLED")

    def test_corrupt_meta_returns_none(self):
        path = "guard.pac"
        self.mgr.snapshot(path, b"DATA")
        meta_path = next(self.tmp.glob("guard*.meta.json"))
        meta_path.write_text("{ not valid json", encoding="utf-8")
        self.assertFalse(self.mgr.has_baseline(path))
        self.assertIsNone(self.mgr.get_bytes(path))

    def test_missing_bin_returns_none(self):
        path = "guard.pac"
        self.mgr.snapshot(path, b"X")
        next(self.tmp.glob("guard*.bin")).unlink()
        self.assertIsNone(self.mgr.get_bytes(path))

    def test_missing_meta_returns_none(self):
        path = "guard.pac"
        self.mgr.snapshot(path, b"X")
        next(self.tmp.glob("guard*.meta.json")).unlink()
        self.assertIsNone(self.mgr.get_bytes(path))


# ── Delete / clear / list ────────────────────────────────────────

class DeleteClearList(_BaseCase):

    def test_delete_removes_bin_and_meta(self):
        self.mgr.snapshot("a.pac", b"X")
        self.assertTrue(self.mgr.delete("a.pac"))
        self.assertFalse(self.mgr.has_baseline("a.pac"))

    def test_delete_unknown_is_harmless(self):
        self.assertFalse(self.mgr.delete("never-existed.pac"))

    def test_clear_all_removes_everything(self):
        self.mgr.snapshot("a.pac", b"A")
        self.mgr.snapshot("b.pac", b"B")
        self.mgr.snapshot("c.pac", b"C")
        removed = self.mgr.clear_all()
        self.assertEqual(removed, 3)
        self.assertFalse(self.mgr.has_baseline("a.pac"))
        self.assertFalse(self.mgr.has_baseline("c.pac"))

    def test_list_baselines_returns_every_snapshot(self):
        self.mgr.snapshot("a.pac", b"X")
        self.mgr.snapshot("b.pac", b"YZZZ")
        items = self.mgr.list_baselines()
        paths = sorted(b.vfs_path for b in items)
        self.assertEqual(paths, ["a.pac", "b.pac"])

    def test_list_baselines_sorts_by_time(self):
        self.mgr.snapshot("a.pac", b"X")
        # Force the second snapshot to have a later mtime by
        # sleeping a hair — time.time() resolution on Windows is
        # ~15ms so this is safe.
        import time
        time.sleep(0.01)
        self.mgr.snapshot("b.pac", b"Y")
        items = self.mgr.list_baselines()
        self.assertEqual(
            [b.vfs_path for b in items],
            ["a.pac", "b.pac"],
        )

    def test_list_baselines_empty_dir(self):
        self.assertEqual(self.mgr.list_baselines(), [])


# ── get_or_snapshot (the one-line call site) ────────────────────

class GetOrSnapshot(_BaseCase):

    def test_live_read_invoked_only_on_first_call(self):
        calls = []

        def live():
            calls.append(1)
            return b"LIVE_BYTES"

        a = self.mgr.get_or_snapshot("x.pac", live, source_paz="p.paz")
        b = self.mgr.get_or_snapshot("x.pac", live)
        c = self.mgr.get_or_snapshot("x.pac", live)
        self.assertEqual((a, b, c), (b"LIVE_BYTES",) * 3)
        self.assertEqual(sum(calls), 1)   # exactly one live read

    def test_baseline_survives_across_manager_instances(self):
        # A second manager pointing at the same cache dir should
        # still see the prior snapshot (reload after reboot).
        self.mgr.snapshot("x.pac", b"DATA")
        fresh_mgr = MeshBaselineManager(cache_dir=self.tmp)
        self.assertEqual(fresh_mgr.get_bytes("x.pac"), b"DATA")

    def test_get_or_snapshot_returns_live_bytes_on_snapshot_fail(self):
        # Force snapshot to fail by pointing cache_dir at a read-
        # only sentinel. We expect live bytes still come back.
        ro = self.tmp / "readonly_child"
        ro.mkdir()
        # On Windows, chmod is a no-op for dir write bits, but we
        # can simulate failure by monkey-patching atomic_write.
        mgr = _mgr(ro)
        import core.mesh_baseline_manager as mod

        def _boom(*_a, **_kw):
            raise OSError("disk full simulated")

        original = mod.atomic_write
        mod.atomic_write = _boom
        try:
            data = mgr.get_or_snapshot(
                "y.pac", live_read=lambda: b"LIVE"
            )
            self.assertEqual(data, b"LIVE")
        finally:
            mod.atomic_write = original


# ── Idempotence contract (the whole point) ──────────────────────

class IdempotenceContract(_BaseCase):
    """Patching twice must be byte-identical — this is what the
    baseline manager guarantees for the mesh-patch flow."""

    def test_bytes_do_not_drift_across_many_reads(self):
        path = "char/phm.pac"
        original = b"PAC_ORIGINAL_BYTES" + b"\x00" * 500
        self.mgr.snapshot(path, original)
        # Simulate 10 rebuild cycles, each reading donor bytes.
        readings = [self.mgr.get_bytes(path) for _ in range(10)]
        for r in readings:
            self.assertEqual(r, original)

    def test_bytes_do_not_drift_across_manager_recreations(self):
        # Create / destroy the manager multiple times to prove the
        # snapshot survives process restarts.
        path = "char/phm.pac"
        original = b"DONT_CHANGE_ME"
        self.mgr.snapshot(path, original)
        for _ in range(5):
            fresh = MeshBaselineManager(cache_dir=self.tmp)
            self.assertEqual(fresh.get_bytes(path), original)


# ── Concurrency ─────────────────────────────────────────────────

class Concurrency(_BaseCase):
    """Two threads snapshotting the same key must not corrupt one
    another. The winner is the first writer; the loser is a no-op."""

    def test_parallel_snapshot_same_key(self):
        path = "race.pac"
        done = threading.Barrier(2)
        outcomes: list[bool] = []

        def worker(payload: bytes):
            done.wait()  # line-up for a near-simultaneous start
            outcomes.append(self.mgr.snapshot(path, payload))

        t1 = threading.Thread(target=worker, args=(b"THREAD_A",))
        t2 = threading.Thread(target=worker, args=(b"THREAD_B",))
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        # Exactly one snapshot should have succeeded.
        self.assertEqual(outcomes.count(True), 1)
        self.assertEqual(outcomes.count(False), 1)
        # Bytes are self-consistent (either A or B, but not a
        # mixture).
        bytes_ = self.mgr.get_bytes(path)
        self.assertIn(bytes_, (b"THREAD_A", b"THREAD_B"))


if __name__ == "__main__":
    unittest.main()
