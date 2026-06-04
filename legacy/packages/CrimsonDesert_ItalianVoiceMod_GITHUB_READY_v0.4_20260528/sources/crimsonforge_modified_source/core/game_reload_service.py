"""Coordinator for "Reload Game Files" across every tab.

Problem
-------
Every tab caches its own view of the game state via an
``initialize_from_game`` call — Explorer keeps ``self._vfs`` +
``self._all_groups``, Translate keeps ``self._discovered_palocs``,
Item/Dialogue Catalog keep pre-built indexes, etc. After the user
patches the game, runs Steam Verify, or edits files with a
separate tool, every one of those caches is stale. Previously the
user had to close + reopen the app to see current files — which
dropped any in-flight work (translation project, open dialogs,
selected Repack items).

Solution
--------
:class:`GameReloadService` owns the reload pipeline:

  1. Run a full :meth:`VfsManager.reload` so every subsequent
     PAMT/PAPGT read comes fresh off disk.
  2. Re-discover paloc localisation files (languages may have
     been added or removed by a patch).
  3. Re-read the game-version metadata (0.paver).
  4. Fan out to every registered tab's ``reload_from_game``
     callback so the tab can refresh its own caches without
     losing per-tab user work.

Tabs register themselves at main-window construction time by
calling :meth:`register_tab`. The callback signature is a thin
wrapper the tab owns — one callable that knows how to refresh the
tab's state given the (possibly-updated) shared game resources.

Staleness detection
-------------------
:meth:`snapshot_disk_fingerprint` records the mtime + size of
every sensitive file (``meta/0.papgt``, ``meta/0.paver``, every
``NNNN/0.pamt``). :meth:`is_stale` compares the current
fingerprint to a prior snapshot. Cheap enough (~30 stat calls on
the stock game layout) to poll every few seconds from a
``QFileSystemWatcher`` or UI timer.

Thread safety
-------------
All public methods are safe to call from the UI thread. Reload
itself is synchronous — callers that want background execution
should wrap in a :class:`FunctionWorker`.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from core.vfs_manager import VfsManager
from utils.logger import get_logger

logger = get_logger("core.game_reload_service")


# ── Disk-fingerprint helpers ─────────────────────────────────────

@dataclass(frozen=True)
class _FileStamp:
    """Minimal stat signature used to detect on-disk changes."""
    path: str
    mtime_ns: int
    size: int


@dataclass
class DiskFingerprint:
    """Snapshot of every game file we watch for change detection."""
    captured_unix: float = 0.0
    stamps: tuple[_FileStamp, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.stamps)


def _stamp(path: Path) -> Optional[_FileStamp]:
    try:
        st = path.stat()
    except OSError:
        return None
    return _FileStamp(
        path=str(path),
        mtime_ns=getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9)),
        size=st.st_size,
    )


# ── Tab registration ─────────────────────────────────────────────

@dataclass
class _TabHook:
    """One registered tab's reload callback + label (for logs)."""
    label: str
    callback: Callable[[], None]


# ── Payload passed to tab reload callbacks ───────────────────────

@dataclass
class ReloadPayload:
    """Shared game resources handed to every tab on reload.

    Tabs pull whatever they need off this payload; the service
    builds it once per reload so every tab sees a consistent
    snapshot of the refreshed state.
    """
    vfs: VfsManager
    groups: list[str] = field(default_factory=list)
    discovered_palocs: list[dict] = field(default_factory=list)
    game_version: str = ""


# ── Service ───────────────────────────────────────────────────────

