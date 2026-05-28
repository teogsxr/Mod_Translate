"""PABC mesh-skinning palette extractor.

This module reverse-engineers the SECOND use of the PAR ".pabc"
container — the **per-mesh bone palette** that runtime fills into
``SkinMeshLodBoneToOriginalBoneIndexBuffer`` on the GPU.

The same container is used for character-customization morph deltas
(handled by :mod:`core.pabc_parser`) but with a completely different
semantic interpretation. We disambiguate by file naming convention:
mesh-skinning PABCs sit next to the matching ``.pac`` file (e.g.
``character/cd_phw_00_nude_00_0001.pabc`` for the Damian body mesh),
while morph PABCs are referenced from ``.paccd`` customization files.

Format (verified Apr 2026 on Damian's body PABC, 85,676 bytes):

  PAR header (16 bytes): magic + version + sentinel — same as every
                          other PA container.
  [0x10..0x13] u32 record_count
  [0x14..]     record_count × 196-byte records

  Per-record layout (196 bytes each):
    [+0]           u8 flag (parent link / type bits — purpose TBD)
    [+1..+3]       24-bit PAB bone hash (low 3 bytes of u32 LE at +1)
    [+4..+67]      mat4 #1 — bind matrix (16 fp32, column-major flat)
    [+68..+131]    mat4 #2 — inverse bind matrix
    [+132..+195]   mat4 #3 — auxiliary bind (parent-relative or SRT)

THE KEY DISCOVERY — VERTEX SLOT ⇒ PABC RECORD INDEX, NOT PAB INDEX:

A PAC vertex stores up to 4 bone slots (u8) at bytes 32-35. The
runtime resolves each slot N to a global PAB bone via this PABC:

    slot N → PABC.records[N].bone_hash → matching PAB bone index

For Damian's body mesh, slot 17 was being misinterpreted as PAB[17]
= "Bip01 R Thigh" (the user's upper body shattered because vertices
on the chest were following the thigh bone). The CORRECT mapping is
slot 17 → PABC.records[17].bone_hash = 0xc710e6 → PAB "Bip01
R ThighTwist" (#28). R ThighTwist is a *child* of R Thigh in the
hierarchy, so the chest vertices weighted to it move with the upper
body's intended skinning chain instead of the leg.

Without this palette, the FBX export's skin clusters bind body
vertices to the wrong bones, which is why every animation we tried
produced spike-shatter on the upper body.
"""
from __future__ import annotations

import os
import struct
from dataclasses import dataclass, field

import lz4.block

from core.crypto_engine import decrypt
from utils.logger import get_logger

logger = get_logger("core.pabc_skin_palette")

PAR_MAGIC = b"PAR "
RECORD_SIZE = 196
HEADER_SIZE = 0x14


@dataclass
class PabcSkinRecord:
    """Per-bone record from a mesh-skinning PABC."""
    record_index: int = 0
    bone_hash_24: int = 0
    pab_bone_index: int = -1  # -1 if hash didn't resolve in the PAB
    flag_byte: int = 0
    bind_matrix: tuple = ()
    inv_bind_matrix: tuple = ()
    aux_matrix: tuple = ()


@dataclass
class PabcSkinPalette:
    """Per-mesh bone palette (slot index → PAB bone)."""
    path: str = ""
    record_count: int = 0
    records: list[PabcSkinRecord] = field(default_factory=list)

    def slot_to_pab(self, slot: int) -> int:
        """Resolve a vertex slot to a PAB bone index.

        Returns -1 for out-of-range slots or unresolved hashes.
        """
        if 0 <= slot < len(self.records):
            return self.records[slot].pab_bone_index
        return -1


