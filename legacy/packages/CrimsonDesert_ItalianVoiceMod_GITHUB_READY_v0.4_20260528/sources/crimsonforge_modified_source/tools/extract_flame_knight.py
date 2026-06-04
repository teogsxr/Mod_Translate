#!/usr/bin/env python3
"""Extract every Flame Knight asset to disk for inspection.

Pulls (from the VFS, decrypted+decompressed):
  - character/mon_flame_knight.xml
  - character/cd_m0001_00_flameknight_nude_0001.{pac,pac_xml,prefab,prefabdata_xml,hkx}
  - all character/cd_flameknight_swds_*.paa (sword & shield animations)
  - actionchart/cd_flameknight_swds_*.paa_metabin
  - sequencer/cd_seq_quest_flame_knights_*.{paseq,paseqc,pastage,paschedule,paschedulepath}
"""
from __future__ import annotations
import argparse, os, sys
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.vfs_manager import VfsManager


PATTERNS = (
    "mon_flame_knight",
    "flame_knight",
    "flameknight",
)
SKIP_EXT = (
    ".dds", ".wem",  # skip texture and audio dumps to keep the trace tidy
)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--game",
        default=r"C:\Program Files (x86)\Steam\steamapps\common\Crimson Desert",
    )
    ap.add_argument("--out", default=str(Path(__file__).parent / "flame_knight" / "assets"))
    args = ap.parse_args()

    vfs = VfsManager(args.game)
    out_root = Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)

    extracted = 0
    seen_paths: set[str] = set()
    for group in vfs.list_package_groups():
        try:
            pamt = vfs.load_pamt(group)
        except Exception:
            continue
        for entry in pamt.file_entries:
            pl = entry.path.lower()
            if not any(p in pl for p in PATTERNS):
                continue
            if pl.endswith(SKIP_EXT):
                continue
            if pl in seen_paths:
                continue
            seen_paths.add(pl)

            try:
                data = vfs.read_entry_data(entry)
            except Exception as e:
                print(f"ERR read {entry.path}: {e}", file=sys.stderr)
                continue

            sub = entry.path.replace("\\", "/")
            target = out_root / sub
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            extracted += 1
            print(f"  [{group}] {len(data):>10}b  {sub}")

    print(f"\nExtracted {extracted} files to {out_root}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
