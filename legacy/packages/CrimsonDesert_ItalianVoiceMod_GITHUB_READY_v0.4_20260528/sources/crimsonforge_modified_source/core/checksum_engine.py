"""PaChecksum algorithm - Pearl Abyss custom Bob Jenkins Lookup3 variant.

Loads the MSVC-compiled Python C extension ``core._pa_checksum``
(file: ``core/_pa_checksum.cp*-*.pyd``) for ~100x speedup over the
pure-Python fallback below.

Why Python C extension and not a ctypes DLL:
Prior to v1.22.5 we shipped a MinGW-compiled ``pa_checksum.dll``
loaded via ``ctypes.CDLL(...)``. Windows Defender flagged it as
suspicious on multiple users' machines because:

  1. The DLL was unsigned.
  2. MinGW-compiled binaries share byte-level patterns with malware
     loaders that AV heuristics lock onto.
  3. Standalone ctypes DLLs are treated as unknown low-reputation
     code. ``.pyd`` files, by contrast, load via Python's import
     machinery and inherit Python's AV trust path.

Switching to a .pyd built with MSVC eliminates the false positives
while keeping the speedup. The pure-Python fallback remains as a
second line of defence for source-only installs where the extension
hasn't been compiled.

Build the extension:
    python setup_checksum.py build_ext --inplace

The checksum chain is:
  PAZ CRC -> stored in PAMT PAZ table
  PAMT self-CRC -> computed over pamt[12:], stored at pamt[0:4]
  PAPGT self-CRC -> computed over papgt[12:], stored at papgt[4:8]
"""

import struct

_USE_C = False

# Try loading the compiled Python C extension. Missing is fine —
# we fall back to pure Python below.
try:
    from core._pa_checksum import pa_checksum as _c_pa_checksum
    from core._pa_checksum import checksum_file as _c_checksum_file
    _USE_C = True
except ImportError:
    pass

# Kept for back-compat: code that previously gated on _USE_DLL
# still imports successfully but it will always be False now.
_USE_DLL = False

PA_MAGIC = 0x2145E233
MASK = 0xFFFFFFFF


def _rol(x: int, k: int) -> int:
    return ((x << k) | (x >> (32 - k))) & MASK


def _ror(x: int, k: int) -> int:
    return ((x >> k) | (x << (32 - k))) & MASK


def _pa_checksum_python(data: bytes) -> int:
    """Optimized pure Python fallback for PaChecksum.

    Uses pre-unpacked uint32 array for the main loop (~10x faster than
    struct.unpack_from per iteration on large files).
    """
    length = len(data)
    if length == 0:
        return 0

    M = MASK
    a = b = c = (length - PA_MAGIC) & M

    # Pre-unpack all 12-byte blocks as uint32 triples for speed.
    # This avoids calling struct.unpack_from in the hot loop.
    full_blocks = length // 12
    tail_start = full_blocks * 12

    if full_blocks > 0:
        # Unpack all complete 12-byte (3 x uint32) blocks at once
        fmt = f"<{full_blocks * 3}I"
        words = struct.unpack_from(fmt, data, 0)
        wi = 0
        for _ in range(full_blocks):
            a = (a + words[wi]) & M
            b = (b + words[wi + 1]) & M
            c = (c + words[wi + 2]) & M
            wi += 3

            a = (a - c) & M; a ^= ((c << 4) | (c >> 28)) & M;  c = (c + b) & M
            b = (b - a) & M; b ^= ((a << 6) | (a >> 26)) & M;  a = (a + c) & M
            c = (c - b) & M; c ^= ((b << 8) | (b >> 24)) & M;  b = (b + a) & M
            a = (a - c) & M; a ^= ((c << 16) | (c >> 16)) & M; c = (c + b) & M
            b = (b - a) & M; b ^= ((a << 19) | (a >> 13)) & M; a = (a + c) & M
            c = (c - b) & M; c ^= ((b << 4) | (b >> 28)) & M;  b = (b + a) & M

    # Handle remaining bytes (0-12)
    remaining = length - tail_start
    offset = tail_start

    if remaining >= 12: c = (c + (data[offset + 11] << 24)) & M
    if remaining >= 11: c = (c + (data[offset + 10] << 16)) & M
    if remaining >= 10: c = (c + (data[offset + 9] << 8)) & M
    if remaining >= 9:  c = (c + data[offset + 8]) & M
    if remaining >= 8:  b = (b + (data[offset + 7] << 24)) & M
    if remaining >= 7:  b = (b + (data[offset + 6] << 16)) & M
    if remaining >= 6:  b = (b + (data[offset + 5] << 8)) & M
    if remaining >= 5:  b = (b + data[offset + 4]) & M
    if remaining >= 4:  a = (a + (data[offset + 3] << 24)) & M
    if remaining >= 3:  a = (a + (data[offset + 2] << 16)) & M
    if remaining >= 2:  a = (a + (data[offset + 1] << 8)) & M
    if remaining >= 1:  a = (a + data[offset]) & M

    v82 = ((b ^ c) - _rol(b, 14)) & M
    v83 = ((a ^ v82) - _rol(v82, 11)) & M
    v84 = ((v83 ^ b) - _ror(v83, 7)) & M
    v85 = ((v84 ^ v82) - _rol(v84, 16)) & M
    v86 = _rol(v85, 4)
    t = ((v83 ^ v85) - v86) & M
    v87 = ((t ^ v84) - _rol(t, 14)) & M

    return ((v87 ^ v85) - _ror(v87, 8)) & M


def pa_checksum(data: bytes) -> int:
    """Compute PaChecksum. Uses the compiled C extension when
    available, otherwise the pure-Python fallback.
    """
    if _USE_C:
        return _c_pa_checksum(data)
    return _pa_checksum_python(data)


def checksum_file(path: str, skip_header: int = 0) -> int:
    """Compute PaChecksum for a file, optionally skipping header bytes."""
    if _USE_C:
        return _c_checksum_file(path, skip_header)
    with open(path, "rb") as f:
        if skip_header > 0:
            f.seek(skip_header)
        data = f.read()
    return pa_checksum(data)


def verify_pamt_checksum(pamt_path: str) -> tuple[bool, int, int]:
    """Verify the self-checksum of a PAMT file."""
    with open(pamt_path, "rb") as f:
        data = f.read()
    stored_crc = struct.unpack_from("<I", data, 0)[0]
    computed_crc = pa_checksum(data[12:])
    return (stored_crc == computed_crc, stored_crc, computed_crc)


def verify_papgt_checksum(papgt_path: str) -> tuple[bool, int, int]:
    """Verify the self-checksum of a PAPGT file."""
    with open(papgt_path, "rb") as f:
        data = f.read()
    stored_crc = struct.unpack_from("<I", data, 4)[0]
    computed_crc = pa_checksum(data[12:])
    return (stored_crc == computed_crc, stored_crc, computed_crc)