def parse_skin_pabc(data: bytes, pab_hashes: list[int],
                    filename: str = "") -> PabcSkinPalette:
    """Parse decrypted+decompressed mesh-skinning PABC bytes.

    ``pab_hashes`` must be the list of 24-bit hashes from the
    matching PAB skeleton, in PAB index order. Used to resolve each
    record's bone hash to a global skeleton bone index.
    """
    palette = PabcSkinPalette(path=filename)

    if len(data) < HEADER_SIZE + RECORD_SIZE:
        logger.warning("PABC %s too short: %d bytes", filename, len(data))
        return palette
    if data[:4] != PAR_MAGIC:
        logger.warning("PABC %s missing PAR magic: %r", filename, data[:4])
        return palette

    record_count = struct.unpack_from("<I", data, 0x10)[0]
    if record_count == 0 or record_count > 100_000:
        logger.warning(
            "PABC %s implausible record_count=%d", filename, record_count
        )
        return palette

    available = (len(data) - HEADER_SIZE) // RECORD_SIZE
    if record_count > available:
        logger.warning(
            "PABC %s: header claims %d records but only %d fit; clamping",
            filename, record_count, available,
        )
        record_count = available

    palette.record_count = record_count
    hash_to_idx = {h: i for i, h in enumerate(pab_hashes)}

    n_resolved = 0
    for i in range(record_count):
        rec_off = HEADER_SIZE + i * RECORD_SIZE
        flag = data[rec_off]
        hash_u32 = struct.unpack_from("<I", data, rec_off + 1)[0]
        hash_24 = hash_u32 & 0x00FFFFFF
        pab_idx = hash_to_idx.get(hash_24, -1)

        m1 = struct.unpack_from("<16f", data, rec_off + 4)
        m2 = struct.unpack_from("<16f", data, rec_off + 4 + 64)
        m3 = struct.unpack_from("<16f", data, rec_off + 4 + 128)

        rec = PabcSkinRecord(
            record_index=i,
            bone_hash_24=hash_24,
            pab_bone_index=pab_idx,
            flag_byte=flag,
            bind_matrix=m1,
            inv_bind_matrix=m2,
            aux_matrix=m3,
        )
        palette.records.append(rec)
        if pab_idx >= 0:
            n_resolved += 1

    logger.info(
        "PABC palette %s: %d records, %d resolved to PAB bones",
        filename, len(palette.records), n_resolved,
    )
    return palette


def load_skin_pabc(vfs, pabc_path: str,
                   pab_hashes: list[int]) -> PabcSkinPalette | None:
    """Load and parse a mesh-skinning PABC by VFS path.

    Returns ``None`` if the file is not present in the VFS.
    """
    target = pabc_path.replace("\\", "/").lower()
    entry = None
    for _g, pamt in vfs._pamt_cache.items():
        for e in pamt.file_entries:
            p = (e.path or "").replace("\\", "/").lower()
            if p == target:
                entry = e
                break
        if entry:
            break
    if entry is None:
        logger.info("PABC palette not in VFS: %s", pabc_path)
        return None

    # Replicate VfsManager.read_entry_data, but bypass its decompression
    # fallback (which silently returns raw decrypted bytes when LZ4
    # fails — that path masks legit errors here).
    read_size = entry.comp_size if entry.compressed else entry.orig_size
    with open(entry.paz_file, "rb") as f:
        f.seek(entry.offset)
        raw = f.read(read_size)
    if entry.encrypted:
        raw = decrypt(raw, os.path.basename(entry.path))
    if entry.compressed and entry.compression_type != 0:
        raw = lz4.block.decompress(raw, uncompressed_size=entry.orig_size)

    return parse_skin_pabc(raw, pab_hashes, pabc_path)


def find_pabc_for_pac(pac_path: str) -> str:
    """Given a PAC path (e.g. ``character/cd_phw_00_nude_00_0001_damian.pac``),
    return the conventional PABC palette path.

    Pearl Abyss strips the trailing character-name suffix (everything
    after the last underscore before the extension) to derive the
    shared mesh PABC. So:

        ``cd_phw_00_nude_00_0001_damian.pac``
            → ``cd_phw_00_nude_00_0001.pabc``

    Returns the input path with .pac swapped to .pabc and the
    character suffix stripped. Caller verifies the result exists.
    """
    p = pac_path.replace("\\", "/")
    if p.lower().endswith(".pac"):
        base = p[:-4]
    else:
        base = p
    # Strip the trailing _<charname> if it looks like a character suffix
    # (most chars are 4-10 lowercase letters: damian, elai, karen, etc.)
    if "_" in base.split("/")[-1]:
        prefix, _, suffix = base.rpartition("_")
        if suffix.isalpha() and 3 <= len(suffix) <= 12:
            base = prefix
    return base + ".pabc"
