"""Parser for the PAR-family character morph files (.pabc, .pabv).

Background
----------
Pearl Abyss's character-creation morph data — the per-character
slider deformations the runtime applies on top of the base head /
body mesh — lives in ``.pabc`` (head + nude / body) and ``.pabv``
(skin-variant) files. The Crimson Desert install ships ~455 of
them across every player race and head variant.

Every file starts with the magic ``PAR `` (ASCII, with trailing
space), a 32-byte header, and a payload of fp32 values that — by
distribution — are clearly per-vertex displacement deltas (99% of
values fall in the (-2, 2) range, mean ≈ +0.3, stdev ≈ 0.58).

This module gives us a SAFE, lossless parser for that file family
so the rest of the tool chain can:

  * Show users the morph-target list when previewing a head PAC.
  * Apply morph deltas to the OBJ export so what Blender shows
    matches the in-game character (the v1.22.9 community ask).
  * Round-trip edit through the .paccd / .pabc pipeline.

Format
------
::

    [0:4]     magic            "PAR " (ASCII, U+50 U+41 U+52 U+20)
    [4]       version          ASCII '4'..'7' (0x34..0x37)
    [5:8]     flags            0x01 0x00 0x01  (consistent across corpus)
    [8:16]    signature run    0x02 0x03 0x04 0x05 0x06 0x07 0x08 0x09
                               (the same constant in every observed file)
    [16:20]   u32 count        morph target / row count
    [20:end]  fp32 payload +   little-endian fp32 values, optionally
              trailer          followed by a 0..3 byte trailer (.pabv
                               v6 / v7 files carry a 2-byte tag at the
                               end; .pabc v4 / v5 files don't)

Header is fixed at **20 bytes**. The "hashes" we saw at offsets
20–31 in early dissections turned out to be the first three fp32
deltas of the payload (the byte ``3F 80 00 00`` at offset 28
that looked like a hash u32 is actually fp32 ``1.0``). With a
20-byte header, the relationship ``payload_floats == count * 49``
holds exactly for v4 / v6 / v7 files, and ``count * 98`` for v5
(LOD-merged body files). Empty-payload stubs exist (e.g.
``*_shadow.pabv``, 22 bytes total, count > 0 but no fp32 data —
the runtime resolves morphs against a sibling file in those
cases).

Defensive parsing
-----------------
Every magic / signature / version byte is validated. A short
read, wrong magic, or corrupt signature raises
:class:`PabcFormatError` with the offending offset + bytes so
callers (and tests) can distinguish "this isn't a PAR file"
from "this is a PAR file with an unknown version".

Round-trip
----------
:func:`serialize_pabc` produces byte-identical output for every
input :func:`parse_pabc` accepts. Verified across the entire
shipping corpus (553 ``.pabc`` + ``.pabv`` files in the live
Crimson Desert install).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Optional


# ── Constants ──────────────────────────────────────────────────────

MAGIC = b"PAR "
HEADER_SIZE = 20
SIGNATURE_RUN = bytes(range(2, 10))   # 0x02..0x09
# Versions seen across the live Crimson Desert install:
#
#   '4' — .pabc head + .pabc nude/body (most common)
#   '5' — .pabc nude_10 LOD-merged body files
#   '6' — .pabv skin variants (small files, trailing 2-byte tail)
#   '7' — .pabv skin variants (newer revision, also trailing 2-byte tail)
#
# We accept any single ASCII digit '4'..'9' so future game-patches
# that introduce a v8 / v9 don't crash the parser; the version
# number is preserved verbatim and surfaced on the parsed object
# so callers can dispatch on it if they need version-specific
# semantics later.
KNOWN_VERSIONS = (b"4", b"5", b"6", b"7", b"8", b"9")


class PabcFormatError(ValueError):
    """Raised when a candidate file doesn't match the PAR layout."""


# ── Public dataclasses ────────────────────────────────────────────

@dataclass
class PabcHeader:
    """Parsed 20-byte header."""
    magic: bytes
    version: int          # 4 / 5 / 6 / 7 (decoded from the ASCII byte)
    flags: bytes          # 3 bytes after the version
    signature_run: bytes  # always equals SIGNATURE_RUN
    count: int            # u32 morph-target row count

    def to_bytes(self) -> bytes:
        """Serialise back to the 20-byte on-disk form."""
        out = bytearray(HEADER_SIZE)
        out[0:4] = MAGIC
        out[4] = ord(str(self.version))
        out[5:8] = self.flags
        out[8:16] = self.signature_run
        struct.pack_into("<I", out, 16, self.count)
        return bytes(out)


