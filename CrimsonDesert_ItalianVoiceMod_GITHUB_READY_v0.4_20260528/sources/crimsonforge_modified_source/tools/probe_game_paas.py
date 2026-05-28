"""Deep tracer — scan every .paa in your game install, classify each.

For each PAA found in the loaded VFS, parses it and reports:
  - link target (if it's a stub pointing elsewhere)
  - real frame/bone counts (if it's actual animation data)
  - tags / metadata

Helps you find PAAs with real data vs ones that are link-variants.
Prints a sorted summary at the end so you know what's worth using.

Usage:
    python tools/probe_game_paas.py --game "C:/Program Files (x86)/Steam/steamapps/common/Crimson Desert"

Optional filters:
    --filter walk          → only paths containing 'walk'
    --filter cd_phw_00     → only female-rig animations
    --char damian          → only PAAs related to Damian (best guess)
    --max 200              → cap number of PAAs to scan (default 500)

Output sample:
    REAL ANIMATIONS (data inside the PAA itself):
      character/cd_phw_00_walk_f_ing_00.paa     78 bones, 32 frames, 1.07s
      character/cd_phw_00_idle_00.paa           81 bones, 120 frames, 4.00s
    LINK ANIMATIONS (stub pointing elsewhere):
      character/cd_damian_..._walk_f_ing.paa    -> %character/.../phw_01.pab
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--game", required=True,
                        help="Path to Crimson Desert install")
    parser.add_argument("--filter", default="",
                        help="Only PAAs whose path contains this string")
    parser.add_argument("--char", default="",
                        help="Filter to PAAs related to a specific character "
                             "(matched in path or in resolved link targets)")
    parser.add_argument("--max", type=int, default=500,
                        help="Cap on number of PAAs to scan")
    args = parser.parse_args()

    # The game install can be given two ways:
    #   1. Path to the install root that contains numbered group dirs
    #      (0009/, 0012/, 0015/, ...) directly. This is what CrimsonForge's
    #      UI uses.
    #   2. Path to a 'packages' subdir in the same shape.
    # Auto-detect which structure we have.
    game_root = Path(args.game)
    if (game_root / "packages").is_dir():
        packages_dir = game_root / "packages"
    elif any(p.is_dir() and p.name.isdigit() for p in game_root.iterdir()):
        packages_dir = game_root
    else:
        print(f"ERROR: {game_root} does not look like a Crimson Desert install.")
        print(f"Expected either '{game_root}/packages' OR numbered group")
        print(f"folders (0009, 0015, ...) directly in {game_root}.")
        return 1

    print(f"Loading game VFS from: {packages_dir}")
    from core.vfs_manager import VfsManager
    vfs = VfsManager(str(packages_dir))

    # Skip load_papgt() — it requires <packages>/meta/0.papgt which not
    # every install has. list_package_groups() walks the dir directly
    # and finds every subdir containing 0.pamt, which is enough.
    print("Loading all PAMTs...")
    groups = vfs.list_package_groups()
    print(f"  Found {len(groups)} package group(s)")
    for group in groups:
        try:
            vfs.load_pamt(group)
        except Exception as e:
            print(f"  skip {group}: {e}")

    # Collect every .paa entry
    paa_entries = []
    seen = set()
    for _group, pamt in getattr(vfs, "_pamt_cache", {}).items():
        for entry in getattr(pamt, "file_entries", []):
            if not entry.path or entry.path in seen:
                continue
            if not entry.path.lower().endswith(".paa"):
                continue
            seen.add(entry.path)
            if args.filter and args.filter.lower() not in entry.path.lower():
                continue
            if args.char and args.char.lower() not in entry.path.lower():
                continue
            paa_entries.append(entry)

    print(f"\nFound {len(paa_entries)} matching .paa files in VFS")
    if len(paa_entries) > args.max:
        print(f"Scanning first {args.max} (use --max to override)")
        paa_entries = paa_entries[:args.max]

    from core.animation_parser import parse_paa, parse_paa_with_resolution

    real_anims = []   # (path, bones, frames, duration, tags)
    link_anims = []   # (path, target)
    failed_anims = [] # (path, error)

    for i, entry in enumerate(paa_entries):
        if i % 50 == 0:
            print(f"  scanning {i}/{len(paa_entries)}...")
        try:
            data = vfs.read_entry_data(entry)
            anim = parse_paa(data, entry.path)
            if anim.is_link and anim.link_target:
                link_anims.append((entry.path, anim.link_target))
            elif anim.frame_count > 1 and anim.bone_count > 1:
                real_anims.append((
                    entry.path, anim.bone_count, anim.frame_count,
                    anim.duration, ";".join(anim.metadata_tags or [])[:50],
                ))
            else:
                # Empty / unknown
                failed_anims.append(
                    (entry.path,
                     f"frames={anim.frame_count} bones={anim.bone_count}")
                )
        except Exception as exc:
            failed_anims.append((entry.path, f"parse error: {exc}"))

    # Sort: real anims by duration desc, link anims alphabetically
    real_anims.sort(key=lambda r: -r[3])
    link_anims.sort()

    print()
    print("=" * 80)
    print(f"REAL ANIMATIONS ({len(real_anims)}) — pick from these for export")
    print("=" * 80)
    for path, bones, frames, dur, tags in real_anims[:40]:
        print(f"  {path}")
        print(f"      {bones} bones, {frames} frames, {dur:.2f}s"
              + (f"  tags={tags!r}" if tags else ""))

    if len(real_anims) > 40:
        print(f"  ... and {len(real_anims) - 40} more")

    print()
    print("=" * 80)
    print(f"LINK STUBS ({len(link_anims)}) — point to other files")
    print("=" * 80)
    for path, target in link_anims[:20]:
        print(f"  {path}")
        print(f"      → {target}")
    if len(link_anims) > 20:
        print(f"  ... and {len(link_anims) - 20} more")

    if failed_anims:
        print()
        print("=" * 80)
        print(f"FAILED / EMPTY ({len(failed_anims)})")
        print("=" * 80)
        for path, reason in failed_anims[:10]:
            print(f"  {path}    {reason}")

    # Recommendations
    print()
    print("=" * 80)
    print("RECOMMENDATIONS for unified character export")
    print("=" * 80)
    if real_anims:
        print(f"  Top REAL animations to try:")
        for path, bones, frames, dur, _ in real_anims[:5]:
            print(f"    - {path}    ({bones} bones, {dur:.2f}s)")
    else:
        print(f"  No real animations matched filter '{args.filter}'.")
        print(f"  Try without --filter to see what exists.")

    # Also check: how does parse_paa_with_resolution help?
    if link_anims:
        print()
        print(f"  Testing link resolution on first 3 link stubs...")
        for path, target in link_anims[:3]:
            try:
                data = vfs.read_entry_data(
                    next(e for e in paa_entries if e.path == path))
                resolved = parse_paa_with_resolution(
                    data, path, vfs=vfs, max_hops=5)
                if resolved.frame_count > 1:
                    print(f"    ✓ {path}")
                    print(f"      resolved → {resolved.bone_count} bones, "
                          f"{resolved.frame_count} frames, "
                          f"{resolved.duration:.2f}s")
                else:
                    print(f"    ✗ {path}    (link unresolved or target empty)")
            except Exception as exc:
                print(f"    ✗ {path}    error: {exc}")


if __name__ == "__main__":
    main()
