"""For each animated bone in Damian's walk PAA, compare:
  * PAB bind quaternion (from skeleton)
  * PAA frame 0 quaternion
  * PAA frame mid quaternion

Goal: determine the EXACT relationship between PAA stored quats and
bind. There are several possible conventions:
  (a) PAA frame 0 == identity (delta from bind, pre-multiply)
  (b) PAA frame 0 == bind     (absolute local rotation)
  (c) PAA frame 0 == bind^-1  (post-multiply delta)
  (d) Something else entirely

If the explosion is asymmetric (legs OK, upper body bad), then the
relationship may also be axis-dependent — e.g. PAA stores rotation
in BONE-LOCAL frame for legs (which have bind ≈ identity) but the
exporter applies it wrongly when bind is non-identity.

Usage:
    python tools/probe_paa_vs_bind.py
"""
from __future__ import annotations

import struct
import sys
import math
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.vfs_manager import VfsManager
from core.skeleton_parser import parse_pab
from core.animation_parser import parse_paa_with_resolution


def quat_mul(a, b):
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (
        aw*bx + ax*bw + ay*bz - az*by,
        aw*by - ax*bz + ay*bw + az*bx,
        aw*bz + ax*by - ay*bx + az*bw,
        aw*bw - ax*bx - ay*by - az*bz,
    )


def quat_conj(q):
    return (-q[0], -q[1], -q[2], q[3])


def quat_diff(a, b):
    """Returns the rotation R such that a*R = b (i.e., R = a^-1 * b)."""
    return quat_mul(quat_conj(a), b)


def quat_norm(q):
    return math.sqrt(sum(c*c for c in q))


def quat_dot(a, b):
    return sum(x*y for x, y in zip(a, b))


def quat_to_euler_deg(q):
    qx, qy, qz, qw = q
    sinr = 2 * (qw*qx + qy*qz)
    cosr = 1 - 2 * (qx*qx + qy*qy)
    roll = math.atan2(sinr, cosr)
    sinp = 2 * (qw*qy - qz*qx)
    if abs(sinp) >= 1:
        pitch = math.copysign(math.pi / 2, sinp)
    else:
        pitch = math.asin(sinp)
    siny = 2 * (qw*qz + qx*qy)
    cosy = 1 - 2 * (qy*qy + qz*qz)
    yaw = math.atan2(siny, cosy)
    return (math.degrees(roll), math.degrees(pitch), math.degrees(yaw))


