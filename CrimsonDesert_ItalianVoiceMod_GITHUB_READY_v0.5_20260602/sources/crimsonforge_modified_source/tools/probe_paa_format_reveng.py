"""Reverse-engineer the PAA "link-with-embedded-tracks" layout.

Walks one PAA byte-by-byte, identifies each section (header / tag /
bind SRT block / link path / track block), and tries to decode the
per-keyframe record format. Tries multiple interpretations of the
10-byte keyframe records so we can pick the one that produces sane
quaternion values.

Usage:
    python tools/probe_paa_format_reveng.py --game "<install>" --path "<paa path>"
"""
from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def fp16_to_float(b0: int, b1: int) -> float:
    """Decode fp16 little-endian from two bytes."""
    val = (b1 << 8) | b0
    sign = (val >> 15) & 1
    exp  = (val >> 10) & 0x1F
    frac = val & 0x3FF
    if exp == 0:
        if frac == 0:
            return -0.0 if sign else 0.0
        return ((-1) ** sign) * (frac / 1024.0) * (2 ** -14)
    if exp == 31:
        if frac == 0:
            return float('-inf') if sign else float('inf')
        return float('nan')
    return ((-1) ** sign) * (1 + frac / 1024.0) * (2 ** (exp - 15))


def s16(b0: int, b1: int) -> int:
    """Decode signed int16 LE."""
    val = (b1 << 8) | b0
    if val >= 0x8000:
        val -= 0x10000
    return val


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--game", required=True)
    parser.add_argument("--path", required=True)
    args = parser.parse_args()

    from core.vfs_manager import VfsManager

    game_root = Path(args.game)
    if (game_root / "packages").is_dir():
        packages_dir = game_root / "packages"
    else:
        packages_dir = game_root

    vfs = VfsManager(str(packages_dir))
    for group in vfs.list_package_groups():
        try: vfs.load_pamt(group)
        except Exception: pass

    target = args.path.replace("\\", "/").lower()
    found = None
    for _g, pamt in getattr(vfs, "_pamt_cache", {}).items():
        for entry in getattr(pamt, "file_entries", []):
            if (entry.path or "").replace("\\", "/").lower() == target:
                found = entry
                break
        if found: break
    if not found:
        print(f"Path not found: {args.path}")
        return 1

    data = vfs.read_entry_data(found)
    print(f"File: {args.path}    Size: {len(data):,} bytes")
    print(f"Header: {data[:0x14].hex()}")

    # Walk known fields
    print(f"\n--- Section walk ---")
    print(f"  0x00..0x03: magic = {data[0:4]!r}")
    print(f"  0x04..0x05: version = {data[4]}.{data[5]}")
    print(f"  0x06..0x0F: padding = {data[6:0x10].hex()}")
    print(f"  0x10..0x13: flags = 0x{struct.unpack_from('<I', data, 0x10)[0]:08x}")
    tag_len = struct.unpack_from('<H', data, 0x14)[0]
    print(f"  0x14..0x15: tag_len = {tag_len}")
    tag_bytes = data[0x16:0x16 + tag_len]
    try:
        tag_str = tag_bytes.decode('utf-8', errors='replace')
    except UnicodeDecodeError:
        tag_str = tag_bytes.decode('latin-1', errors='replace')
    print(f"  0x16..0x{0x16 + tag_len - 1:02x}: tag = {tag_str!r}")

    # After tags, bind SRT block. Each bone = 10 floats = 40 bytes.
    bind_start = 0x16 + tag_len
    print(f"\n--- Bind SRT block (starts at 0x{bind_start:02x}) ---")
    print(f"  Each bone = 10 floats (3 scale + 4 quat + 3 trans) = 40 bytes")

    # Find the link path. Scan for '%character/'
    pct = data.find(b'%character/', bind_start, min(len(data), 4096))
    print(f"\n--- Link path (starts at 0x{pct:02x}) ---")
    if pct < 0:
        print("  NOT FOUND")
        return 0

    bind_block_size = pct - bind_start
    bone_count_in_bind = bind_block_size // 40
    print(f"  Bind block: {bind_block_size} bytes = {bone_count_in_bind} bones × 40 bytes")
    print(f"             remainder: {bind_block_size % 40} bytes")

    # Decode first 3 bones of bind block
    print(f"\n  First 3 bind SRT records:")
    for b in range(min(3, bone_count_in_bind)):
        off = bind_start + b * 40
        floats = struct.unpack_from('<10f', data, off)
        print(f"    bone[{b}]@0x{off:04x}:")
        print(f"      scale = ({floats[0]:.4f}, {floats[1]:.4f}, {floats[2]:.4f})")
        print(f"      quat  = ({floats[3]:.4f}, {floats[4]:.4f}, {floats[5]:.4f}, {floats[6]:.4f})")
        print(f"      trans = ({floats[7]:.4f}, {floats[8]:.4f}, {floats[9]:.4f})")

    # Find link path end
    end = pct
    while end < len(data) and 0x20 <= data[end] <= 0x7E:
        end += 1
    link_path = data[pct:end].decode('ascii', errors='replace')
    # truncate at ext
    for ext in ('.pab', '.paa', '.pac', '.pam'):
        i = link_path.lower().find(ext)
        if i >= 0:
            link_path = link_path[:i + len(ext)]
            break
    path_end = pct + len(link_path)
    print(f"  Link path: {link_path!r}")
    print(f"  Path ends at 0x{path_end:04x}")

    # ── TRACKS BLOCK ──
    # Look for the `3b NN 00` keyframe markers.
    print(f"\n--- Tracks block (starts after link, around 0x{path_end:04x}) ---")
    # Scan for first occurrence of `3b 00 00` or `3b 01 00` to anchor
    tracks_start = -1
    for off in range(path_end, min(len(data), path_end + 256)):
        if data[off] == 0x3b and off + 2 < len(data):
            n = struct.unpack_from('<H', data, off + 1)[0]
            # Plausible frame index (small)
            if n == 0 or n == 1:
                tracks_start = off - 7  # back up 7 bytes for the quat
                if tracks_start >= path_end:
                    print(f"  Found keyframe marker at 0x{off:04x} "
                          f"(frame {n}); first record starts at 0x{tracks_start:04x}")
                    break

    if tracks_start < 0:
        print(f"  Could not locate keyframe markers")
        return 0

    # Try interpretations of the 10-byte keyframe record
    print(f"\n  Decoding first 6 keyframe records (10 bytes each):")
    print(f"    bytes 0-5 = 3 int16 (xyz) ?")
    print(f"    byte 6    = ???")
    print(f"    byte 7    = 0x3b marker")
    print(f"    bytes 8-9 = u16 frame index")
    for i in range(6):
        off = tracks_start + i * 10
        if off + 10 > len(data):
            break
        rec = data[off:off + 10]
        x_i16 = s16(rec[0], rec[1])
        y_i16 = s16(rec[2], rec[3])
        z_i16 = s16(rec[4], rec[5])
        b6 = rec[6]
        marker = rec[7]
        frame = struct.unpack_from('<H', rec, 8)[0]
        # Interpretation A: int16 normalised by /32767
        x_a = x_i16 / 32767.0
        y_a = y_i16 / 32767.0
        z_a = z_i16 / 32767.0
        # Interpretation B: bytes 4-7 as fp32, bytes 0-1 + 2-3 as int16 (mixed)
        # Just for completeness
        # Interpretation C: bytes 0-7 as 4 fp16
        c0 = fp16_to_float(rec[0], rec[1])
        c1 = fp16_to_float(rec[2], rec[3])
        c2 = fp16_to_float(rec[4], rec[5])
        c3 = fp16_to_float(rec[6], rec[7])
        print(f"    rec[{i}]@0x{off:04x}: bytes={rec.hex()} "
              f"frame={frame} marker=0x{marker:02x} byte6=0x{b6:02x}")
        print(f"      INT16/32767: x={x_a:+.4f} y={y_a:+.4f} z={z_a:+.4f}  "
              f"(byte6={b6/255.0:.4f})")
        print(f"      4×FP16:      ({c0:+.4f}, {c1:+.4f}, {c2:+.4f}, {c3:+.4f})")

    # Verify keyframe stride by checking marker pattern repeats
    print(f"\n  Verifying 10-byte stride pattern...")
    consistent = 0
    for i in range(50):
        off = tracks_start + i * 10
        if off + 10 > len(data):
            break
        marker_pos = off + 7
        if marker_pos < len(data) and data[marker_pos] == 0x3b:
            consistent += 1
    print(f"    {consistent} of 50 records have 0x3b at byte 7 → "
          f"{'CONFIRMED 10-byte stride' if consistent > 40 else 'unclear'}")

    # Track end detection: when frame index resets (next track starts)
    # OR when the data ends. Find total track size.
    print(f"\n  Following tracks until frame-index drop or non-marker byte...")
    last_frame = -1
    frames_in_first_track = 0
    for i in range(2000):
        off = tracks_start + i * 10
        if off + 10 > len(data):
            break
        if data[off + 7] != 0x3b:
            print(f"    record {i} has marker 0x{data[off+7]:02x} — track ends "
                  f"at 0x{off:04x} (after {frames_in_first_track} keyframes)")
            break
        frame = struct.unpack_from('<H', data, off + 8)[0]
        if last_frame >= 0 and frame < last_frame:
            print(f"    record {i} frame {frame} < prev {last_frame} — "
                  f"new track? at 0x{off:04x} (after {frames_in_first_track})")
            break
        last_frame = frame
        frames_in_first_track += 1
    if frames_in_first_track > 0:
        print(f"    First track has {frames_in_first_track} keyframes "
              f"(frame range 0..{last_frame})")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
