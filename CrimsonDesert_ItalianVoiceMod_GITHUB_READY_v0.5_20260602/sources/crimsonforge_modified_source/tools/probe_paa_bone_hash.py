"""Reverse-engineer the EXACT bone-hash function used in PAA inter-track
gaps. Tries every common hash algorithm against known bone names from
the matching PAB skeleton. Reports which hash function (if any) matches
all observed hash bytes in the gaps.

Strategy:
  1. Parse the PAA file. Find each rotation track's start offset and
     the per-bone gap header before it. Extract candidate hash bytes.
  2. Parse the PAB skeleton. Get all bone names.
  3. For each bone name, compute hash via:
       FNV-1a (32 bit), FNV-1, CRC32, djb2, sdbm, Pearl Abyss custom
       variants. Try Korean / cp949 encoded versions too.
  4. For each hash function, count how many of the observed gap-hashes
     it can match to a bone name. The function with the most matches
     is the actual one.
  5. If 100% matches found, print the exact track-to-bone mapping.

Usage:
    python tools/probe_paa_bone_hash.py --game "<install>" --paa "<paa path>" --pab "<pab path>"
"""
from __future__ import annotations

import argparse
import struct
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


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


def is_unit_quat_kf(data, off):
    if off + 10 > len(data):
        return None
    qx = fp16(data[off], data[off+1])
    qy = fp16(data[off+2], data[off+3])
    qz = fp16(data[off+4], data[off+5])
    qw = fp16(data[off+6], data[off+7])
    m2 = qx*qx + qy*qy + qz*qz + qw*qw
    if not (0.95 < m2 < 1.05):
        return None
    f = struct.unpack_from('<H', data, off + 8)[0]
    if f > 4096:
        return None
    return (f, (qx, qy, qz, qw))


def find_rotation_tracks(data, start_offset=0xA0):
    """Find all rotation tracks AND extract the candidate hash bytes
    from the gap before each."""
    tracks = []  # list of (track_off, n_keyframes, end_off, gap_bytes)
    p = start_offset
    last_track_end = start_offset

    while p < len(data) - 20:
        # Look for the next valid keyframe start
        rec1 = is_unit_quat_kf(data, p)
        if rec1 is None or rec1[0] > 4:
            p += 1
            continue
        rec2 = is_unit_quat_kf(data, p + 10)
        if rec2 is None:
            p += 1
            continue
        if not (rec1[0] < rec2[0] <= rec1[0] + 8):
            p += 1
            continue
        # Found a track start. Walk forward
        kfs = [rec1, rec2]
        last = rec2[0]
        q = p + 20
        while q + 10 <= len(data):
            r = is_unit_quat_kf(data, q)
            if r is None or r[0] < last:
                break
            kfs.append(r)
            last = r[0]
            q += 10
        gap_bytes = data[last_track_end:p]
        tracks.append({
            'track_off': p,
            'gap_bytes': gap_bytes,
            'gap_size': p - last_track_end,
            'n_kfs': len(kfs),
            'end_off': q,
        })
        last_track_end = q
        p = q
    return tracks


# ─── Hash function library ──────────────────────────────────────────

def fnv1a_32(data: bytes) -> int:
    h = 0x811c9dc5
    for b in data:
        h ^= b
        h = (h * 0x01000193) & 0xFFFFFFFF
    return h


def fnv1_32(data: bytes) -> int:
    h = 0x811c9dc5
    for b in data:
        h = (h * 0x01000193) & 0xFFFFFFFF
        h ^= b
    return h


def djb2(data: bytes) -> int:
    h = 5381
    for b in data:
        h = ((h << 5) + h + b) & 0xFFFFFFFF
    return h


def sdbm(data: bytes) -> int:
    h = 0
    for b in data:
        h = (b + (h << 6) + (h << 16) - h) & 0xFFFFFFFF
    return h


def crc32_zlib(data: bytes) -> int:
    return zlib.crc32(data) & 0xFFFFFFFF


