"""NAV navmesh parser for Crimson Desert.

Parses .nav navigation mesh files used by the game's AI pathfinding.
The format uses a tile/grid-based cell reference system with 16-byte
records containing cell IDs and connectivity data.

NAV format (proprietary):
  No header — file starts directly with 16-byte cell records.
  Each record: [4B cell_id] [4B grid_ref] [4B flags] [4B neighbor]
  Grid references use 0xFEFFFF prefix with incrementing tile indices.

This parser extracts basic structure info (cell count, tile range)
but does not fully decode the pathfinding connectivity graph.
"""

from __future__ import annotations

import os
import struct
from dataclasses import dataclass, field

from utils.logger import get_logger

logger = get_logger("core.navmesh_parser")


@dataclass
class NavCell:
    """A single navigation cell."""
    index: int = 0
    cell_id: int = 0
    grid_ref: int = 0
    flags: int = 0
    neighbor: int = 0
    tile_x: int = 0
    tile_y: int = 0


@dataclass
class ParsedNavmesh:
    """Parsed navmesh data."""
    path: str = ""
    cell_count: int = 0
    tile_min: tuple[int, int] = (0, 0)
    tile_max: tuple[int, int] = (0, 0)
    cells: list[NavCell] = field(default_factory=list)
    file_size: int = 0


def parse_nav(data: bytes, filename: str = "") -> ParsedNavmesh:
    """Parse a .nav navigation mesh file.

    Extracts cell records and tile grid extent.
    """
    result = ParsedNavmesh(path=filename, file_size=len(data))

    if len(data) < 16:
        return result

    record_size = 16
    record_count = len(data) // record_size
    result.cell_count = record_count

    tile_xs = set()
    tile_ys = set()

    # Parse a sample of records (full parse would be too slow for large files)
    sample_count = min(record_count, 10000)

    for i in range(sample_count):
        off = i * record_size
        cell_id, grid_ref, flags, neighbor = struct.unpack_from("<IIII", data, off)

        # Extract tile coordinates from grid_ref
        # Pattern: 0xFEFFFFxx where xx is tile index
        tile_byte = (grid_ref >> 24) & 0xFF
        grid_low = grid_ref & 0x00FFFFFF

        cell = NavCell(
            index=i,
            cell_id=cell_id,
            grid_ref=grid_ref,
            flags=flags,
            neighbor=neighbor,
            tile_x=cell_id & 0xFFFF,
            tile_y=(cell_id >> 16) & 0xFFFF,
        )
        result.cells.append(cell)
        tile_xs.add(cell.tile_x)
        tile_ys.add(cell.tile_y)

    if tile_xs and tile_ys:
        result.tile_min = (min(tile_xs), min(tile_ys))
        result.tile_max = (max(tile_xs), max(tile_ys))

    logger.info("Parsed NAV %s: %d cells, tiles (%d,%d)->(%d,%d)",
                filename, result.cell_count,
                result.tile_min[0], result.tile_min[1],
                result.tile_max[0], result.tile_max[1])
    return result


def get_nav_summary(data: bytes) -> str:
    """Get a human-readable summary of a NAV file."""
    try:
        nav = parse_nav(data)
        return (
            f"Navigation Mesh\n"
            f"Cells: {nav.cell_count:,}\n"
            f"Tile grid: ({nav.tile_min[0]},{nav.tile_min[1]}) to ({nav.tile_max[0]},{nav.tile_max[1]})\n"
            f"File size: {nav.file_size:,} bytes"
        )
    except Exception as e:
        return f"NAV parse error: {e}"


def is_navmesh_file(path: str) -> bool:
    """Check if a file is a navigation mesh."""
    return os.path.splitext(path.lower())[1] == ".nav"
