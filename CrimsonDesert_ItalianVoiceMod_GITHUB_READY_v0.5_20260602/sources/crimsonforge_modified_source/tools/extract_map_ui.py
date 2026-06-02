#!/usr/bin/env python3
"""Extract every map-related ui html/css/js file."""
from __future__ import annotations
import os, sys
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.vfs_manager import VfsManager


def main():
    vfs = VfsManager(r"C:\Program Files (x86)\Steam\steamapps\common\Crimson Desert")
    out = Path(__file__).parent / "flame_knight" / "ui"
    out.mkdir(parents=True, exist_ok=True)
    for grp in vfs.list_package_groups():
        try: pamt = vfs.load_pamt(grp)
        except Exception: continue
        for entry in pamt.file_entries:
            pl = entry.path.lower()
            keep = False
            if pl.startswith("ui/") and any(pl.endswith(e) for e in (".html",".htm",".css",".js",".json")):
                # narrow to map / observer / faction / knowledge / mission / hud panels
                if any(k in pl for k in ("map", "observer", "knowledge", "mission", "questlist",
                                          "questmemo", "questinfo", "knowledgebook", "faction")):
                    keep = True
            if not keep: continue
            try: data = vfs.read_entry_data(entry)
            except Exception: continue
            target = out / Path(entry.path).name
            target.write_bytes(data)
            print(f"  [{grp}] {len(data):>10}b  {entry.path}")
    print("done")


if __name__ == "__main__":
    main()