def murmur3_32(data: bytes, seed: int = 0) -> int:
    """MurmurHash3 32-bit (x86)."""
    c1 = 0xcc9e2d51
    c2 = 0x1b873593
    r1 = 15
    r2 = 13
    m = 5
    n = 0xe6546b64
    h = seed
    nblocks = len(data) // 4
    for i in range(nblocks):
        k = struct.unpack_from('<I', data, i * 4)[0]
        k = (k * c1) & 0xFFFFFFFF
        k = ((k << r1) | (k >> (32 - r1))) & 0xFFFFFFFF
        k = (k * c2) & 0xFFFFFFFF
        h ^= k
        h = ((h << r2) | (h >> (32 - r2))) & 0xFFFFFFFF
        h = (h * m + n) & 0xFFFFFFFF
    # Tail
    tail_index = nblocks * 4
    tail = data[tail_index:]
    k1 = 0
    if len(tail) >= 3:
        k1 ^= tail[2] << 16
    if len(tail) >= 2:
        k1 ^= tail[1] << 8
    if len(tail) >= 1:
        k1 ^= tail[0]
        k1 = (k1 * c1) & 0xFFFFFFFF
        k1 = ((k1 << r1) | (k1 >> (32 - r1))) & 0xFFFFFFFF
        k1 = (k1 * c2) & 0xFFFFFFFF
        h ^= k1
    h ^= len(data)
    h ^= h >> 16
    h = (h * 0x85ebca6b) & 0xFFFFFFFF
    h ^= h >> 13
    h = (h * 0xc2b2ae35) & 0xFFFFFFFF
    h ^= h >> 16
    return h


def adler32_(data: bytes) -> int:
    return zlib.adler32(data) & 0xFFFFFFFF


def hash_lower(fn):
    """Wrap a hash to apply on lowercased bytes."""
    return lambda b: fn(b.lower())


