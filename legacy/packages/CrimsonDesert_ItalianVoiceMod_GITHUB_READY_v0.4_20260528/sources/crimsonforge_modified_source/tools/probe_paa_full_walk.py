"""Walk EVERY byte of a PAA file, identifying:
  - rotation tracks (10 bytes/kf, 4 fp16 unit quat + u16 frame)
  - position tracks (8 bytes/kf, 3 fp16 vec3 + u16 frame)
  - per-bone headers (any non-track data between tracks)

The goal: find the per-bone header structure so we can iterate every
bone and decode all its channels. Currently we decode bone 0 only.

Usage:
    python tools/probe_paa_full_walk.py --game "<install>" --path "<paa path>"
"""
from __future__ import annotations

import argparse
import struct
import sys
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
    """Returns (True, frame, quat) if 10 bytes at off look like a rot kf."""
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


def is_position_kf(data, off):
    """Returns (True, frame, vec3) if 8 bytes at off look like a pos kf.
    Vec3 should be small (< 5m typically for joint local positions)."""
    if off + 8 > len(data):
        return None
    px = fp16(data[off], data[off+1])
    py = fp16(data[off+2], data[off+3])
    pz = fp16(data[off+4], data[off+5])
    if abs(px) > 5 or abs(py) > 5 or abs(pz) > 5:
        return None
    if any(p != p for p in (px, py, pz)):  # NaN
        return None
    f = struct.unpack_from('<H', data, off + 6)[0]
    if f > 4096:
        return None
    return (f, (px, py, pz))


def find_rotation_track(data, start, end):
    """Walk ahead from start, return (track_start, list_of_keyframes,
    track_end_off) for the next valid rotation track."""
    p = start
    while p + 20 <= end:
        rec1 = is_unit_quat_kf(data, p)
        if rec1 is None:
            p += 1
            continue
        f1 = rec1[0]
        if f1 > 4:  # most tracks start at frame 0 or 1
            p += 1
            continue
        rec2 = is_unit_quat_kf(data, p + 10)
        if rec2 is None:
            p += 1
            continue
        f2 = rec2[0]
        if not (f1 < f2 <= f1 + 8):
            p += 1
            continue
        # Found one. Walk forward until invalid or frame drops
        kfs = [(f1, rec1[1]), (f2, rec2[1])]
        last = f2
        q = p + 20
        while q + 10 <= end:
            r = is_unit_quat_kf(data, q)
            if r is None or r[0] < last:
                break
            kfs.append(r)
            last = r[0]
            q += 10
        return (p, kfs, q)
    return (-1, [], end)


def find_position_track(data, start, end):
    """Walk ahead from start for a position track (8 bytes/kf)."""
    p = start
    while p + 16 <= end:
        rec1 = is_position_kf(data, p)
        if rec1 is None:
            p += 1
            continue
        f1 = rec1[0]
        if f1 > 4:
            p += 1
            continue
        rec2 = is_position_kf(data, p + 8)
        if rec2 is None:
            p += 1
            continue
        f2 = rec2[0]
        if not (f1 < f2 <= f1 + 8):
            p += 1
            continue
        kfs = [(f1, rec1[1]), (f2, rec2[1])]
        last = f2
        q = p + 16
        while q + 8 <= end:
            r = is_position_kf(data, q)
            if r is None or r[0] < last:
                break
            kfs.append(r)
            last = r[0]
            q += 8
        return (p, kfs, q)
    return (-1, [], end)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--game", required=True)
    parser.add_argument("--path", required=True)
    args = parser.parse_args()

    from core.vfs_manager import VfsManager

    game = Path(args.game)
    packages = game / "packages" if (game / "packages").is_dir() else game
    vfs = VfsManager(str(packages))
    for g in vfs.list_package_groups():
        try: vfs.load_pamt(g)
        except Exception: pass

    target = args.path.replace("\\", "/").lower()
    entry = None
    for _g, p in vfs._pamt_cache.items():
        for e in p.file_entries:
            if (e.path or "").replace("\\", "/").lower() == target:
                entry = e; break
        if entry: break
    if not entry:
        print(f"NOT FOUND: {args.path}")
        return 1

    data = vfs.read_entry_data(entry)
    print(f"File: {args.path}    Size: {len(data):,} bytes")

    # Skip past header + tag + bind + link path. Find first valid track.
    # Start scanning at 0xA0 (just after typical header structures).
    print(f"\nWalking entire file for tracks (rotation = 10b/kf, position = 8b/kf)")
    print(f"=" * 80)

    p = 0xA0
    n_rot = 0
    n_pos = 0
    last_pos = p
    section_log = []

    while p < len(data) - 10:
        # Try rotation first
        rt_start, rt_kfs, rt_end = find_rotation_track(data, p, len(data))
        pt_start, pt_kfs, pt_end = find_position_track(data, p, len(data))

        # Pick the closest one
        if rt_start < 0 and pt_start < 0:
            break
        if rt_start < 0 or (pt_start >= 0 and pt_start < rt_start):
            # Position track is next
            gap = pt_start - p
            section_log.append((p, gap, 'POS', pt_start, len(pt_kfs)))
            n_pos += 1
            p = pt_end
        else:
            # Rotation track is next
            gap = rt_start - p
            section_log.append((p, gap, 'ROT', rt_start, len(rt_kfs)))
            n_rot += 1
            p = rt_end

    print(f"\nTotal tracks found: {n_rot} rotation, {n_pos} position")
    print(f"\n{'pos_walk':>8s} {'gap':>4s} {'type':>4s} {'track_off':>10s} {'kfs':>4s}  bytes_used")
    total = 0
    for entry in section_log:
        p_walk, gap, ttype, tstart, n_kfs = entry
        kf_size = 10 if ttype == 'ROT' else 8
        bytes_used = n_kfs * kf_size
        total += gap + bytes_used
        print(f"  0x{p_walk:04x} {gap:>4d}  {ttype:>4s}  0x{tstart:08x}  {n_kfs:>4d}  {bytes_used} bytes")
    print(f"\nTotal bytes used (gaps + tracks): {total}")
    print(f"File size: {len(data)}")
    print(f"Difference (unaccounted): {len(data) - 0xA0 - total}")

    # Show first 10 gaps to understand inter-track header structure
    print(f"\n--- First 10 inter-track gaps (raw bytes) ---")
    shown = 0
    for entry in section_log:
        p_walk, gap, ttype, tstart, n_kfs = entry
        if gap == 0:
            continue
        print(f"\n  Gap before {ttype} track at 0x{tstart:04x} ({gap} bytes from 0x{p_walk:04x}):")
        gap_bytes = data[p_walk:tstart]
        hex_str = ' '.join(f'{b:02x}' for b in gap_bytes[:48])
        print(f"    {hex_str}")
        # Try to parse as little-endian u16/u32
        if gap >= 4:
            u16_a = struct.unpack_from('<H', gap_bytes, 0)[0]
            u32_a = struct.unpack_from('<I', gap_bytes, 0)[0]
            print(f"    interpret: u16[0]={u16_a}  u32[0]={u32_a}  u32[0]hex=0x{u32_a:08x}")
        shown += 1
        if shown >= 10:
            break


if __name__ == "__main__":
    sys.exit(main() or 0)
