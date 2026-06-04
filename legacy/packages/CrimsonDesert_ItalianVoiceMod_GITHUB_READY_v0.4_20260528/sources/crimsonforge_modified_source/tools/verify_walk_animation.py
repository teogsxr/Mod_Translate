"""End-to-end verification: parse Damian walk PAA + skeleton, then
print which bones are animated and show their first/middle/last
rotations. A correct walk animation should have:
  * L Thigh / R Thigh moving in OPPOSITE phase
  * L Calf  / R Calf  moving in OPPOSITE phase
  * Spine + Pelvis with small periodic motion
  * Arms swinging opposite to same-side leg

If hash mapping is wrong, the motion appears on the wrong bones
(e.g. Spine moves like a thigh) — easy to spot.

Usage:
    python tools/verify_walk_animation.py
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.vfs_manager import VfsManager
from core.skeleton_parser import parse_pab
from core.animation_parser import parse_paa_with_resolution


def quat_to_euler_deg(q):
    """Approximate XYZ Euler in degrees from a unit quat."""
    import math
    qx, qy, qz, qw = q
    sinr_cosp = 2 * (qw * qx + qy * qz)
    cosr_cosp = 1 - 2 * (qx * qx + qy * qy)
    roll = math.atan2(sinr_cosp, cosr_cosp)
    sinp = 2 * (qw * qy - qz * qx)
    if abs(sinp) >= 1:
        pitch = math.copysign(math.pi / 2, sinp)
    else:
        pitch = math.asin(sinp)
    siny_cosp = 2 * (qw * qz + qx * qy)
    cosy_cosp = 1 - 2 * (qy * qy + qz * qz)
    yaw = math.atan2(siny_cosp, cosy_cosp)
    return (math.degrees(roll), math.degrees(pitch), math.degrees(yaw))


def main():
    game = Path(r"C:\Program Files (x86)\Steam\steamapps\common\Crimson Desert")
    pkg = game / "packages" if (game / "packages").is_dir() else game
    vfs = VfsManager(str(pkg))
    for g in vfs.list_package_groups():
        try:
            vfs.load_pamt(g)
        except Exception:
            pass

    def lookup(pth):
        target = pth.replace("\\", "/").lower()
        for _g, pamt in vfs._pamt_cache.items():
            for e in pamt.file_entries:
                if (e.path or "").replace("\\", "/").lower() == target:
                    return e
        return None

    paa_path = "character/cd_damian_rd_sg_basic_01_01_nor_move_walk_f_ing_00.paa"
    pab_path = "character/phw_01.pab"

    paa_data = vfs.read_entry_data(lookup(paa_path))
    pab_data = vfs.read_entry_data(lookup(pab_path))

    skel = parse_pab(pab_data, pab_path)
    pab_hashes = []
    off = 0x17
    for _ in range(len(skel.bones)):
        if off + 4 > len(pab_data):
            break
        h = struct.unpack_from('<I', pab_data, off)[0] & 0x00FFFFFF
        name_len = pab_data[off + 3]
        pab_hashes.append(h)
        off += 4 + name_len + 4 + 256 + 40 + 1

    print(f"Skeleton: {len(skel.bones)} bones from {pab_path}")

    anim = parse_paa_with_resolution(
        paa_data, paa_path, vfs=vfs, max_hops=5,
        pab_bone_hashes=pab_hashes,
        pab_bone_count=len(skel.bones),
    )
    print(f"Animation: {anim.frame_count} frames, {anim.bone_count} bones, "
          f"{anim.duration:.2f}s")

    if not anim.keyframes:
        print("NO KEYFRAMES — abort.")
        return

    # Find which bones actually animate
    animated = []
    for bi in range(min(anim.bone_count, len(skel.bones))):
        q0 = anim.keyframes[0].bone_rotations[bi]
        q_mid = anim.keyframes[len(anim.keyframes) // 2].bone_rotations[bi]
        q_end = anim.keyframes[-1].bone_rotations[bi]
        diff = sum(abs(a - b) for a, b in zip(q0, q_mid))
        if diff > 0.01:
            animated.append((bi, skel.bones[bi].name, q0, q_mid, q_end, diff))

    print(f"\n{'#':>3s} {'name':<28s}  diff  {'frame 0 (XYZ deg)':>22s}  {'frame mid (XYZ deg)':>22s}")
    print("-" * 105)
    for bi, name, q0, qm, qe, diff in animated:
        e0 = quat_to_euler_deg(q0)
        em = quat_to_euler_deg(qm)
        print(f"{bi:>3d} {name:<28s}  {diff:.3f}  "
              f"({e0[0]:+7.1f},{e0[1]:+7.1f},{e0[2]:+7.1f})  "
              f"({em[0]:+7.1f},{em[1]:+7.1f},{em[2]:+7.1f})")

    # ── Walk-correctness check ──
    # In a walking gait, L Thigh and R Thigh should be in opposite
    # phase: when L is forward (positive pitch around X), R is back.
    print(f"\n=== WALK SANITY CHECK ===")
    name_to_bi = {b.name: i for i, b in enumerate(skel.bones)}

    pairs = [
        ("Bip01 L Thigh", "Bip01 R Thigh"),
        ("Bip01 L Calf",  "Bip01 R Calf"),
        ("Bip01 L Foot",  "Bip01 R Foot"),
        ("Bip01 L UpperArm", "Bip01 R UpperArm"),
        ("Bip01 L Forearm",  "Bip01 R Forearm"),
    ]
    for ln, rn in pairs:
        li = name_to_bi.get(ln)
        ri = name_to_bi.get(rn)
        if li is None or ri is None:
            print(f"  {ln} / {rn}: bone(s) missing from PAB")
            continue
        # Sample at 1/4 and 3/4 of the animation
        f_q = len(anim.keyframes) // 4
        f_3q = 3 * len(anim.keyframes) // 4
        l_q  = anim.keyframes[f_q].bone_rotations[li]
        l_3q = anim.keyframes[f_3q].bone_rotations[li]
        r_q  = anim.keyframes[f_q].bone_rotations[ri]
        r_3q = anim.keyframes[f_3q].bone_rotations[ri]
        l_diff = sum(abs(a - b) for a, b in zip(l_q, l_3q))
        r_diff = sum(abs(a - b) for a, b in zip(r_q, r_3q))
        # Cross-check: if both move, do their X-component changes
        # have OPPOSITE signs (the walking-pendulum signature)?
        l_dx = l_3q[0] - l_q[0]
        r_dx = r_3q[0] - r_q[0]
        same_phase = (l_dx * r_dx) > 0
        opp_phase = (l_dx * r_dx) < 0
        marker = "OPPOSITE-PHASE OK" if opp_phase else (
            "SAME-PHASE (suspect)" if same_phase else "STATIC"
        )
        print(f"  {ln:>20s} (#{li:>3d}) move={l_diff:.3f}  dx={l_dx:+.4f}")
        print(f"  {rn:>20s} (#{ri:>3d}) move={r_diff:.3f}  dx={r_dx:+.4f}  -> {marker}")


if __name__ == "__main__":
    main()
