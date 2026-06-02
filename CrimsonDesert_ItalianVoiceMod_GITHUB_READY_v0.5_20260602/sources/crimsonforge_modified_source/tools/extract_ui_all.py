#!/usr/bin/env python3
"""Extract every ui/*.html and ui/*.css to flame_knight/ui_all/"""
from __future__ import annotations
import os, sys
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.vfs_manager import VfsManager


def main():
    vfs = VfsManager(r"C:\Program Files (x86)\Steam\steamapps\common\Crimson Desert")
    out = Path(__file__).parent / "flame_knight" / "ui_all"
    out.mkdir(parents=True, exist_ok=True)
    n = 0
    for grp in vfs.list_package_groups():
        try: pamt = vfs.load_pamt(grp)
        except Exception: continue
        for entry in pamt.file_entries:
            pl = entry.path.lower()
            if not pl.startswith("ui/"): continue
            if not (pl.endswith(".html") or pl.endswith(".css") or pl.endswith(".htm")): continue
            try: data = vfs.read_entry_data(entry)
            except Exception: continue
            (out / Path(entry.path).name).write_bytes(data)
            n += 1
    print("extracted", n, "files to", out)


if __name__ == "__main__":
    main()
