"""Paloc localization file parser.

Parses and writes .paloc files which store game localization strings.

Paloc format (real game format):
  The file is a stream of length-prefixed UTF-8 strings. Localization
  entries appear as triplets: [empty_string, numeric_id, text_value].

Performance: Uses pre-compiled struct, greedy jump scanning (not
byte-by-byte), and memoryview. Parses 13MB / 102K entries in ~1s.
"""

import struct
from dataclasses import dataclass
from typing import Optional

from utils.logger import get_logger

logger = get_logger("core.paloc_parser")

_U32 = struct.Struct("<I")
_U32SZ = 4
_MAX_STR_LEN = 50_000_000


@dataclass
class PalocEntry:
    """A single key-value entry in a paloc file."""
    key: str
    value: str
    key_offset: int
    value_offset: int

    def __repr__(self):
        val_preview = self.value[:50] + "..." if len(self.value) > 50 else self.value
        return f"PalocEntry(key={self.key!r}, value={val_preview!r})"


@dataclass
class PalocData:
    """Parsed paloc file data."""
    path: str
    entries: list[PalocEntry]
    raw_data: bytes
    header_entries: list[PalocEntry]


def _scan_strings_fast(data: bytes) -> list[tuple[int, int, str]]:
    """Scan all length-prefixed strings from data using greedy jumping.

    Starts at offset 4 (skip file header) and follows length chains.
    When a length looks invalid, skips 4 bytes and retries. This is
    O(N) where N = file size, not O(N * entries).

    Returns list of (offset, length, text) tuples.
    """
    data_len = len(data)
    strings = []
    off = _U32SZ

    while off + _U32SZ <= data_len:
        slen = _U32.unpack_from(data, off)[0]

        if slen > _MAX_STR_LEN or off + _U32SZ + slen > data_len:
            off += _U32SZ
            continue

        if slen == 0:
            strings.append((off, 0, ""))
            off += _U32SZ
            continue

        start = off + _U32SZ
        chunk = data[start:start + slen]

        try:
            text = chunk.decode("utf-8")
        except UnicodeDecodeError:
            off += _U32SZ
            continue

        has_control = False
        for b in chunk:
            if b < 0x09:
                has_control = True
                break
            if 0x0E <= b <= 0x1F:
                has_control = True
                break

        if has_control:
            off += _U32SZ
            continue

        strings.append((off, slen, text))
        off += _U32SZ + slen

    return strings


def _is_symbolic_key(text: str) -> bool:
    """Check if a string looks like a symbolic localization key.

    Symbolic keys are ASCII identifiers like ``questdialog_hello_00496``,
    ``textdialog_quest_00123``, ``epilogue_npc_01``, etc.  They contain
    only ASCII letters, digits, underscores, and dots, start with a
    letter or underscore, and are short enough to be a key (not a
    sentence of translated text).
    """
    if not text or len(text) > 200:
        return False
    first = text[0]
    if not (first.isascii() and (first.isalpha() or first == "_")):
        return False
    for ch in text:
        if not (ch.isascii() and (ch.isalnum() or ch in "_.-")):
            return False
    return True


def parse_paloc(data: bytes) -> list[PalocEntry]:
    """Parse raw paloc bytes into a list of key-value entries.

    Handles two entry formats found in Crimson Desert paloc files:

    1. **Numeric triplets** — ``[empty_string, numeric_id, text_value]``
       These have a zero-length sentinel followed by a numeric key and the
       localized text.

    2. **Symbolic pairs** — ``[symbolic_key, text_value]``
       These have an ASCII identifier key (e.g. ``questdialog_hello_00496``)
       followed directly by the localized text, with no empty sentinel.

    Args:
        data: Raw paloc file bytes (already decrypted and decompressed).

    Returns:
        List of PalocEntry with all localization strings.
    """
    data_len = len(data)
    if data_len < _U32SZ:
        return []

    all_strings = _scan_strings_fast(data)

    entries = []
    numeric_count = 0
    symbolic_count = 0
    i = 0
    count = len(all_strings)
    while i < count:
        s_off, s_len, s_text = all_strings[i]

        # Pattern 1: Numeric triplet  [empty, numeric_id, text]
        if s_len == 0 and i + 2 < count:
            id_off, id_len, id_text = all_strings[i + 1]
            val_off, val_len, val_text = all_strings[i + 2]
            if id_len > 0 and id_text and id_text[0].isdigit():
                entries.append(PalocEntry(
                    key=id_text,
                    value=val_text,
                    key_offset=id_off,
                    value_offset=val_off,
                ))
                numeric_count += 1
                i += 3
                continue

        # Pattern 2: Symbolic pair  [symbolic_key, text]
        if s_len > 0 and _is_symbolic_key(s_text) and i + 1 < count:
            val_off, val_len, val_text = all_strings[i + 1]
            entries.append(PalocEntry(
                key=s_text,
                value=val_text,
                key_offset=s_off,
                value_offset=val_off,
            ))
            symbolic_count += 1
            i += 2
            continue

        i += 1

    logger.info("Parsed paloc: %d entries (%d numeric, %d symbolic) from %d bytes (%d raw strings)",
                len(entries), numeric_count, symbolic_count, data_len, count)
    return entries


