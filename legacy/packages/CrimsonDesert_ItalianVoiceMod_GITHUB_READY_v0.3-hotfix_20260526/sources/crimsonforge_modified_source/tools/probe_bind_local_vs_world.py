"""For each bone in PAB, compare:
  * bone.rotation (the quaternion stored after the matrices)
  * Local rotation extracted from bind_matrix
  * "World" rotation extracted from bind_matrix * inv(parent.bind_matrix)

If bone.rotation == local extracted from bind_matrix → it's LOCAL space
If bone.rotation == world from bind_matrix → it's WORLD space

This is critical: the exporter needs LOCAL rotation to compose with PAA
deltas. Using WORLD when the format expects LOCAL (or vice versa) will
visually explode the bones.

Usage:
    python tools/probe_bind_local_vs_world.py
"""
from __future__ import annotations

import struct
import sys
import math
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.vfs_manager import VfsManager
from core.skeleton_parser import parse_pab


def quat_dot(a, b):
    return sum(x*y for x, y in zip(a, b))


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


def matrix_to_quat(m):
    """4x4 column-major flat → quat xyzw."""
    # Extract 3x3 rotation block. The matrix is column-major, so:
    # m[0..3] = column 0, m[4..7] = column 1, m[8..11] = column 2, m[12..15] = column 3
    # The 3x3 rotation block (excluding scale) is:
    # m00 = m[0], m10 = m[1], m20 = m[2]   (column 0)
    # m01 = m[4], m11 = m[5], m21 = m[6]   (column 1)
    # m02 = m[8], m12 = m[9], m22 = m[10]  (column 2)
    m00, m10, m20 = m[0], m[1], m[2]
    m01, m11, m21 = m[4], m[5], m[6]
    m02, m12, m22 = m[8], m[9], m[10]

    # Strip scale from each column
    sx = math.sqrt(m00*m00 + m10*m10 + m20*m20) or 1.0
    sy = math.sqrt(m01*m01 + m11*m11 + m21*m21) or 1.0
    sz = math.sqrt(m02*m02 + m12*m12 + m22*m22) or 1.0
    m00, m10, m20 = m00/sx, m10/sx, m20/sx
    m01, m11, m21 = m01/sy, m11/sy, m21/sy
    m02, m12, m22 = m02/sz, m12/sz, m22/sz

    trace = m00 + m11 + m22
    if trace > 0:
        s = math.sqrt(trace + 1.0) * 2
        qw = 0.25 * s
        qx = (m21 - m12) / s
        qy = (m02 - m20) / s
        qz = (m10 - m01) / s
    else:
        if m00 > m11 and m00 > m22:
            s = math.sqrt(1.0 + m00 - m11 - m22) * 2
            qw = (m21 - m12) / s
            qx = 0.25 * s
            qy = (m01 + m10) / s
            qz = (m02 + m20) / s
        elif m11 > m22:
            s = math.sqrt(1.0 + m11 - m00 - m22) * 2
            qw = (m02 - m20) / s
            qx = (m01 + m10) / s
            qy = 0.25 * s
            qz = (m12 + m21) / s
        else:
            s = math.sqrt(1.0 + m22 - m00 - m11) * 2
            qw = (m10 - m01) / s
            qx = (m02 + m20) / s
            qy = (m12 + m21) / s
            qz = 0.25 * s
    return (qx, qy, qz, qw)


def matrix_translation(m):
    return (m[12], m[13], m[14])


def fmt_q(q):
    return f"({q[0]:+.4f},{q[1]:+.4f},{q[2]:+.4f},{q[3]:+.4f})"


def fmt_v(v):
    return f"({v[0]:+8.3f},{v[1]:+8.3f},{v[2]:+8.3f})"


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

    pab_path = "character/phw_01.pab"
    pab_data = vfs.read_entry_data(lookup(pab_path))
    skel = parse_pab(pab_data, pab_path)

    bones_to_check = [
        "Bip01", "Bip01 Footsteps", "Bip01 Pelvis",
        "Bip01 Spine", "Bip01 Spine1", "Bip01 Spine2", "Bip01 Neck", "Bip01 Head",
        "Bip01 L Thigh", "Bip01 L Calf", "Bip01 L Foot",
        "Bip01 R Thigh", "Bip01 R Calf", "Bip01 R Foot",
        "Bip01 L Clavicle", "Bip01 L UpperArm", "Bip01 L Forearm", "Bip01 L Hand",
        "Bip01 R Clavicle", "Bip01 R UpperArm", "Bip01 R Forearm", "Bip01 R Hand",
    ]

    name_to_idx = {b.name: i for i, b in enumerate(skel.bones)}

    print(f"Compare bone.rotation vs quat extracted from bind_matrix")
    print(f"Looking for: are they EQUAL (matrix is local) or DIFFERENT (matrix is world)?")
    print()
    print(f"{'bone':<22s}  {'parent':<22s}  {'.rotation':<35s}  {'matrix-to-quat':<35s}  {'pos':<28s}")
    print("=" * 160)

    for name in bones_to_check:
        bi = name_to_idx.get(name)
        if bi is None:
            continue
        b = skel.bones[bi]
        parent_name = "ROOT" if b.parent_index < 0 else skel.bones[b.parent_index].name
        rot = b.rotation
        if b.bind_matrix:
            mat_q = matrix_to_quat(b.bind_matrix)
            mat_pos = matrix_translation(b.bind_matrix)
        else:
            mat_q = (0, 0, 0, 1)
            mat_pos = (0, 0, 0)

        # Check if rot ≈ mat_q (or its antipode, since q and -q are same rotation)
        d = quat_dot(rot, mat_q)
        same = abs(abs(d) - 1.0) < 0.01
        label = " EQUAL" if same else " DIFFER"

        print(f"{name:<22s}  {parent_name:<22s}  {fmt_q(rot):<35s}  {fmt_q(mat_q):<35s}  {fmt_v(mat_pos):<28s}{label}")

        # If they differ, compute world rotation from matrix * inverse(parent matrix)
        if not same and b.parent_index >= 0:
            parent = skel.bones[b.parent_index]
            if parent.bind_matrix:
                par_q = matrix_to_quat(parent.bind_matrix)
                # local quat from world: par_inv * world
                local_from_world = quat_mul(quat_conj(par_q), mat_q)
                d2 = quat_dot(rot, local_from_world)
                same2 = abs(abs(d2) - 1.0) < 0.01
                label2 = " <-- ROT IS WORLD-SPACE!" if same2 else " (still differ)"
                print(f"  {'parent_inv * world matrix => local quat:':<60s} {fmt_q(local_from_world):<35s}{label2}")


if __name__ == "__main__":
    main()
