"""Compare _decode_link_embedded_tracks output vs the standalone probe
walker. They should find the same tracks. If parser finds more, it's
splitting tracks incorrectly.

Usage:
    python tools/probe_parser_vs_probe.py
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.vfs_manager import VfsManager
from core.skeleton_parser import parse_pab
from core.animation_parser import _decode_link_embedded_tracks


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


def walk_tracks_probe(data, start=0xA0):
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
            'kfs': len(kfs),
            'last_frame': kfs[-1][0],
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

    # Find link path scan_start for the parser tracks_start
    scan_start = paa_data.find(b"%character/", 0x14)
    end = scan_start
    while end < min(len(paa_data), scan_start + 1024):
        b = paa_data[end]
        if b < 0x20 or b > 0x7E:
            break
        end += 1
    link_target = paa_data[scan_start:end].decode("ascii", errors="replace")
    for ext in (".pab", ".paa", ".pac", ".pam", ".pamlod", ".pabc", ".pabgb"):
        idx = link_target.lower().find(ext)
        if idx >= 0:
            link_target = link_target[:idx + len(ext)]
            break
    tracks_start = scan_start + len(link_target)
    tracks_start = (tracks_start + 3) & ~3
    print(f"Link target: {link_target}")
    print(f"tracks_start: 0x{tracks_start:04x}")

    # Probe walker
    probe_tracks = walk_tracks_probe(paa_data, start=tracks_start)
    print(f"\nProbe walker tracks: {len(probe_tracks)}")
    for ti, t in enumerate(probe_tracks):
        print(f"  probe[{ti:>2d}] @ 0x{t['off']:04x}  kfs={t['kfs']:>3d}  last_frame={t['last_frame']:>3d}  gap_to_prev={t['gap']:>4d}")

    # Parser walker
    parser_tracks, parser_hashes = _decode_link_embedded_tracks(
        paa_data, tracks_start,
        filename="cd_damian_walk",
        pab_bone_hashes=pab_hashes,
    )
    print(f"\nParser walker tracks: {len(parser_tracks)}")
    print(f"  Parser hashes matched: {sum(1 for h in parser_hashes if h is not None)}")
    for ti, t in enumerate(parser_tracks):
        bone_name = ""
        h = parser_hashes[ti]
        if h is not None:
            try:
                bi = pab_hashes.index(h)
                bone_name = skel.bones[bi].name
            except ValueError:
                pass
        print(f"  parser[{ti:>2d}]  kfs={len(t):>3d}  first_frame={t[0][0]:>3d}  last_frame={t[-1][0]:>3d}  hash={'0x%06x' % h if h else 'None':>10s}  bone={bone_name}")


if __name__ == "__main__":
    main()
