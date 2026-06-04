#!/usr/bin/env python3
"""Read the user's lobby.save + save.save and search for difficulty-shaped data.

Strategy: PA save files are typically a binary blob of a serialized
ReflectObject tree. Field names are usually present as inline ASCII tags.
We search for likely keys (DifficultyOption, _gameDifficulty, GameDifficulty,
_balanceDifficultyLevel, etc.) and dump the surrounding bytes so we can
guess the value type.
"""
from __future__ import annotations
import struct, sys, os
from pathlib import Path

SLOT = r"C:\Users\hzeem\AppData\Local\Pearl Abyss\CD\save\1740862637\slot0"
OUT = r"C:\Users\hzeem\Desktop\crimsonforge\tools\save_difficulty_dump.txt"

KEYS = [
    b"_gameDifficulty",
    b"GameDifficulty",
    b"DifficultyOption",
    b"DifficultyLevel",
    b"_difficulty",
    b"Difficulty",
    b"GameLevel",
    b"_balanceDifficultyLevel",
    b"_isApplyGameBalanceLevel",
    b"_difficultyOption",
    b"GamePlayLevel",
    b"PlayLevel",
    b"_gameDifficultyOption",
]

def dump_file(path: Path, out_lines: list):
    if not path.exists():
        out_lines.append(f"!! missing: {path}")
        return
    blob = path.read_bytes()
    out_lines.append(f"\n{'=' * 78}\n=== {path.name}  ({len(blob)} bytes)\n{'=' * 78}")

    # Print the leading 64 bytes as hex+ascii
    head = blob[:64]
    out_lines.append(f"  head: {head.hex()}")
    out_lines.append(f"  head.ascii: {''.join(chr(b) if 32 <= b < 127 else '.' for b in head)}")

    # Search for keys
    for k in KEYS:
        i = blob.find(k)
        while i >= 0:
            a = max(0, i - 16)
            b_ = min(len(blob), i + len(k) + 64)
            seg = blob[a:b_]
            ascii_seg = ''.join(chr(c) if 32 <= c < 127 else '.' for c in seg)
            # Try int / float at i + len(k) + N (k followed by null + 1 or 2 byte type)
            after = blob[i + len(k):i + len(k) + 24]
            ints  = []
            for off in range(0, min(20, len(after)) - 3, 1):
                v = struct.unpack_from("<I", after, off)[0]
                if 0 <= v <= 16:
                    ints.append((off, v))
            out_lines.append(f"\n  HIT @0x{i:08X}  key={k.decode()}")
            out_lines.append(f"    seg: {ascii_seg}")
            out_lines.append(f"    raw: {seg.hex()}")
            if ints:
                out_lines.append(f"    small uint32 @ +N: " + ", ".join(f"+{o}={v}" for o, v in ints[:8]))
            i = blob.find(k, i + 1)

def main():
    out_lines = []
    out_lines.append("=== USER SAVE DIFFICULTY SCAN ===")
    out_lines.append(f"Slot: {SLOT}")

    for name in ("lobby.save", "save.save"):
        dump_file(Path(SLOT) / name, out_lines)

    Path(OUT).write_text("\n".join(out_lines), encoding="utf-8")
    print(f"Wrote {OUT}")

if __name__ == "__main__":
    main()
