"""PAPGT root index manager.

The PAPGT file (meta/0.papgt) is the root of the game's Virtual File System.
It contains a list of package groups with their PAMT checksums.

Structure:
  [0:4]   Magic / flags
  [4:8]   Self-CRC (PaChecksum of data[12:])
  [8:12]  Header data
  [12:]   Group entries (12 bytes each):
            [0:4]  Flags / metadata
            [4:8]  Sequence number
            [8:12] PAMT CRC for this group

Entries are POSITIONAL - the Nth entry corresponds to the Nth package
group directory in sorted filesystem order. There is NO folder number
stored in the entry itself.

To find the CRC offset for package 0020:
  1. List all package directories from the filesystem
  2. Find 0020's index in that sorted list
  3. CRC offset = 12 + index * 12 + 8
"""

import os
import struct
from dataclasses import dataclass
from typing import Optional

from utils.logger import get_logger

logger = get_logger("core.papgt_manager")


@dataclass
class PapgtGroupEntry:
    """A group entry in the PAPGT root index."""
    entry_index: int
    flags: int
    sequence: int
    pamt_crc: int
    entry_offset: int
    crc_offset: int


@dataclass
class PapgtData:
    """Parsed PAPGT root index data."""
    path: str
    magic: int
    self_crc: int
    groups: list[PapgtGroupEntry]
    raw_data: bytes
    packages_path: str = ""


def parse_papgt(papgt_path: str) -> PapgtData:
    """Parse the PAPGT root index file.

    Args:
        papgt_path: Path to the 0.papgt file.

    Returns:
        PapgtData with all group entries.
    """
    with open(papgt_path, "rb") as f:
        data = f.read()

    if len(data) < 12:
        raise ValueError(
            f"PAPGT file too small ({len(data)} bytes): {papgt_path}. "
            f"Expected at least 12 bytes for the header."
        )

    magic = struct.unpack_from("<I", data, 0)[0]
    self_crc = struct.unpack_from("<I", data, 4)[0]

    groups = []
    off = 12
    index = 0
    while off + 12 <= len(data):
        flags = struct.unpack_from("<I", data, off)[0]
        sequence = struct.unpack_from("<I", data, off + 4)[0]
        pamt_crc = struct.unpack_from("<I", data, off + 8)[0]

        groups.append(PapgtGroupEntry(
            entry_index=index,
            flags=flags,
            sequence=sequence,
            pamt_crc=pamt_crc,
            entry_offset=off,
            crc_offset=off + 8,
        ))
        off += 12
        index += 1

    packages_path = ""
    papgt_dir = os.path.dirname(papgt_path)
    if os.path.basename(papgt_dir) == "meta":
        packages_path = os.path.dirname(papgt_dir)

    logger.info(
        "Parsed %s: %d group entries, self_crc=0x%08X",
        papgt_path, len(groups), self_crc
    )

    return PapgtData(
        path=papgt_path,
        magic=magic,
        self_crc=self_crc,
        groups=groups,
        raw_data=data,
        packages_path=packages_path,
    )


def _get_sorted_package_dirs(packages_path: str) -> list[str]:
    """Get sorted list of package directories (those containing 0.pamt)."""
    dirs = []
    for item in sorted(os.listdir(packages_path)):
        full = os.path.join(packages_path, item)
        if os.path.isdir(full) and os.path.isfile(os.path.join(full, "0.pamt")):
            dirs.append(item)
    return dirs


def get_pamt_crc_offset(papgt_data: PapgtData, folder_number: int) -> int:
    """Get the byte offset in PAPGT where the PAMT CRC is stored for a folder.

    PAPGT entries are positional (no folder number stored). We match
    the folder_number to its position in the sorted package directory list.

    Args:
        papgt_data: Parsed PAPGT data.
        folder_number: Package folder number (e.g., 12 for 0012, 20 for 0020).

    Returns:
        Byte offset into the PAPGT file where the PAMT CRC is stored.
    """
    packages_path = papgt_data.packages_path
    if not packages_path:
        papgt_dir = os.path.dirname(papgt_data.path)
        if os.path.basename(papgt_dir) == "meta":
            packages_path = os.path.dirname(papgt_dir)

    if not packages_path or not os.path.isdir(packages_path):
        raise ValueError(
            f"Cannot determine packages directory from PAPGT path: {papgt_data.path}. "
            f"Ensure the PAPGT file is at packages/meta/0.papgt."
        )

    folder_name = f"{folder_number:04d}"
    sorted_dirs = _get_sorted_package_dirs(packages_path)

    if folder_name not in sorted_dirs:
        raise ValueError(
            f"Package folder {folder_name} not found in {packages_path}. "
            f"Available: {sorted_dirs[:10]}..."
        )

    index = sorted_dirs.index(folder_name)

    if index >= len(papgt_data.groups):
        raise ValueError(
            f"Package folder {folder_name} is at position {index}, "
            f"but PAPGT only has {len(papgt_data.groups)} entries."
        )

    crc_offset = papgt_data.groups[index].crc_offset
    logger.info(
        "Package %s -> PAPGT entry %d, CRC offset 0x%03X",
        folder_name, index, crc_offset
    )
    return crc_offset


def find_group_by_index(papgt_data: PapgtData, index: int) -> Optional[PapgtGroupEntry]:
    """Find a group entry by its positional index."""
    if 0 <= index < len(papgt_data.groups):
        return papgt_data.groups[index]
    return None


def update_papgt_pamt_crc(
    papgt_raw: bytearray,
    pamt_crc_offset: int,
    new_pamt_crc: int,
) -> None:
    """Write a new PAMT CRC into the PAPGT at the given offset."""
    struct.pack_into("<I", papgt_raw, pamt_crc_offset, new_pamt_crc)


def update_papgt_self_crc(papgt_raw: bytearray) -> int:
    """Recalculate and write the PAPGT self-CRC. Returns the new CRC.

    The self-CRC is stored at offset 4, computed over data[12:].
    """
    from core.checksum_engine import pa_checksum
    new_crc = pa_checksum(bytes(papgt_raw[12:]))
    struct.pack_into("<I", papgt_raw, 4, new_crc)
    return new_crc