HASH_FNS = {
    'fnv1a_32': fnv1a_32,
    'fnv1_32': fnv1_32,
    'djb2': djb2,
    'sdbm': sdbm,
    'crc32': crc32_zlib,
    'murmur3_32': murmur3_32,
    'adler32': adler32_,
    'fnv1a_32_lower': hash_lower(fnv1a_32),
    'crc32_lower': hash_lower(crc32_zlib),
    'murmur3_32_lower': hash_lower(murmur3_32),
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--game", required=True)
    parser.add_argument("--paa", required=True)
    parser.add_argument("--pab", required=True,
                        help="Path to matching PAB, e.g. character/phw_01.pab")
    args = parser.parse_args()

    from core.vfs_manager import VfsManager

    game_root = Path(args.game)
    pkg = game_root / "packages" if (game_root / "packages").is_dir() else game_root
    vfs = VfsManager(str(pkg))
    for g in vfs.list_package_groups():
        try: vfs.load_pamt(g)
        except Exception: pass

    def lookup(p):
        target = p.replace("\\", "/").lower()
        for _g, pamt in vfs._pamt_cache.items():
            for e in pamt.file_entries:
                if (e.path or "").replace("\\", "/").lower() == target:
                    return e
        return None

    paa_e = lookup(args.paa)
    pab_e = lookup(args.pab)
    if not paa_e or not pab_e:
        print(f"Missing files: paa={paa_e is not None} pab={pab_e is not None}")
        return 1

    paa_data = vfs.read_entry_data(paa_e)
    pab_data = vfs.read_entry_data(pab_e)

    print(f"PAA: {args.paa} ({len(paa_data):,} bytes)")
    print(f"PAB: {args.pab} ({len(pab_data):,} bytes)")

    # Parse skeleton bones
    from core.skeleton_parser import parse_pab
    skel = parse_pab(pab_data, args.pab)
    bone_names = [b.name for b in skel.bones]
    print(f"PAB has {len(bone_names)} bones")

    # Also pull the PAB's stored per-bone hash (24-bit, in low 3 bytes
    # of the 4-byte record header). The PAA might reference bones via
    # this same hash directly.
    pab_bone_hashes: list[int] = []  # parallel to bone_names
    off = 0x17
    for i in range(len(bone_names)):
        if off + 4 > len(pab_data):
            break
        hash_lo24 = struct.unpack_from('<I', pab_data, off)[0] & 0x00FFFFFF
        name_len = pab_data[off + 3]
        pab_bone_hashes.append(hash_lo24)
        off += 4 + name_len + 4 + 256 + 40 + 1
    print(f"Pulled {len(pab_bone_hashes)} per-bone hashes from PAB header")
    print(f"  First 5 PAB hashes: {[hex(h) for h in pab_bone_hashes[:5]]}")
    print(f"  First 5 bone names: {bone_names[:5]}")

    # Find PAA tracks + gaps
    tracks = find_rotation_tracks(paa_data)
    print(f"\nPAA has {len(tracks)} rotation tracks")
    print(f"\nTrack gaps (first 10):")
    for i, t in enumerate(tracks[:10]):
        print(f"  track[{i}] @ 0x{t['track_off']:04x}: "
              f"{t['n_kfs']} kfs, gap_size={t['gap_size']}")
        print(f"    gap bytes: {t['gap_bytes'].hex()}")

    # ── EXTRACT CANDIDATE HASHES from gaps ──
    # Try every 4-byte aligned position in the gap, treating each as
    # a u32 LE value. Pick the most likely position by trying each
    # offset and seeing which yields the most matches.
    print(f"\n{'='*72}")
    print("Hash function discovery — trying every algorithm × every offset")
    print('='*72)

    # Build hash-to-bone-index map for each hash function
    bone_hashes = {}  # name → {fn_name: hash_value}
    for name in bone_names:
        nb = name.encode('utf-8')
        bone_hashes[name] = {fn_name: fn(nb) for fn_name, fn in HASH_FNS.items()}

    # Reverse lookup: hash_value → bone_name (per hash function)
    hash_to_bone = {fn_name: {} for fn_name in HASH_FNS}
    for name, hashes in bone_hashes.items():
        for fn_name, h in hashes.items():
            hash_to_bone[fn_name].setdefault(h, []).append(name)

    # Also include the PAB stored hash as a "function" — for each bone
    # name, the hash is just whatever PAB has for that bone (no
    # algorithm, just lookup). Test against full 32-bit AND 24-bit
    # masked.
    pab_hash_24 = {h: bone_names[i] for i, h in enumerate(pab_bone_hashes)}
    pab_hash_full_lo3 = {}  # bone_name → hash, but with various upper-byte values

    # For each gap, scan EVERY byte offset (not just aligned) for a
    # u32 or u24 that matches some bone identifier.
    best = None

    def _check_with_lookup(lookup: dict, label: str, mask: int = 0xFFFFFFFF):
        nonlocal best
        best_offset = -1
        best_matches = 0
        best_matched = []
        for off in range(0, 32):
            n_match = 0
            matched = []
            for t in tracks:
                gb = t['gap_bytes']
                if off + 4 > len(gb):
                    continue
                cand = struct.unpack_from('<I', gb, off)[0] & mask
                bones = lookup.get(cand)
                if bones:
                    name = bones if isinstance(bones, str) else bones[0]
                    n_match += 1
                    matched.append((t['track_off'], cand, name))
            if n_match > best_matches:
                best_matches = n_match
                best_offset = off
                best_matched = matched
        print(f"  {label:>26s}  best offset={best_offset:>3d}  matches={best_matches}/{len(tracks)}")
        if best_matches > 0 and (best is None or best_matches > best[1]):
            best = (label, best_matches, best_offset, best_matched)

    for fn_name in HASH_FNS:
        _check_with_lookup(hash_to_bone[fn_name], fn_name)
    _check_with_lookup(pab_hash_24, "pab_stored_hash_24bit", mask=0x00FFFFFF)
    _check_with_lookup(pab_hash_24, "pab_stored_hash_32bit_lo24")

    if best and best[1] >= len(tracks) // 2:
        print(f"\n=== WINNER ===")
        print(f"  Hash function: {best[0]}")
        print(f"  Matches: {best[1]}/{len(tracks)}")
        print(f"  Gap offset: {best[2]} bytes")
        print(f"\nFirst 10 matched tracks:")
        for track_off, hash_val, bone_name in best[3][:10]:
            print(f"  0x{track_off:04x}  hash=0x{hash_val:08x}  → bone {bone_name!r}")
    else:
        print(f"\nNO CLEAR WINNER — hash function not in our library.")
        print(f"Best result: {best}")
        # Dump candidate hashes from first 5 gaps for further analysis
        print(f"\nCandidate hashes (every 4-byte offset of first 5 gaps):")
        for i, t in enumerate(tracks[:5]):
            gb = t['gap_bytes']
            print(f"  track[{i}] @ 0x{t['track_off']:04x}, gap {len(gb)} bytes:")
            for off in range(0, len(gb), 4):
                if off + 4 <= len(gb):
                    val = struct.unpack_from('<I', gb, off)[0]
                    print(f"    +{off:>2d}: 0x{val:08x}  ({val})")

    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