class GameReloadService:
    """Central coordinator for refreshing game state across tabs.

    One instance lives in :class:`ui.main_window.MainWindow`. Tabs
    register their refresh callback at construction time and the
    main window calls :meth:`reload` when the user hits the Reload
    Game button or the file watcher fires.
    """

    def __init__(
        self,
        vfs: Optional[VfsManager] = None,
        discover_palocs: Optional[Callable[[VfsManager], list[dict]]] = None,
        read_game_version: Optional[Callable[[Path], str]] = None,
    ):
        self._vfs = vfs
        self._discover_palocs = discover_palocs or (lambda _v: [])
        self._read_game_version = read_game_version or (lambda _p: "")
        self._tabs: list[_TabHook] = []
        self._last_fingerprint: Optional[DiskFingerprint] = None

    # ── Mutators ─────────────────────────────────────

    def bind_vfs(self, vfs: VfsManager) -> None:
        """Attach / replace the VFS this service coordinates.

        Called on initial game load so the service can compute
        fingerprints and drive reloads. Replacing a previously-
        bound VFS is legal — useful for tests or when the user
        picks a different game path.
        """
        self._vfs = vfs
        # Capturing the fingerprint here seeds staleness detection
        # so :meth:`is_stale` is False immediately after bind.
        self._last_fingerprint = self.snapshot_disk_fingerprint()

    def register_tab(
        self,
        label: str,
        callback: Callable[[ReloadPayload], None],
    ) -> None:
        """Register a tab's reload callback.

        ``callback`` is invoked on every :meth:`reload` with the
        fresh :class:`ReloadPayload`. Duplicate labels are logged
        but permitted — same tab registering twice is not a crash,
        just a wasted call.
        """
        if not label or not callable(callback):
            raise ValueError("label and callback are both required")
        # We wrap the real callback in a zero-arg shim so the
        # service's internal list doesn't need the payload type
        # at storage time.
        def _invoke_with_payload(payload: ReloadPayload, cb=callback):
            cb(payload)
        hook = _TabHook(
            label=label,
            callback=lambda payload, f=_invoke_with_payload: f(payload),
        )
        self._tabs.append(hook)
        logger.info("GameReloadService: registered tab '%s'", label)

    def unregister_tab(self, label: str) -> int:
        """Remove every registered callback with this label.

        Returns the number removed. Safe to call even if no
        callback by that label exists.
        """
        before = len(self._tabs)
        self._tabs = [h for h in self._tabs if h.label != label]
        removed = before - len(self._tabs)
        if removed:
            logger.info("GameReloadService: removed %d '%s' hook(s)", removed, label)
        return removed

    # ── Read-only ───────────────────────────────────

    @property
    def vfs(self) -> Optional[VfsManager]:
        return self._vfs

    @property
    def registered_tab_labels(self) -> list[str]:
        return [h.label for h in self._tabs]

    # ── Fingerprint / staleness ─────────────────────

    def _files_to_fingerprint(self) -> list[Path]:
        """Every file whose change signals the VFS is stale.

        Includes the PAPGT, PAVER, and every PAMT. We stat PAZ
        files on demand via the PAMT (they're stable between
        patches), so we don't include them here — saves ~17 stat
        calls per poll.
        """
        if self._vfs is None:
            return []
        root = Path(self._vfs.packages_path)
        if not root.is_dir():
            return []
        paths: list[Path] = []
        meta = root / "meta"
        for name in ("0.papgt", "0.paver"):
            p = meta / name
            if p.is_file():
                paths.append(p)
        # Every group's PAMT.
        for group_dir in sorted(root.iterdir()):
            if not group_dir.is_dir():
                continue
            pamt = group_dir / "0.pamt"
            if pamt.is_file():
                paths.append(pamt)
        return paths

    def snapshot_disk_fingerprint(self) -> DiskFingerprint:
        """Capture the current on-disk state for later comparison."""
        stamps = tuple(
            s for s in (_stamp(p) for p in self._files_to_fingerprint())
            if s is not None
        )
        fp = DiskFingerprint(
            captured_unix=time.time(),
            stamps=stamps,
        )
        self._last_fingerprint = fp
        return fp

    def is_stale(
        self,
        baseline: Optional[DiskFingerprint] = None,
    ) -> bool:
        """Return True if the current disk state differs from ``baseline``.

        With ``baseline=None`` we compare against the most recent
        fingerprint captured by :meth:`snapshot_disk_fingerprint`
        (typically set during the last :meth:`bind_vfs` or
        :meth:`reload`).

        Missing / added files count as stale. This is intentionally
        strict — a removed PAMT means a package group vanished,
        which absolutely invalidates our indexes.
        """
        ref = baseline if baseline is not None else self._last_fingerprint
        if ref is None:
            # Never took a snapshot — treat as stale so the caller
            # does the safe thing and reloads.
            return True
        current = tuple(
            s for s in (_stamp(p) for p in self._files_to_fingerprint())
            if s is not None
        )
        return current != ref.stamps

    # ── Reload ──────────────────────────────────────

    def reload(
        self,
        on_progress: Optional[Callable[[str], None]] = None,
    ) -> ReloadPayload:
        """Rebuild VFS state and fan out to every registered tab.

        Returns the :class:`ReloadPayload` used for the fan-out so
        the caller can use it too (the main window does: it also
        wants the refreshed game version for its status bar).

        ``on_progress`` receives short status strings suitable for
        a progress dialog / status bar. Safe to pass ``None``.

        A tab callback that raises is logged but does NOT abort
        the fan-out — one broken tab shouldn't prevent the others
        from seeing the fresh state.
        """
        if self._vfs is None:
            raise RuntimeError(
                "GameReloadService.reload called with no bound VFS. "
                "Call bind_vfs() first (typically at initial game load)."
            )

        def _tell(msg: str) -> None:
            if on_progress is not None:
                try:
                    on_progress(msg)
                except Exception:   # pragma: no cover - defensive
                    pass
            logger.info("game reload: %s", msg)

        t0 = time.perf_counter()

        _tell("Clearing VFS caches...")
        self._vfs.reload()

        _tell("Reading PAPGT root index...")
        self._vfs.load_papgt()

        _tell("Discovering package groups...")
        groups = self._vfs.list_package_groups()

        _tell(f"Loading {len(groups)} package group(s)...")
        # Force-load every group so the fan-out targets see a
        # fully populated VFS — otherwise the first tab that asks
        # for a particular group's PAMT pays the cold-load cost
        # and holds up the UI.
        for group_dir in groups:
            try:
                self._vfs.load_pamt(group_dir)
            except Exception as exc:
                logger.warning(
                    "game reload: load_pamt(%s) failed: %s",
                    group_dir, exc,
                )

        _tell("Re-discovering paloc localisation files...")
        try:
            discovered = self._discover_palocs(self._vfs)
        except Exception as exc:
            logger.exception("game reload: discover_palocs failed: %s", exc)
            discovered = []

        _tell("Reading game version...")
        try:
            version = self._read_game_version(Path(self._vfs.packages_path))
        except Exception as exc:
            logger.warning("game reload: read_game_version failed: %s", exc)
            version = ""

        payload = ReloadPayload(
            vfs=self._vfs,
            groups=groups,
            discovered_palocs=discovered,
            game_version=version,
        )

        _tell(f"Refreshing {len(self._tabs)} tab(s)...")
        for hook in self._tabs:
            try:
                hook.callback(payload)
            except Exception as exc:
                # Don't abort the fan-out on a single-tab failure.
                logger.exception(
                    "game reload: tab '%s' reload callback failed: %s",
                    hook.label, exc,
                )

        # Capture a fresh fingerprint so :meth:`is_stale` returns
        # False until the disk state changes again.
        self._last_fingerprint = self.snapshot_disk_fingerprint()

        elapsed = time.perf_counter() - t0
        _tell(f"Reload complete ({elapsed:.2f} s).")
        return payload
