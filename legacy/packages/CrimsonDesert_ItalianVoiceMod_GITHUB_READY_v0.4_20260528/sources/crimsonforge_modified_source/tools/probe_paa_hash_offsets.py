"""Per-track canonical hash offset analysis.

Walks every rotation track in a PAA, then for each track scans the
preceding gap for ANY u32 LE whose low-24 bits match a known PAB
bone hash. Reports the offset(s) where matches were found.

Goal: determine the per-gap-size canonical offset(s) so we can
replace the byte-scan in animation_parser.py with a small lookup
table. Byte-scanning produces false positives that scramble bones.

Usage:
    python tools/probe_paa_hash_offsets.py
"""
from __future__ import annotations

import struct
import sys
from collections import Counter
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
        tracks.append({
            'off': p,
            'gap': p - last_end,
            'gap_bytes': data[last_end:p],
            'kfs': len(kfs),
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

    paa_e = lookup("character/cd_damian_rd_sg_basic_01_01_nor_move_walk_f_ing_00.paa")
    pab_e = lookup("character/phw_01.pab")

    paa_data = vfs.read_entry_data(paa_e)
    pab_data = vfs.read_entry_data(pab_e)

    skel = parse_pab(pab_data, "character/phw_01.pab")
    pab_hashes = get_pab_hashes(pab_data, len(skel.bones))
    pab_hash_set = set(pab_hashes)
    print(f"PAB bones: {len(skel.bones)}, hashes: {len(pab_hashes)}")

    tracks = walk_tracks(paa_data)
    print(f"\nTotal rotation tracks in PAA: {len(tracks)}")

    print(f"\nGap-size distribution:")
    gap_dist = Counter(t['gap'] for t in tracks)
    for size, cnt in sorted(gap_dist.items()):
        print(f"  gap_size={size:4d}: {cnt} tracks")

    print(f"\nPer-track hash match positions (gap.size - 9 = canonical?):")
    print(f"{'#':>3s} {'pos':>6s} {'gap':>4s} {'kfs':>4s}  expected@(gap-9)  hit_offsets")
    n_match_at_canon = 0
    n_match_at_any = 0
    for ti, t in enumerate(tracks):
        gb = t['gap_bytes']
        canonical_off = t['gap'] - 9
        canon_hit = False
        all_hits = []
        for off in range(0, len(gb) - 3):
            cand = struct.unpack_from('<I', gb, off)[0] & 0x00FFFFFF
            if cand in pab_hash_set:
                bone_idx = pab_hashes.index(cand)
                bone_name = skel.bones[bone_idx].name
                all_hits.append((off, cand, bone_name))
                if off == canonical_off:
                    canon_hit = True
        if canon_hit:
            n_match_at_canon += 1
        if all_hits:
            n_match_at_any += 1
        marker = "*CANON*" if canon_hit else "       "
        hits_str = ", ".join(
            f"@{o}=0x{h:06x}({n})" for o, h, n in all_hits[:3]
        )
        print(f"{ti:>3d} 0x{t['off']:04x} {t['gap']:>4d} {t['kfs']:>4d}  expect_off={canonical_off:<4d} {marker}  {hits_str}")

    print(f"\n=== SUMMARY ===")
    print(f"Tracks matching at canonical offset (gap-9): {n_match_at_canon}/{len(tracks)}")
    print(f"Tracks with ANY hash match: {n_match_at_any}/{len(tracks)}")


if __name__ == "__main__":
    main()
