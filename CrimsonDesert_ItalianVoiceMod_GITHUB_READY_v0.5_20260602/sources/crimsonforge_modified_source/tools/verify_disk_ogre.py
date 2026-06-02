#!/usr/bin/env python3
"""Read characterinfo.pabgb from the on-disk PAZ via the same code
path the GAME would use, find the Ogre row, and verify the f32 bytes
at HP/AttackPower offsets are actually the user's edited values.

Also checks: the PAZ checksum stored in PAMT vs the actual checksum
of the on-disk PAZ. If they don't match, the engine may fall back to
a vanilla copy or simply refuse to load.
"""
from __future__ import annotations
import os, sys, struct
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path: sys.path.insert(0, ROOT)
from core.vfs_manager import VfsManager
from core.pabgb_parser import parse_pabgb
from core.checksum_engine import checksum_file, pa_checksum

GAME = r"C:\Program Files (x86)\Steam\steamapps\common\Crimson Desert"
vfs = VfsManager(GAME)

pamt = vfs.load_pamt("0008")
b_entry = h_entry = None
for e in pamt.file_entries:
    p = e.path.lower()
    if p == "gamedata/characterinfo.pabgb": b_entry = e
    elif p == "gamedata/characterinfo.pabgh": h_entry = e

print(f"PABGB entry: paz={b_entry.paz_file}  off=0x{b_entry.offset:08X}  size={b_entry.orig_size}")
print(f"PABGH entry: paz={h_entry.paz_file}  off=0x{h_entry.offset:08X}  size={h_entry.orig_size}")

# Compute current checksum of 0.paz as the game would.
print(f"\nChecksumming {b_entry.paz_file} ...")
disk_crc = checksum_file(b_entry.paz_file)
print(f"  on-disk PAZ checksum: 0x{disk_crc:08X}")

# What does PAMT THINK the PAZ checksum should be? Read from PAMT directly.
# parse_pamt returns paz_table entries.
print(f"\nPAMT paz table (group 0008):")
for pe in pamt.paz_table:
    print(f"  paz idx {pe.index}: stored_crc=0x{pe.checksum:08X}  size={pe.size}")

# Now read the Ogre row through the VFS and check the f32 bytes.
data = vfs.read_entry_data(b_entry)
head = vfs.read_entry_data(h_entry)
print(f"\nLoaded characterinfo.pabgb: {len(data)} bytes (header {len(head)})")

tbl = parse_pabgb(data, head, "characterinfo.pabgb")
ogre = next((r for r in tbl.rows if r.name == "Boss_Ogre_55515"), None)
if not ogre:
    print("OGRE NOT FOUND")
    sys.exit(1)

print(f"\nOgre row idx={ogre.index} hash=0x{ogre.row_hash:08X} size={ogre.data_size}")

# Read the Ogre row bytes DIRECTLY from the PAZ (no parser),
# at the absolute byte offset, to verify what the engine reads.
# Absolute offset within PAZ = b_entry.offset + ogre.data_offset
# But pabgb is decompressed/decrypted first... Simpler: trust read_entry_data
# (which is what the game does internally). Just dump raw row bytes.
print(f"\nRaw Ogre row bytes (first 80) [from VFS read_entry_data]:")
print(" ", ogre.raw[:80].hex())

# Show key f32 fields with raw bytes.
print(f"\nKey field values:")
for idx, lbl in [(18, "MaxHp"), (24, "AggroRange"), (29, "MoveSpeed"),
                  (161, "Defence"), (174, "PoiseHp?"), (176, "AttackPower"),
                  (608, "SpawnX"), (610, "SpawnY"), (612, "SpawnZ")]:
    if idx >= len(ogre.fields):
        print(f"  [{idx}] {lbl}: out of range")
        continue
    f = ogre.fields[idx]
    val = f.value
    raw = f.raw.hex()
    # Re-interpret raw as f32 to be sure.
    if len(f.raw) == 4:
        raw_f32 = struct.unpack("<f", f.raw)[0]
    else:
        raw_f32 = "?"
    print(f"  [{idx:3d}] {lbl:<12s} kind={f.kind:<3s} value={val!s:<14s} raw={raw}  raw_as_f32={raw_f32}")
