"""Test the hypothesis: PAA stores ABSOLUTE local rotations (replaces
bind), not deltas (composed with bind).

For each bone, compute what the LOCAL rotation should be at frame 1
under three interpretations:
  (A) bind * paa  -- delta (current exporter)
  (B) paa         -- absolute (exporter would skip bind)
  (C) paa * bind  -- post-multiply delta

Then compute the WORLD rotation chain (parent.world * local) for both
to see which gives anatomically reasonable bone orientations.

A bone vector points along +Y in its local frame. After applying the
rotation chain, where does the bone vector point in world space?
For a walking character:
  * L Thigh should point DOWN (world -Z if Z-up, or world -Y if Y-up)
  * L UpperArm should point DOWN-ish (slight outward angle)

Usage:
    python tools/probe_paa_absolute_test.py
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
    ax,ay,az,aw = a
    bx,by,bz,bw = b
    return (
        aw*bx + ax*bw + ay*bz - az*by,
        aw*by - ax*bz + ay*bw + az*bx,
        aw*bz + ax*by - ay*bx + az*bw,
        aw*bw - ax*bx - ay*by - az*bz,
    )


def quat_rotate_vec3(q, v):
    """Rotate a 3-vector by a quaternion."""
    qx, qy, qz, qw = q
    vx, vy, vz = v
    # v' = q * (v, 0) * conj(q) — using the formula:
    # v' = v + 2 * cross(q.xyz, cross(q.xyz, v) + q.w * v)
    tx = 2 * (qy*vz - qz*vy)
    ty = 2 * (qz*vx - qx*vz)
    tz = 2 * (qx*vy - qy*vx)
    rx = vx + qw*tx + (qy*tz - qz*ty)
    ry = vy + qw*ty + (qz*tx - qx*tz)
    rz = vz + qw*tz + (qx*ty - qy*tx)
    return (rx, ry, rz)


def fmt_v(v):
    return f"({v[0]:+6.3f},{v[1]:+6.3f},{v[2]:+6.3f})"


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

    paa_data = vfs.read_entry_data(lookup("character/cd_damian_rd_sg_basic_01_01_nor_move_walk_f_ing_00.paa"))
    pab_data = vfs.read_entry_data(lookup("character/phw_01.pab"))
    skel = parse_pab(pab_data, "character/phw_01.pab")

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
        paa_data, "test_walk", vfs=vfs, max_hops=5,
        pab_bone_hashes=pab_hashes, pab_bone_count=len(skel.bones),
    )

    name_to_idx = {b.name: i for i, b in enumerate(skel.bones)}

    # For each bone, compute the world position of its tip vector under
    # the 3 different composition modes and compare.
    bones = ['Bip01 L Thigh', 'Bip01 R Thigh', 'Bip01 L Calf',
             'Bip01 L UpperArm', 'Bip01 L Forearm', 'Bip01 L Hand']

    # Walk the bone chain to compute parent world rotations
    # We need to compute world rotation for each bone in each mode

    print(f"{'bone':<20s} {'mode':<25s} {'bone vec rotated by local':<35s}  {'tip world (Y-up)':<35s}")
    print("=" * 120)

    # First pass: build parent world quat per bone for each mode
    # For deltas, we apply parent_world * bind * paa each step
    # For absolute, we apply parent_world * paa each step

    def compute_world_rot(bi, mode='delta_pre'):
        """Compute world rotation by walking up the chain (frame 1)."""
        bone = skel.bones[bi]
        if bone.parent_index < 0:
            parent_world = (0, 0, 0, 1)
        else:
            parent_world = compute_world_rot(bone.parent_index, mode)
        bind = bone.rotation
        if bi < len(anim.keyframes[1].bone_rotations):
            paa = anim.keyframes[1].bone_rotations[bi]
        else:
            paa = (0, 0, 0, 1)
        if mode == 'delta_pre':       # bind * paa
            local = quat_mul(bind, paa)
        elif mode == 'delta_post':    # paa * bind
            local = quat_mul(paa, bind)
        elif mode == 'absolute':      # paa
            local = paa
        elif mode == 'rest_only':     # just bind, no animation
            local = bind
        return quat_mul(parent_world, local)

    # Bone tip vector: assume bone vector points along +Y in local frame
    # (typical for FBX/Blender bones)
    BONE_VEC = (0, 1, 0)
    EXPECTED = {
        'Bip01 L Thigh':  'DOWN (-Y)',
        'Bip01 R Thigh':  'DOWN (-Y)',
        'Bip01 L Calf':   'DOWN (-Y)',
        'Bip01 L UpperArm': 'DOWN-OUT (small +X, -Y)',
        'Bip01 L Forearm': 'DOWN (-Y)',
        'Bip01 L Hand':   'DOWN (-Y)',
    }

    for name in bones:
        bi = name_to_idx.get(name)
        if bi is None:
            continue
        for mode in ['rest_only', 'delta_pre', 'delta_post', 'absolute']:
            world_q = compute_world_rot(bi, mode)
            tip = quat_rotate_vec3(world_q, BONE_VEC)
            print(f"{name:<20s} {mode:<25s} {fmt_v(tip):<35s}  expected: {EXPECTED.get(name, '?')}")
        print()


if __name__ == "__main__":
    main()
