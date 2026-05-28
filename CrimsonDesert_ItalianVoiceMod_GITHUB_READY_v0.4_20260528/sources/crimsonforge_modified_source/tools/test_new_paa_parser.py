"""Quick test: re-parse Damian's walk PAA with the new layout decoder."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.vfs_manager import VfsManager
from core.animation_parser import parse_paa, parse_paa_with_resolution


def main():
    game = Path("C:/Program Files (x86)/Steam/steamapps/common/Crimson Desert")
    vfs = VfsManager(str(game))
    for g in vfs.list_package_groups():
        try:
            vfs.load_pamt(g)
        except Exception:
            pass

    target_paths = [
        "character/cd_damian_rd_sg_basic_01_01_nor_move_walk_f_ing_00.paa",
        "character/cd_damian_rd_sg_basic_01_01_nor_move_walk_f_stt_00.paa",
        "character/cd_damian_lk_cn_01_01_nor_move_walk_f_ing_00.paa",
    ]

    for target in target_paths:
        print()
        print("=" * 70)
        print(f"PAA: {target}")
        print("=" * 70)
        entry = None
        for _g, p in vfs._pamt_cache.items():
            for e in p.file_entries:
                norm = (e.path or "").replace("\\", "/").lower()
                if norm == target.lower():
                    entry = e
                    break
            if entry:
                break
        if entry is None:
            print(f"  NOT FOUND in VFS")
            continue

        data = vfs.read_entry_data(entry)
        anim = parse_paa(data, target)

        print(f"  is_link:     {anim.is_link}")
        print(f"  link_target: {anim.link_target!r}")
        print(f"  bone_count:  {anim.bone_count}")
        print(f"  frame_count: {anim.frame_count}")
        print(f"  duration:    {anim.duration:.3f}s")

        if anim.bone_count > 1 and anim.frame_count > 1:
            print(f"\n  Per-bone first/last quaternions (first 3 bones):")
            for bi in range(min(3, anim.bone_count)):
                kf0 = anim.keyframes[0]
                kfN = anim.keyframes[-1]
                q0 = kf0.bone_rotations[bi] if bi < len(kf0.bone_rotations) else None
                qN = kfN.bone_rotations[bi] if bi < len(kfN.bone_rotations) else None
                if q0 and qN:
                    mag0 = sum(c * c for c in q0) ** 0.5
                    magN = sum(c * c for c in qN) ** 0.5
                    print(f"    bone[{bi}] frame 0:    "
                          f"({q0[0]:+.4f}, {q0[1]:+.4f}, {q0[2]:+.4f}, {q0[3]:+.4f}) mag={mag0:.4f}")
                    print(f"    bone[{bi}] frame {anim.frame_count - 1}: "
                          f"({qN[0]:+.4f}, {qN[1]:+.4f}, {qN[2]:+.4f}, {qN[3]:+.4f}) mag={magN:.4f}")

            # Check that some bones change between frame 0 and last
            changed = 0
            for bi in range(anim.bone_count):
                q0 = anim.keyframes[0].bone_rotations[bi] if bi < len(anim.keyframes[0].bone_rotations) else None
                qN = anim.keyframes[-1].bone_rotations[bi] if bi < len(anim.keyframes[-1].bone_rotations) else None
                if q0 and qN:
                    diff = sum(abs(a - b) for a, b in zip(q0, qN))
                    if diff > 0.01:
                        changed += 1
            print(f"\n  Bones that animate (q changes between f=0 and f={anim.frame_count - 1}): "
                  f"{changed} of {anim.bone_count}")
            print(f"  → {'✓ MOTION' if changed > 5 else '⚠ MOSTLY STATIC'}")


if __name__ == "__main__":
    main()
