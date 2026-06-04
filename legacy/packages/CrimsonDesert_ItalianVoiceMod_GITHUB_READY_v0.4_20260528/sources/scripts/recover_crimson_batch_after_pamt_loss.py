"""Recover Crimson Desert 0006 TTS patches after a corrupted PAMT write.

The long Generate All + Patch batch leaves one temporary WEM per completed row.
If the game loses power while CrimsonForge is replacing ``0006/0.pamt``, the
manifest and those WEMs can be replayed from the last good backup without
running OmniVoice again.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path


REPO = Path(r"C:\Users\matte\Downloads\crimsonforge-latest")
GAME_ROOT = Path(r"C:\Program Files (x86)\Steam\steamapps\common\Crimson Desert")
BACKUP_DIR = Path(
    r"C:\Program Files (x86)\Steam\steamapps\common\crimsonforge_backups"
    r"\20260521_230314"
)
MANIFEST_PATH = Path(r"C:\Users\matte\.crimsonforge\tts_patch_progress.json")
TEMP_ROOT = Path(r"C:\Users\matte\AppData\Local\Temp")
BACKUP_CUT_UTC = datetime.fromisoformat("2026-05-21T21:03:14+00:00")

GAME_PAMT = GAME_ROOT / "0006" / "0.pamt"
GAME_PAPGT = GAME_ROOT / "meta" / "0.papgt"
BACKUP_PAMT = BACKUP_DIR / "0006_0.pamt"
BACKUP_PAPGT = BACKUP_DIR / "meta_0.papgt"
RECOVERY_ROOT = Path(r"C:\Users\matte\.crimsonforge\recovery")
WEM_NAME_RE = re.compile(r"^cf_wem_tts_(\d+)\.wem$")


def _load_replay_rows() -> list[tuple[str, datetime]]:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    completed = next(iter(manifest["games"].values()))["completed"]
    rows: list[tuple[str, datetime]] = []
    for key, value in completed.items():
        completed_at = value.get("completed_at") if isinstance(value, dict) else None
        if not completed_at:
            continue
        timestamp = datetime.fromisoformat(completed_at)
        if timestamp >= BACKUP_CUT_UTC and key.startswith("0006:"):
            rows.append((key, timestamp))
    return rows


def _load_temp_wems() -> list[tuple[int, Path]]:
    wems: list[tuple[int, Path]] = []
    for path in TEMP_ROOT.glob("cf_wem_tts_*.wem"):
        match = WEM_NAME_RE.match(path.name)
        if match:
            wems.append((int(match.group(1)), path))
    return sorted(wems, key=lambda item: item[0])


def _recovery_snapshot_dir() -> Path:
    return RECOVERY_ROOT / datetime.now().strftime("pamt_loss_%Y%m%d_%H%M%S")


def _snapshot_current_indices() -> Path:
    snapshot = _recovery_snapshot_dir()
    snapshot.mkdir(parents=True, exist_ok=False)
    shutil.copy2(GAME_PAMT, snapshot / "corrupt_0006_0.pamt")
    shutil.copy2(GAME_PAPGT, snapshot / "pre_recovery_meta_0.papgt")
    return snapshot


def _restore_backup_indices() -> None:
    shutil.copy2(BACKUP_PAMT, GAME_PAMT)
    shutil.copy2(BACKUP_PAPGT, GAME_PAPGT)


def _replay_wems(rows: list[tuple[str, datetime]], wems: list[tuple[int, Path]]) -> None:
    sys.path.insert(0, str(REPO))
    from core.pamt_parser import parse_pamt
    from core.repack_engine import ModifiedFile, RepackEngine

    pamt = parse_pamt(str(GAME_PAMT))
    entries_by_path = {entry.path.lower(): entry for entry in pamt.file_entries}

    modified_files = []
    missing_entries = []
    for (key, _timestamp), (_wem_ms, wem_path) in zip(rows, wems):
        _group, entry_path = key.split(":", 1)
        entry = entries_by_path.get(entry_path.lower())
        if entry is None:
            missing_entries.append(entry_path)
            continue
        modified_files.append(
            ModifiedFile(
                data=wem_path.read_bytes(),
                entry=entry,
                pamt_data=pamt,
                package_group="0006",
            )
        )

    if missing_entries:
        sample = "\n".join(missing_entries[:10])
        raise RuntimeError(
            f"Cannot replay {len(missing_entries)} rows missing from backup PAMT. "
            f"First paths:\n{sample}"
        )

    result = RepackEngine(str(GAME_ROOT)).repack(
        modified_files,
        papgt_path=str(GAME_PAPGT),
        create_backup=False,
        verify_after=True,
        preserve_timestamps=True,
    )
    if not result.success:
        raise RuntimeError(f"Repack verification failed: {result.errors}")
    print(f"Replayed {result.files_repacked} temporary WEM files.")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Snapshot current indices, restore the last good indices, and replay WEMs.",
    )
    args = parser.parse_args()

    required = [REPO, GAME_PAMT, GAME_PAPGT, BACKUP_PAMT, BACKUP_PAPGT, MANIFEST_PATH]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise FileNotFoundError("Missing recovery inputs:\n" + "\n".join(missing))

    rows = _load_replay_rows()
    wems = _load_temp_wems()
    print(f"Manifest rows after backup: {len(rows)}")
    print(f"Temporary WEM files found: {len(wems)}")
    if len(rows) != len(wems):
        raise RuntimeError("Manifest/WEM count mismatch; refusing to replay.")

    first_row, first_time = rows[0]
    first_wem_ms, first_wem = wems[0]
    last_row, last_time = rows[-1]
    last_wem_ms, last_wem = wems[-1]
    print(f"First row: {first_time.isoformat()} {first_row} <- {first_wem.name} ({first_wem_ms})")
    print(f"Last row : {last_time.isoformat()} {last_row} <- {last_wem.name} ({last_wem_ms})")

    if not args.apply:
        print("Dry run only. Pass --apply to perform recovery.")
        return 0

    snapshot = _snapshot_current_indices()
    print(f"Saved pre-recovery indices to {snapshot}")
    _restore_backup_indices()
    print(f"Restored index backup from {BACKUP_DIR}")
    _replay_wems(rows, wems)
    print("Recovery complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
