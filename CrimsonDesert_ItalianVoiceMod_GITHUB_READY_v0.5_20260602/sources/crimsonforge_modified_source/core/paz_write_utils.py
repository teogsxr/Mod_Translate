"""Helpers for safely writing modified entries back into PAZ archives."""

import os

from core.pamt_parser import PamtFileEntry
from utils.platform_utils import get_file_timestamps, pad_to_16, set_file_timestamps
from utils.logger import get_logger

logger = get_logger("core.paz_write_utils")


def build_space_map(entries: list[PamtFileEntry]) -> dict[tuple[str, int], int]:
    """Build an available-space map for entries grouped by PAZ file."""
    by_paz: dict[str, list[PamtFileEntry]] = {}
    for entry in entries:
        by_paz.setdefault(entry.paz_file, []).append(entry)

    space_map: dict[tuple[str, int], int] = {}
    for paz_path, paz_entries in by_paz.items():
        sorted_entries = sorted(paz_entries, key=lambda e: e.offset)
        for i, entry in enumerate(sorted_entries):
            if i + 1 < len(sorted_entries):
                gap = sorted_entries[i + 1].offset - entry.offset
            else:
                gap = entry.comp_size + 16
            space_map[(paz_path, entry.offset)] = max(gap, entry.comp_size)
    return space_map


def write_entry_payload(
    entry: PamtFileEntry,
    payload: bytes,
    space_map: dict[tuple[str, int], int],
    preserve_timestamps: bool = True,
    zero_old_region_on_relocate: bool = True,
) -> tuple[int, int]:
    """Write payload into a PAZ entry, appending to the archive if needed.

    Returns:
        Tuple of (new_offset, written_size), where written_size is the logical
        compressed/encrypted payload size, not the padded archive size.
    """
    padded = pad_to_16(payload)
    paz_path = entry.paz_file
    max_space = space_map.get((paz_path, entry.offset), entry.comp_size)

    ts = None
    if preserve_timestamps:
        ts = get_file_timestamps(paz_path)

    if len(padded) <= max_space:
        logger.info("[PAZ_WRITE] Overwriting entry in %s at offset 0x%08X (size %d)", paz_path, entry.offset, len(padded))
        with open(paz_path, "r+b") as f:
            f.seek(entry.offset)
            f.write(padded)
        new_offset = entry.offset
    else:
        paz_size = os.path.getsize(paz_path)
        aligned = (paz_size + 15) & ~15
        logger.info("[PAZ_WRITE] Appending entry to %s at new offset 0x%08X (size %d)", paz_path, aligned, len(padded))
        with open(paz_path, "r+b") as f:
            if zero_old_region_on_relocate:
                f.seek(entry.offset)
                f.write(b"\x00" * entry.comp_size)
            f.seek(paz_size)
            if aligned > paz_size:
                f.write(b"\x00" * (aligned - paz_size))
            f.write(padded)
        new_offset = aligned

    if preserve_timestamps and ts:
        set_file_timestamps(paz_path, ts["modified"], ts["accessed"])

    return new_offset, len(payload)
