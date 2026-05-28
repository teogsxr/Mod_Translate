"""Hex-dump a single PAA file to inspect its raw bytes / string content.

Useful for diagnosing link-variant PAAs: shows the FULL header, every
ASCII string, and explicitly lists every .pab/.paa/.pac extension
reference found anywhere in the first 4KB.

Usage:
    python tools/probe_paa_link_bytes.py --game "<install>" --path "character/cd_damian_..._walk_f_ing_00.paa"
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def find_strings(data: bytes, min_len: int = 6) -> list[tuple[int, str]]:
    """Find printable-ASCII runs of at least min_len characters."""
    out = []
    cur = []
    cur_start = -1
    for i, b in enumerate(data):
        if 0x20 <= b <= 0x7E:
            if cur_start < 0:
                cur_start = i
            cur.append(chr(b))
        else:
            if len(cur) >= min_len:
                out.append((cur_start, "".join(cur)))
            cur = []
            cur_start = -1
    if len(cur) >= min_len:
        out.append((cur_start, "".join(cur)))
    return out


def hex_dump(data: bytes, start: int, length: int):
    end = min(start + length, len(data))
    for off in range(start, end, 16):
        chunk = data[off:off + 16]
        hex_str = ' '.join(f'{b:02x}' for b in chunk)
        ascii_str = ''.join(chr(b) if 0x20 <= b <= 0x7E else '.' for b in chunk)
        print(f"  {off:04x}  {hex_str:<48s}  {ascii_str}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--game", required=True)
    parser.add_argument("--path", required=True,
                        help="In-game path of PAA, e.g. 'character/cd_damian_..._walk_f_ing_00.paa'")
    args = parser.parse_args()

    from core.vfs_manager import VfsManager

    game_root = Path(args.game)
    if (game_root / "packages").is_dir():
        packages_dir = game_root / "packages"
    elif any(p.is_dir() and p.name.isdigit() for p in game_root.iterdir()):
        packages_dir = game_root
    else:
        print("Bad game path"); return 1

    vfs = VfsManager(str(packages_dir))
    for group in vfs.list_package_groups():
        try: vfs.load_pamt(group)
        except Exception: pass

    target = args.path.replace("\\", "/").lower()
    found_entry = None
    for _g, pamt in getattr(vfs, "_pamt_cache", {}).items():
        for entry in getattr(pamt, "file_entries", []):
            if (entry.path or "").replace("\\", "/").lower() == target:
                found_entry = entry
                break
        if found_entry: break
    if not found_entry:
        print(f"Path not found in VFS: {args.path}")
        return 1

    data = vfs.read_entry_data(found_entry)
    print(f"File: {args.path}")
    print(f"Size: {len(data):,} bytes")
    print(f"Header (first 32 bytes):")
    hex_dump(data, 0, 32)

    # Hex-dump in 256-byte chunks around regions of interest
    print(f"\nFull dump (first 512 bytes):")
    hex_dump(data, 0, 512)

    # Show ALL ASCII strings in first 1KB
    print(f"\nAll printable strings in first 1KB (min 6 chars):")
    for off, s in find_strings(data[:1024], min_len=6):
        print(f"  +{off:04x}  {s!r}")

    # Highlight extension references
    print(f"\nExtension references (.pab/.paa/.pac/.pam/.pamlod):")
    text = data[:4096].decode("ascii", errors="replace")
    for m in re.finditer(r"%[\w/]+?\.(pab|paa|pac|pam|pamlod|pabc|pabgb)", text):
        print(f"  +{m.start():04x}  {m.group()!r}")

    # And try the parser to see what it reports
    print(f"\nParser output:")
    from core.animation_parser import parse_paa
    anim = parse_paa(data, args.path)
    print(f"  is_link: {anim.is_link}")
    print(f"  link_target: {anim.link_target!r}")
    print(f"  bone_count: {anim.bone_count}")
    print(f"  frame_count: {anim.frame_count}")
    print(f"  duration: {anim.duration}")
    print(f"  metadata_tags: {anim.metadata_tags}")


if __name__ == "__main__":
    sys.exit(main() or 0)
