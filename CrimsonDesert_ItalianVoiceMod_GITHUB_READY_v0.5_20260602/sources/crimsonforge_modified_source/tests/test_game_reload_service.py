"""Regression tests for :mod:`core.game_reload_service`.

We verify:

  1. ``bind_vfs`` captures a disk fingerprint so subsequent calls
     to :meth:`is_stale` return False until the files actually
     change.
  2. ``register_tab`` + ``reload`` fan out correctly — every
     registered callback is invoked exactly once with a fresh
     :class:`ReloadPayload`.
  3. A single-tab callback that raises does NOT abort fan-out;
     the other tabs still get their refresh.
  4. ``unregister_tab`` detaches callbacks.
  5. Staleness detection picks up mtime changes + file add/remove.

The VFS itself is simulated with a lightweight in-test class so
we can exercise the orchestration without a real game install.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Optional

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from core.game_reload_service import (   # noqa: E402
    DiskFingerprint,
    GameReloadService,
    ReloadPayload,
)


# ── Minimal VFS stand-in ─────────────────────────────────────────
#
# We don't need a real VFS for orchestration tests — only the
# three attributes / methods the reload service touches:
#   .packages_path / .reload() / .load_papgt() / .load_pamt() /
#   .list_package_groups()

class _FakeVfs:
    def __init__(self, packages_path: str, groups=None):
        self._packages_path = Path(packages_path)
        self._groups = list(groups or [])
        self.reload_count = 0
        self.load_papgt_count = 0
        self.load_pamt_calls: list[str] = []

    @property
    def packages_path(self) -> str:
        return str(self._packages_path)

    def reload(self) -> None:
        self.reload_count += 1

    def load_papgt(self) -> None:
        self.load_papgt_count += 1

    def load_pamt(self, group_dir: str) -> None:
        self.load_pamt_calls.append(group_dir)

    def list_package_groups(self) -> list[str]:
        return list(self._groups)


def _make_game_dir() -> tuple[tempfile.TemporaryDirectory, Path]:
    """Build a minimal packages/ layout so the fingerprint has
    something to stat. Returns the owning TempDir + the root."""
    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name) / "packages"
    (root / "meta").mkdir(parents=True)
    (root / "meta" / "0.papgt").write_bytes(b"papgt")
    (root / "meta" / "0.paver").write_bytes(b"paver")
    for grp in ("0003", "0012", "0020"):
        (root / grp).mkdir()
        (root / grp / "0.pamt").write_bytes(b"pamt_" + grp.encode())
    return tmp, root


class _BaseCase(unittest.TestCase):

    def setUp(self):
        self._tmp, self.root = _make_game_dir()
        self.addCleanup(self._tmp.cleanup)
        self.vfs = _FakeVfs(self.root, groups=["0003", "0012", "0020"])


# ── bind_vfs + is_stale basic path ─────────────────────────────

class FingerprintLifecycle(_BaseCase):

    def test_is_stale_true_before_bind(self):
        svc = GameReloadService()
        # No VFS bound + no prior fingerprint → treat as stale.
        self.assertTrue(svc.is_stale())

    def test_is_stale_false_right_after_bind(self):
        svc = GameReloadService(vfs=self.vfs)
        svc.bind_vfs(self.vfs)
        self.assertFalse(svc.is_stale())

    def test_touching_papgt_flips_to_stale(self):
        svc = GameReloadService(vfs=self.vfs)
        svc.bind_vfs(self.vfs)
        self.assertFalse(svc.is_stale())
        # Modify content + mtime.
        time.sleep(0.02)
        (self.root / "meta" / "0.papgt").write_bytes(b"PATCHED")
        self.assertTrue(svc.is_stale())

    def test_removing_a_pamt_flips_to_stale(self):
        svc = GameReloadService(vfs=self.vfs)
        svc.bind_vfs(self.vfs)
        (self.root / "0012" / "0.pamt").unlink()
        self.assertTrue(svc.is_stale())

    def test_adding_a_new_group_flips_to_stale(self):
        svc = GameReloadService(vfs=self.vfs)
        svc.bind_vfs(self.vfs)
        new_group = self.root / "9999"
        new_group.mkdir()
        (new_group / "0.pamt").write_bytes(b"new_group_pamt")
        self.assertTrue(svc.is_stale())

    def test_snapshot_returns_populated_fingerprint(self):
        svc = GameReloadService(vfs=self.vfs)
        svc.bind_vfs(self.vfs)
        fp = svc.snapshot_disk_fingerprint()
        self.assertIsInstance(fp, DiskFingerprint)
        # 2 meta files + 3 PAMT = 5.
        self.assertEqual(len(fp.stamps), 5)
        # Every path exists on disk.
        for stamp in fp.stamps:
            self.assertTrue(Path(stamp.path).is_file())


# ── Registration + unregistration ─────────────────────────────

class Registration(_BaseCase):

    def test_register_adds_to_list(self):
        svc = GameReloadService()
        svc.register_tab("Explorer", lambda payload: None)
        self.assertEqual(svc.registered_tab_labels, ["Explorer"])

    def test_register_validates_inputs(self):
        svc = GameReloadService()
        with self.assertRaises(ValueError):
            svc.register_tab("", lambda p: None)
        with self.assertRaises(ValueError):
            svc.register_tab("Tab", None)   # type: ignore[arg-type]

    def test_unregister_removes_matching_label(self):
        svc = GameReloadService()
        svc.register_tab("A", lambda p: None)
        svc.register_tab("B", lambda p: None)
        svc.register_tab("A", lambda p: None)
        removed = svc.unregister_tab("A")
        self.assertEqual(removed, 2)
        self.assertEqual(svc.registered_tab_labels, ["B"])

    def test_unregister_unknown_label_is_harmless(self):
        svc = GameReloadService()
        self.assertEqual(svc.unregister_tab("NEVER_EXISTED"), 0)


# ── Reload fan-out ────────────────────────────────────────────

class ReloadFanout(_BaseCase):

    def test_every_registered_callback_fires(self):
        svc = GameReloadService(
            vfs=self.vfs,
            discover_palocs=lambda v: [
                {"lang_code": "en", "filename": "localizationstring_eng.paloc"},
            ],
            read_game_version=lambda p: "v1.22.9",
        )
        svc.bind_vfs(self.vfs)

        calls: list[tuple[str, ReloadPayload]] = []
        svc.register_tab("Explorer", lambda p: calls.append(("Explorer", p)))
        svc.register_tab("Translate", lambda p: calls.append(("Translate", p)))
        svc.register_tab("Repack", lambda p: calls.append(("Repack", p)))

        payload = svc.reload()

        # Each tab fired exactly once, in registration order.
        self.assertEqual([c[0] for c in calls], ["Explorer", "Translate", "Repack"])
        # Payload matches the bound VFS + the discovered palocs.
        self.assertIs(payload.vfs, self.vfs)
        self.assertEqual(len(payload.discovered_palocs), 1)
        self.assertEqual(payload.game_version, "v1.22.9")
        self.assertEqual(sorted(payload.groups), ["0003", "0012", "0020"])

    def test_vfs_reload_method_is_called(self):
        svc = GameReloadService(vfs=self.vfs)
        svc.bind_vfs(self.vfs)
        before = self.vfs.reload_count
        svc.reload()
        self.assertEqual(self.vfs.reload_count, before + 1)

    def test_all_groups_are_warmed(self):
        svc = GameReloadService(vfs=self.vfs)
        svc.bind_vfs(self.vfs)
        svc.reload()
        self.assertEqual(
            sorted(self.vfs.load_pamt_calls),
            ["0003", "0012", "0020"],
        )

    def test_failing_tab_does_not_abort_others(self):
        svc = GameReloadService(vfs=self.vfs)
        svc.bind_vfs(self.vfs)
        good_calls: list[int] = []

        def bad(_p):
            raise RuntimeError("deliberate")

        svc.register_tab("A_good", lambda p: good_calls.append(1))
        svc.register_tab("B_bad", bad)
        svc.register_tab("C_good", lambda p: good_calls.append(1))

        # Should NOT raise — service swallows the exception + logs.
        svc.reload()
        # Both good tabs still fired.
        self.assertEqual(len(good_calls), 2)

    def test_reload_without_bound_vfs_raises(self):
        svc = GameReloadService()
        with self.assertRaises(RuntimeError):
            svc.reload()

    def test_progress_callback_receives_strings(self):
        svc = GameReloadService(vfs=self.vfs)
        svc.bind_vfs(self.vfs)
        msgs: list[str] = []
        svc.reload(on_progress=msgs.append)
        self.assertTrue(len(msgs) >= 3)
        for m in msgs:
            self.assertIsInstance(m, str)

    def test_progress_callback_failure_is_swallowed(self):
        svc = GameReloadService(vfs=self.vfs)
        svc.bind_vfs(self.vfs)

        def boom(_msg: str):
            raise ValueError("progress handler exploded")
        # Should still complete.
        svc.reload(on_progress=boom)

    def test_paloc_discovery_failure_yields_empty_list(self):
        svc = GameReloadService(
            vfs=self.vfs,
            discover_palocs=lambda v: (_ for _ in ()).throw(RuntimeError("x")),
        )
        svc.bind_vfs(self.vfs)
        payload = svc.reload()
        self.assertEqual(payload.discovered_palocs, [])

    def test_version_discovery_failure_yields_empty_string(self):
        svc = GameReloadService(
            vfs=self.vfs,
            read_game_version=lambda p: (_ for _ in ()).throw(OSError("x")),
        )
        svc.bind_vfs(self.vfs)
        payload = svc.reload()
        self.assertEqual(payload.game_version, "")

    def test_reload_refreshes_fingerprint(self):
        svc = GameReloadService(vfs=self.vfs)
        svc.bind_vfs(self.vfs)
        # Drift the fingerprint by editing a file.
        time.sleep(0.02)
        (self.root / "meta" / "0.papgt").write_bytes(b"CHANGED")
        self.assertTrue(svc.is_stale())
        # A reload should capture a fresh fingerprint.
        svc.reload()
        self.assertFalse(svc.is_stale())


# ── ReloadPayload defaults ────────────────────────────────────

class PayloadDefaults(unittest.TestCase):

    def test_default_lists_are_empty(self):
        payload = ReloadPayload(vfs=None)   # type: ignore[arg-type]
        self.assertEqual(payload.groups, [])
        self.assertEqual(payload.discovered_palocs, [])
        self.assertEqual(payload.game_version, "")


# ── VfsManager.reload() behaviour (the real VFS, not the fake) ──

class VfsManagerReload(_BaseCase):
    """Exercise the new reload() method on core.vfs_manager itself."""

    def _make_real_vfs(self):
        from core.vfs_manager import VfsManager
        return VfsManager(str(self.root))

    def test_reload_resets_pamt_cache(self):
        vfs = self._make_real_vfs()
        # Prime the cache by pretending to load a group (we can't
        # parse a fake 0.pamt so just poke the cache directly).
        vfs._pamt_cache["0012"] = "fake_cached_data"   # type: ignore[assignment]
        vfs.reload()
        self.assertNotIn("0012", vfs._pamt_cache)

    def test_reload_clears_papgt_state(self):
        vfs = self._make_real_vfs()
        vfs._papgt_data = "stub"   # type: ignore[assignment]
        vfs.reload()
        self.assertIsNone(vfs._papgt_data)

    def test_reload_clears_warnings_dedup(self):
        vfs = self._make_real_vfs()
        vfs._logged_processing_warnings.add(("x", "y"))
        vfs.reload()
        self.assertEqual(len(vfs._logged_processing_warnings), 0)

    def test_reload_raises_when_packages_dir_disappears(self):
        vfs = self._make_real_vfs()
        # Burn the whole packages folder the user was pointing at.
        import shutil
        shutil.rmtree(self.root)
        with self.assertRaises(FileNotFoundError):
            vfs.reload()


if __name__ == "__main__":
    unittest.main()