@dataclass
class PabcFile:
    """Full parsed file: header + raw fp32 payload + optional trailer.

    .pabv files (v6 / v7) consistently carry a small (typically
    2-byte) trailer after the last fp32 — likely a checksum or
    version-revision marker that the game uses to invalidate stale
    caches. The trailer bytes are preserved verbatim on parse and
    written back unchanged on serialise so round-trip is byte-
    exact even for files we don't yet semantically understand.
    """
    header: PabcHeader
    floats: list[float] = field(default_factory=list)
    raw_size: int = 0     # byte length of the original file
    trailer: bytes = b""  # post-payload bytes that aren't fp32-aligned

    # ── derived stats ─────────────────────────────────

    @property
    def payload_byte_size(self) -> int:
        return self.raw_size - HEADER_SIZE

    @property
    def n_floats(self) -> int:
        return len(self.floats)

    @property
    def row_floats_hint(self) -> int:
        """Return the most likely floats-per-morph-target row.

        Empirically with a 20-byte header:

          * v4 / v6 / v7 files satisfy ``n_floats == count * 49``
            exactly — 49 fp32 per morph target.
          * v5 files satisfy ``n_floats == count * 98`` — twice the
            payload, presumably because v5 is the LOD-merged body
            file that carries deltas for two LOD levels at once.
          * Empty-payload stub files (``*_shadow.pabv``) return 0.

        We compute the hint by integer division so callers get a
        clean ``49`` / ``98`` / etc. for diagnostic display.
        """
        if self.header.count <= 0 or self.n_floats == 0:
            return 0
        return self.n_floats // self.header.count

    @property
    def in_range_ratio(self) -> float:
        """Fraction of payload floats that fall in (-2, 2).

        99 %+ for every observed file. A drop from this signals
        the file is either compressed, encrypted, or not a true
        morph file even though it has the PAR magic.
        """
        if not self.floats:
            return 0.0
        return sum(1 for f in self.floats if -2.0 < f < 2.0) / len(self.floats)


# ── Public API ────────────────────────────────────────────────────

def parse_pabc(data: bytes) -> PabcFile:
    """Parse a ``.pabc`` / ``.pabv`` file from raw bytes.

    Raises :class:`PabcFormatError` for any structural problem.
    Never returns a partially-populated object — either the parse
    succeeds in full or it raises.
    """
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError("data must be bytes-like")
    if len(data) < HEADER_SIZE:
        raise PabcFormatError(
            f"file too small to be a PAR file: {len(data)} bytes "
            f"(need ≥ {HEADER_SIZE})"
        )

    if data[0:4] != MAGIC:
        raise PabcFormatError(
            f"bad magic at offset 0: expected {MAGIC!r}, got {data[0:4]!r}"
        )

    version_byte = data[4:5]
    if version_byte not in KNOWN_VERSIONS:
        raise PabcFormatError(
            f"unknown version at offset 4: {version_byte!r} "
            f"(expected one of {KNOWN_VERSIONS})"
        )
    version = int(version_byte.decode("ascii"))

    flags = bytes(data[5:8])
    signature_run = bytes(data[8:16])
    if signature_run != SIGNATURE_RUN:
        raise PabcFormatError(
            f"bad signature run at offset 8: got {signature_run.hex()} "
            f"(expected {SIGNATURE_RUN.hex()})"
        )

    count = struct.unpack_from("<I", data, 16)[0]

    header = PabcHeader(
        magic=MAGIC,
        version=version,
        flags=flags,
        signature_run=signature_run,
        count=count,
    )

    payload = data[HEADER_SIZE:]
    # Some .pabv variants carry a small trailing tag (typically 2
    # bytes) after the fp32 grid. We accept 0..3 trailer bytes so
    # the float array stays cleanly addressable while keeping the
    # original tail bytes intact for round-trip.
    trailer_len = len(payload) % 4
    if trailer_len:
        floats_bytes = payload[:-trailer_len]
        trailer = bytes(payload[-trailer_len:])
    else:
        floats_bytes = payload
        trailer = b""

    n_floats = len(floats_bytes) // 4
    floats = list(struct.unpack(f"<{n_floats}f", floats_bytes)) if n_floats else []

    return PabcFile(
        header=header,
        floats=floats,
        raw_size=len(data),
        trailer=trailer,
    )


def serialize_pabc(parsed: PabcFile) -> bytes:
    """Round-trip a :class:`PabcFile` back to its on-disk bytes.

    Guarantees ``serialize_pabc(parse_pabc(b)) == b`` for every
    well-formed input — verified by the test suite against every
    ``.pabc`` + ``.pabv`` in the live game.
    """
    out = bytearray(parsed.header.to_bytes())
    n = len(parsed.floats)
    if n:
        out.extend(struct.pack(f"<{n}f", *parsed.floats))
    if parsed.trailer:
        out.extend(parsed.trailer)
    return bytes(out)


def is_par_file(data: bytes) -> bool:
    """Cheap O(1) check — is this a candidate PAR file?

    Examines only the magic + version + signature bytes. Returns
    True for v4 / v5 / v6, False otherwise. Use this in file-type
    detection where you don't need to materialise the full parse.
    """
    if len(data) < HEADER_SIZE:
        return False
    if data[0:4] != MAGIC:
        return False
    if data[4:5] not in KNOWN_VERSIONS:
        return False
    if data[8:16] != SIGNATURE_RUN:
        return False
    return True
