"""For every track in Damian's walk PAA, dump the FULL gap bytes (the
header BEFORE each track's first keyframe) and try to interpret each
slot.

Goal: identify what the 11 bytes BEFORE the bone hash mean. The current
parser skips them. They might contain a per-bone REST QUATERNION (4
fp16), or a transform offset, or anything else that we need to compose
into the per-frame rotation to make upper-body bones not explode.

For each bone, also dump the bind from PAB so we can compare.

Usage:
    python tools/probe_paa_gap_structure.py
"""
from __future__ import annotations

import struct
import sys
import math
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.vfs_manager import VfsManager
from core.skeleton_parser import parse_pab


def fp16(b0, b1):
    val = (b1 << 8) | b0
    sign = (val >> 15) & 1
    exp = (val >> 10) & 0x1F
    frac = val & 0x3FF
    if exp == 0:
        return ((-1) ** sign) * (frac / 1024.0) * (2 ** -14) if frac else 0.0
    if exp == 31:
        return float('nan')
    return ((-1) ** sign) * (1 + frac / 1024.0) * (2 ** (exp - 15))


def is_kf(data, p):
    if p + 10 > len(data):
        return None
    qs = [fp16(data[p + i * 2], data[p + i * 2 + 1]) for i in range(4)]
    m2 = sum(q * q for q in qs)
    if not (0.95 < m2 < 1.05):
        return None
    f = struct.unpack_from('<H', data, p + 8)[0]
    if f > 4096:
        return None
    return (f, qs)


def walk_tracks(data, start=0xA0):
    tracks = []
    last_end = start
    p = start
    while p < len(data) - 20:
        r1 = is_kf(data, p)
        if not r1 or r1[0] > 4:
            p += 1
            continue
        r2 = is_kf(data, p + 10)
        if not r2 or not (r1[0] < r2[0] <= r1[0] + 8):
            p += 1
            continue
        kfs = [r1, r2]
        last = r2[0]
        q = p + 20
        while q + 10 <= len(data):
            r = is_kf(data, q)
            if not r or r[0] < last:
                break
            kfs.append(r)
            last = r[0]
            q += 10
        gap_bytes = data[last_end:p]
        tracks.append({
            'off': p,
            'gap': p - last_end,
            'gap_bytes': gap_bytes,
            'first_quat': kfs[0][1],
            'first_frame': kfs[0][0],
            'kf_count': len(kfs),
        })
        last_end = q
        p = q
    return tracks


def get_pab_hashes(pab_data, n_bones):
    hashes = []
    off = 0x17
    for _ in range(n_bones):
        if off + 4 > len(pab_data):
            break
        h = struct.unpack_from('<I', pab_data, off)[0] & 0x00FFFFFF
        name_len = pab_data[off + 3]
        hashes.append(h)
        off += 4 + name_len + 4 + 256 + 40 + 1
    return hashes


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

    paa = vfs.read_entry_data(lookup("character/cd_damian_rd_sg_basic_01_01_nor_move_walk_f_ing_00.paa"))
    pab = vfs.read_entry_data(lookup("character/phw_01.pab"))
    skel = parse_pab(pab, "character/phw_01.pab")
    pab_hashes = get_pab_hashes(pab, len(skel.bones))

    tracks = walk_tracks(paa)

    # For each gap, decode multiple interpretations of the 11 bytes BEFORE the hash:
    # - Bytes 0-7: 4 fp16 = candidate REST QUATERNION
    # - Bytes 0-5: 3 fp16 = candidate POSITION (X, Y, Z)
    # - Bytes 8-9: u16
    # - Bytes 0-1: u16
    # - Bytes 0-3: float32
    # - Bytes 0-7: 2 float32
    print("Per-track gap analysis. Each row = one PAA rotation track.")
    print("The hash at gap[gap_size-9] identifies the bone (canonical offset).")
    print("The 11 bytes BEFORE the hash are CURRENTLY DISCARDED — find their meaning.")
    print()

    for ti, t in enumerate(tracks):
        gb = t['gap_bytes']
        gap_size = len(gb)
        canon_off = gap_size - 9
        if canon_off < 0:
            continue
        hash_val = struct.unpack_from('<I', gb, canon_off)[0] & 0x00FFFFFF
        bone_name = "?"
        bone_bind = None
        if hash_val in pab_hashes:
            bi = pab_hashes.index(hash_val)
            bone_name = skel.bones[bi].name
            bone_bind = skel.bones[bi].rotation

        # The header is bytes [0..canon_off) = canon_off bytes
        header = bytes(gb[:canon_off])
        # Try to interpret bytes 0..7 as 4 fp16 (potential rest quaternion)
        if len(header) >= 8:
            qx = fp16(header[0], header[1])
            qy = fp16(header[2], header[3])
            qz = fp16(header[4], header[5])
            qw = fp16(header[6], header[7])
            mag = math.sqrt(qx*qx + qy*qy + qz*qz + qw*qw)
        else:
            qx = qy = qz = qw = mag = 0
        # Bytes 0..5 as 3 fp16 (potential position)
        if len(header) >= 6:
            px = fp16(header[0], header[1])
            py = fp16(header[2], header[3])
            pz = fp16(header[4], header[5])
        else:
            px = py = pz = 0
        # Bytes 0..3 as float32
        if len(header) >= 4:
            f0 = struct.unpack_from('<f', header, 0)[0]
        else:
            f0 = 0
        # Bytes 0..3 as i32
        if len(header) >= 4:
            i0 = struct.unpack_from('<I', header, 0)[0]
        else:
            i0 = 0

        # Print
        hex_str = ' '.join(f'{b:02x}' for b in header)
        bind_str = f"{bone_bind}" if bone_bind else "?"
        print(f"#{ti:2d} bone={bone_name!r:<25s} gap={gap_size:>4d} kfs={t['kf_count']:>3d}")
        print(f"    PAB bind quat (xyzw): {bind_str}")
        print(f"    Gap header ({len(header)}b before hash):  {hex_str}")
        print(f"      as 4 fp16 (q0..3): ({qx:+.4f}, {qy:+.4f}, {qz:+.4f}, {qw:+.4f})  mag={mag:.4f}")
        if 0.9 < mag < 1.1:
            print(f"      ^^ LOOKS LIKE A UNIT QUATERNION! ^^")
        print(f"      as 3 fp16 (xyz):   ({px:+.4f}, {py:+.4f}, {pz:+.4f})")
        print(f"      as float32 [0..3]: {f0:.6e}     as u32: 0x{i0:08x} ({i0})")
        # PAA's first keyframe quat
        fq = t['first_quat']
        print(f"    First PAA keyframe quat: ({fq[0]:+.4f}, {fq[1]:+.4f}, {fq[2]:+.4f}, {fq[3]:+.4f})")
        print()


if __name__ == "__main__":
    main()