def parse_paloc_file(path: str) -> PalocData:
    """Parse a paloc file from disk (assumes already decrypted/decompressed)."""
    with open(path, "rb") as f:
        data = f.read()

    all_entries = parse_paloc(data)
    header_entries = []
    string_entries = []
    for entry in all_entries:
        if entry.key.startswith("@") or entry.key.startswith("#"):
            header_entries.append(entry)
        else:
            string_entries.append(entry)

    return PalocData(
        path=path,
        entries=string_entries,
        raw_data=data,
        header_entries=header_entries,
    )


def build_paloc(entries: list[PalocEntry], header_entries: Optional[list[PalocEntry]] = None) -> bytes:
    """Build raw paloc bytes from a list of key-value entries."""
    parts = []
    all_entries = []
    if header_entries:
        all_entries.extend(header_entries)
    all_entries.extend(entries)

    for entry in all_entries:
        key_bytes = entry.key.encode("utf-8")
        value_bytes = entry.value.encode("utf-8")
        parts.append(_U32.pack(len(key_bytes)))
        parts.append(key_bytes)
        parts.append(_U32.pack(len(value_bytes)))
        parts.append(value_bytes)

    return b"".join(parts)


def replace_value_in_raw(
    raw_data: bytearray,
    entry: PalocEntry,
    new_value: str,
) -> bytearray:
    """Replace a single value in raw paloc data, adjusting length prefix."""
    old_value_bytes = entry.value.encode("utf-8")
    new_value_bytes = new_value.encode("utf-8")
    value_data_offset = entry.value_offset + _U32SZ
    old_len = len(old_value_bytes)

    result = bytearray()
    result.extend(raw_data[:entry.value_offset])
    result.extend(_U32.pack(len(new_value_bytes)))
    result.extend(new_value_bytes)
    result.extend(raw_data[value_data_offset + old_len:])
    return result


def splice_values_in_raw(
    raw_data: bytes | bytearray,
    replacements: list[tuple[PalocEntry, str]],
) -> bytes:
    """Apply multiple value replacements in a single sequential rebuild.

    The caller must provide entries with original offsets from the same
    raw paloc buffer. Replacements are sorted by value offset so the file
    is rebuilt once, which avoids cloning the full buffer for each change.
    """
    if not replacements:
        return bytes(raw_data)

    ordered = sorted(replacements, key=lambda item: item[0].value_offset)
    result = bytearray()
    cursor = 0

    for entry, new_value in ordered:
        if entry.value_offset < cursor:
            raise ValueError(
                f"Overlapping or duplicate replacement at offset 0x{entry.value_offset:08X}"
            )

        old_len = len(entry.value.encode("utf-8"))
        new_value_bytes = new_value.encode("utf-8")
        old_end = entry.value_offset + _U32SZ + old_len

        result.extend(raw_data[cursor:entry.value_offset])
        result.extend(_U32.pack(len(new_value_bytes)))
        result.extend(new_value_bytes)
        cursor = old_end

    result.extend(raw_data[cursor:])
    return bytes(result)


def get_string_count(entries: list[PalocEntry]) -> int:
    """Count the number of non-empty string entries."""
    return sum(1 for e in entries if e.value.strip())


def filter_entries(
    entries: list[PalocEntry],
    search: str = "",
    key_filter: str = "",
) -> list[PalocEntry]:
    """Filter entries by search text (in key or value) and key prefix."""
    result = entries
    if key_filter:
        key_filter_lower = key_filter.lower()
        result = [e for e in result if key_filter_lower in e.key.lower()]
    if search:
        search_lower = search.lower()
        result = [e for e in result
                  if search_lower in e.key.lower() or search_lower in e.value.lower()]
    return result
