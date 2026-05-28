"""Probe a PAB file's header to figure out where the real bone_count lives.

Strategy:
  1. Dump the first 64 header bytes in hex.
  2. Try parsing with header byte[0x14] count.
  3. Try parsing PAST that count using only structural validation
     (valid name_len, plausible parent_index, finite floats).
  4. Print the parsed-cleanly count and look for it as a u8/u16/u32 LE
     anywhere in the first 128 bytes.

If the parsed-cleanly count appears in the header at some offset, that's
the real bone_count field.
"""
from __future__ import annotations

import math
import struct
import sys
from pathlib import Path


def parse_permissively(data: bytes, hard_max: int = 4096) -> tuple[int, list[str]]:
    """Parse bones until structural failure. Returns (count, last_few_names)."""
    off = 0x17
    names: list[str] = []
    bone_count = 0

    for i in range(hard_max):
        if off + 4 > len(data):
            break
        name_len = data[off + 3]
        off += 4

        if name_len == 0 or name_len > 80 or off + name_len > len(data):
            break

        try:
            name = data[off:off + name_len].decode('ascii')
        except UnicodeDecodeError:
            break
        # Sanity-check name: must be printable
        if not all(0x20 <= c <= 0x7E for c in data[off:off + name_len]):
            break
        off += name_len

        if off + 4 + 256 + 40 + 1 > len(data):
            break

        parent_index = struct.unpack_from('<i', data, off)[0]
        off += 4

        # Validate parent_index: must be -1 or a small positive number
        # (less than what we've seen so far + some headroom)
        if not (parent_index == -1 or 0 <= parent_index < 4096):
            break

        bind = struct.unpack_from('<16f', data, off); off += 64
        inv  = struct.unpack_from('<16f', data, off); off += 64
        off += 128  # cache duplicates

        scale = struct.unpack_from('<3f', data, off); off += 12
        rot   = struct.unpack_from('<4f', data, off); off += 16
        pos   = struct.unpack_from('<3f', data, off); off += 12
        off += 1

        def bad(v):
            return math.isnan(v) or math.isinf(v) or abs(v) > 1e5

        if any(bad(v) for v in pos) or any(bad(v) for v in rot) or \
                any(bad(v) for v in scale) or any(bad(v) for v in bind):
            break

        bone_count = i + 1
        names.append(name)

    return bone_count, names


def find_value_in_header(data: bytes, value: int, header_size: int = 128) -> list[tuple[int, str]]:
    """Find offsets where `value` appears as u8/u16 LE/u32 LE in the header."""
    hits: list[tuple[int, str]] = []
    head = data[:header_size]
    # u8
    for off in range(len(head)):
        if head[off] == (value & 0xFF) and value <= 0xFF:
            hits.append((off, 'u8'))
    # u16 LE
    for off in range(len(head) - 1):
        v = struct.unpack_from('<H', head, off)[0]
        if v == value and value <= 0xFFFF:
            hits.append((off, 'u16le'))
    # u32 LE
    for off in range(len(head) - 3):
        v = struct.unpack_from('<I', head, off)[0]
        if v == value:
            hits.append((off, 'u32le'))
    return hits


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: probe_pab_header.py <file.pab>")
        return 1

    path = Path(sys.argv[1])
    data = path.read_bytes()
    print(f"File: {path}")
    print(f"Size: {len(data):,} bytes")
    print(f"Magic: {data[:4]!r}  Version: {data[4]:#x} {data[5]:#x}")
    print()

    print("First 64 header bytes:")
    for row in range(4):
        off = row * 16
        hex_str = ' '.join(f'{b:02x}' for b in data[off:off + 16])
        ascii_str = ''.join(chr(b) if 0x20 <= b <= 0x7E else '.'
                            for b in data[off:off + 16])
        print(f"  [0x{off:02x}]  {hex_str}  |{ascii_str}|")
    print()

    header_count = data[0x14]
    print(f"Byte[0x14] (current bone_count source): {header_count} (0x{header_count:02x})")

    # Also report common header field interpretations:
    print(f"  As u16 LE @ 0x14: {struct.unpack_from('<H', data, 0x14)[0]}")
    print(f"  u32 LE @ 0x10: {struct.unpack_from('<I', data, 0x10)[0]}")
    print(f"  u32 LE @ 0x14: {struct.unpack_from('<I', data, 0x14)[0]}")
    print(f"  u32 LE @ 0x18: {struct.unpack_from('<I', data, 0x18)[0]}")
    print()

    print("Permissive parse (continues past header count, stops at structural break):")
    count, names = parse_permissively(data)
    print(f"  parsed cleanly: {count} bones")
    if names:
        print(f"  first 3:  {names[:3]}")
        print(f"  last  3:  {names[-3:]}")
    print()

    if count != header_count:
        print(f"*** Header count ({header_count}) != actual count ({count}) ***")
        hits = find_value_in_header(data, count)
        print(f"\nLocations where {count} appears in header[:128]:")
        for off, kind in hits:
            print(f"  [0x{off:02x}] as {kind}")
        if not hits:
            print(f"  (not found as u8/u16/u32 in first 128 bytes)")

    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