def fmt_q(q):
    return f"({q[0]:+.4f},{q[1]:+.4f},{q[2]:+.4f},{q[3]:+.4f})"


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
    pab_data = vfs.read_entry_data(lookup(pab_path))
    paa_data = vfs.read_entry_data(lookup(paa_path))
    skel = parse_pab(pab_data, pab_path)

    # Extract PAB bone hashes for parser
    pab_hashes = []
    off = 0x17
    for _ in range(len(skel.bones)):
        if off + 4 > len(pab_data):
            break
        h = struct.unpack_from('<I', pab_data, off)[0] & 0x00FFFFFF
        name_len = pab_data[off + 3]
        pab_hashes.append(h)
        off += 4 + name_len + 4 + 256 + 40 + 1

    anim = parse_paa_with_resolution(
        paa_data, paa_path, vfs=vfs, max_hops=5,
        pab_bone_hashes=pab_hashes, pab_bone_count=len(skel.bones),
    )

    name_to_idx = {b.name: i for i, b in enumerate(skel.bones)}

    bones_to_examine = [
        "Bip01",                    # root
        "Bip01 Pelvis",
        "Bip01 Spine",
        "Bip01 L Thigh",            # leg (works)
        "Bip01 R Thigh",
        "Bip01 L Calf",
        "Bip01 Spine1",
        "Bip01 Spine2",
        "Bip01 Neck",
        "Bip01 Head",
        "Bip01 R Clavicle",
        "Bip01 L UpperArm",         # upper body (broken)
        "Bip01 R UpperArm",
        "Bip01 L Forearm",
        "Bip01 R Forearm",
        "Bip01 L Hand",
        "Bip01 R Hand",
    ]

    print(f"{'Bone':<20s}  {'bind_rot':<35s}  {'paa@0':<35s}  {'paa@mid':<35s}")
    print("=" * 130)
    for name in bones_to_examine:
        bi = name_to_idx.get(name)
        if bi is None:
            print(f"{name:<20s}  (not in skeleton)")
            continue
        b = skel.bones[bi]
        # Bone bind has matrices. The "local rotation" portion is what
        # we need. Use the local SRT if available.
        bind_q = None
        if hasattr(b, 'local_rotation'):
            bind_q = tuple(b.local_rotation)
        elif hasattr(b, 'rotation'):
            bind_q = tuple(b.rotation)
        else:
            # Try bind_matrix (4x4) — extract rotation
            if hasattr(b, 'bind_local'):
                M = b.bind_local
                # Convert 3x3 rotation block to quat
                m = [[M[r][c] for c in range(3)] for r in range(3)]
                trace = m[0][0] + m[1][1] + m[2][2]
                if trace > 0:
                    s = math.sqrt(trace + 1.0) * 2
                    qw = 0.25 * s
                    qx = (m[2][1] - m[1][2]) / s
                    qy = (m[0][2] - m[2][0]) / s
                    qz = (m[1][0] - m[0][1]) / s
                else:
                    if m[0][0] > m[1][1] and m[0][0] > m[2][2]:
                        s = math.sqrt(1.0 + m[0][0] - m[1][1] - m[2][2]) * 2
                        qw = (m[2][1] - m[1][2]) / s
                        qx = 0.25 * s
                        qy = (m[0][1] + m[1][0]) / s
                        qz = (m[0][2] + m[2][0]) / s
                    elif m[1][1] > m[2][2]:
                        s = math.sqrt(1.0 + m[1][1] - m[0][0] - m[2][2]) * 2
                        qw = (m[0][2] - m[2][0]) / s
                        qx = (m[0][1] + m[1][0]) / s
                        qy = 0.25 * s
                        qz = (m[1][2] + m[2][1]) / s
                    else:
                        s = math.sqrt(1.0 + m[2][2] - m[0][0] - m[1][1]) * 2
                        qw = (m[1][0] - m[0][1]) / s
                        qx = (m[0][2] + m[2][0]) / s
                        qy = (m[1][2] + m[2][1]) / s
                        qz = 0.25 * s
                bind_q = (qx, qy, qz, qw)
        if bind_q is None:
            bind_q = (0, 0, 0, 1)

        paa_0 = anim.keyframes[0].bone_rotations[bi] if bi < len(anim.keyframes[0].bone_rotations) else None
        paa_mid = anim.keyframes[len(anim.keyframes)//2].bone_rotations[bi] if bi < len(anim.keyframes[len(anim.keyframes)//2].bone_rotations) else None

        print(f"{name:<20s}  {fmt_q(bind_q):<35s}  {fmt_q(paa_0) if paa_0 else 'None':<35s}  {fmt_q(paa_mid) if paa_mid else 'None':<35s}")

        # Compute composition results to check delta vs absolute
        if paa_0 is not None:
            delta = quat_mul(bind_q, paa_0)
            print(f"  -> bind*paa@0  = {fmt_q(delta)}  euler={quat_to_euler_deg(delta)}")
            print(f"  -> paa@0       = {fmt_q(paa_0)}  euler={quat_to_euler_deg(paa_0)}")
            print(f"  -> paa@0*bind  = {fmt_q(quat_mul(paa_0, bind_q))}  euler={quat_to_euler_deg(quat_mul(paa_0, bind_q))}")
            d = quat_dot(paa_0, bind_q)
            print(f"  dot(paa0, bind) = {d:+.4f}  ({'NEAR-IDENTITY-DELTA' if abs(d-1) < 0.05 or abs(d+1) < 0.05 else 'NOT-IDENTITY'})")
            d2 = quat_dot(paa_0, (0,0,0,1))
            print(f"  dot(paa0, ID  ) = {d2:+.4f}  ({'PAA0=ID' if abs(d2-1) < 0.05 or abs(d2+1) < 0.05 else 'PAA0!=ID'})")


if __name__ == "__main__":
    main()
