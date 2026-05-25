"""Virtual File System manager.

Traverses the full PAZ/PAMT/PAPGT hierarchy to provide a unified view
of all game files across all package groups. Handles extraction with
automatic decryption and decompression.
"""

import os
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

from core.pamt_parser import parse_pamt, PamtData, PamtFileEntry
from core.papgt_manager import parse_papgt, PapgtData
from core.paz_reader import PazReader
from core.crypto_engine import decrypt, encrypt
from core.compression_engine import decompress, compress
from utils.logger import get_logger

logger = get_logger("core.vfs_manager")


@dataclass
class VfsNode:
    """A node in the virtual file system tree."""
    name: str
    is_dir: bool
    children: dict = field(default_factory=dict)
    entry: Optional[PamtFileEntry] = None
    package_group: str = ""


class VfsManager:
    """Manages the game's hierarchical Virtual File System.

    Provides a unified view of all files across all package groups,
    with extraction, decryption, and decompression support.
    """

    def __init__(self, packages_path: str):
        """Initialize VFS from the game packages directory.

        Args:
            packages_path: Path to the packages/ directory (contains 0003/, 0012/, 0020/, meta/).
        """
        self._packages_path = Path(packages_path)
        if not self._packages_path.is_dir():
            raise FileNotFoundError(
                f"Packages directory not found: {packages_path}. "
                f"Select the game's packages/ directory containing meta/, 0012/, 0020/, etc."
            )

        self._papgt_data: Optional[PapgtData] = None
        self._pamt_cache: dict[str, PamtData] = {}
        self._root = VfsNode(name="root", is_dir=True)
        # Groups whose entries are cached but NOT yet inserted into the
        # ``_root`` trie. ``get_tree`` drains this set on first access.
        # See ``load_pamt`` for the rationale (cold-load perf win).
        self._tree_dirty_groups: set[str] = set()
        self._logged_processing_warnings: set[tuple[str, str]] = set()

    def load_papgt(self) -> PapgtData:
        """Load and parse the PAPGT root index."""
        papgt_path = self._packages_path / "meta" / "0.papgt"
        if not papgt_path.exists():
            raise FileNotFoundError(
                f"PAPGT root index not found: {papgt_path}. "
                f"Check that the packages/meta/ directory contains 0.papgt."
            )
        self._papgt_data = parse_papgt(str(papgt_path))
        return self._papgt_data

    def load_pamt(self, group_dir: str) -> PamtData:
        """Load and parse a PAMT index for a package group.

        Args:
            group_dir: Package group directory name (e.g., '0012', '0020').

        Returns:
            Parsed PAMT data.
        """
        cached = self._pamt_cache.get(group_dir)
        if cached is not None:
            return cached

        pamt_path = self._packages_path / group_dir / "0.pamt"
        if not pamt_path.exists():
            raise FileNotFoundError(
                f"PAMT index not found: {pamt_path}. "
                f"Package group {group_dir} may not exist or is incomplete."
            )

        paz_dir = str(self._packages_path / group_dir)
        pamt_data = parse_pamt(str(pamt_path), paz_dir=paz_dir)
        self._pamt_cache[group_dir] = pamt_data

        # ── PERF (2026-05-07) ──
        # We previously called ``_add_to_tree`` for every PAMT entry to
        # populate ``self._root``, a hierarchical VFS trie. Cold-load
        # profiling found this added ~5 s during the parallel paloc
        # scan (it had to allocate ~1.5 M VfsNode objects across all
        # 34 groups, and the work serialised on the GIL even with 8
        # threads). A repo-wide grep for ``get_tree``, ``vfs._root``,
        # and ``VfsNode`` confirms NOTHING outside ``vfs_manager.py``
        # consumes that trie — every real consumer iterates
        # ``pamt.file_entries`` directly.
        #
        # We now mark the cache stale so the trie can be lazy-built
        # on first ``get_tree()`` access, and skip the per-entry walk
        # during the hot paloc-scan path.
        self._tree_dirty_groups.add(group_dir)

        logger.info(
            "Loaded PAMT for %s: %d files",
            group_dir, len(pamt_data.file_entries)
        )
        return pamt_data

    def list_package_groups(self) -> list[str]:
        """List all available package group directories."""
        groups = []
        for item in sorted(self._packages_path.iterdir()):
            if item.is_dir() and (item / "0.pamt").exists():
                groups.append(item.name)
        return groups

    def get_pamt(self, group_dir: str) -> Optional[PamtData]:
        """Get cached PAMT data for a group. Returns None if not loaded."""
        return self._pamt_cache.get(group_dir)

    def invalidate_pamt_cache(self, group_dir: str):
        """Clear a group from the PAMT cache to force a reload from disk.

        Call this after repacking a group to ensure subsequent reads see
        the updated offsets and metadata.
        """
        if group_dir in self._pamt_cache:
            del self._pamt_cache[group_dir]
            logger.info("Invalidated PAMT cache for group: %s", group_dir)
        self._tree_dirty_groups.discard(group_dir)
        # Drop the texture-service combined index too — its entries
        # came from the PAMT we just invalidated.
        try:
            from core.mesh_texture_service import invalidate_pamt_index_cache
            invalidate_pamt_index_cache(self)
        except Exception:
            pass

    def reload(self) -> None:
        """Drop every cache and rebuild the VFS from the packages
        directory.

        Use after patching, after the user runs Steam's "Verify
        Integrity of Game Files", or whenever the on-disk state
        diverges from what this VfsManager currently has in memory.

        After ``reload()``:

          * ``_papgt_data`` is cleared (lazy-loaded on next call to
            :meth:`load_papgt`)
          * ``_pamt_cache`` is emptied so every group re-parses
            from disk on next :meth:`load_pamt` call
          * the VFS tree is rebuilt as empty and will re-populate
            on demand the same way it does after construction
          * processing-warning dedup set is cleared so any new
            warnings surface once each

        The packages directory itself is re-checked; if the user
        uninstalled the game between loads, we raise a
        FileNotFoundError the same as the constructor.

        This method is intentionally cheap (microseconds) because
        the heavy lifting still happens lazily on first access. It
        does NOT pre-warm caches — callers that want eager reload
        should iterate :meth:`list_package_groups` and call
        :meth:`load_pamt` for each, exactly as the initial load
        flow does.
        """
        if not self._packages_path.is_dir():
            raise FileNotFoundError(
                f"Packages directory disappeared during reload: "
                f"{self._packages_path}. Re-select the game path."
            )
        prev_groups = len(self._pamt_cache)
        self._papgt_data = None
        self._pamt_cache.clear()
        self._root = VfsNode(name="root", is_dir=True)
        self._tree_dirty_groups.clear()
        self._logged_processing_warnings.clear()
        # Drop downstream caches that were keyed on this VFS.
        try:
            from core.mesh_texture_service import invalidate_pamt_index_cache
            invalidate_pamt_index_cache(self)
        except Exception:
            pass
        logger.info(
            "VFS reload: cleared %d cached PAMT group(s); "
            "caches will re-populate on next access.",
            prev_groups,
        )

    def extract_entry(
        self,
        entry: PamtFileEntry,
        output_dir: str,
    ) -> dict:
        """Extract a single file entry from a PAZ archive.

        Automatically handles decryption and decompression based on
        the entry's flags and file extension.

        Args:
            entry: PAMT file entry to extract.
            output_dir: Base directory for extracted files.

        Returns:
            Dict with extraction info: path, size, decrypted, decompressed.
        """
        result = {"decrypted": False, "decompressed": False}

        read_size = entry.comp_size if entry.compressed else entry.orig_size

        with open(entry.paz_file, "rb") as f:
            f.seek(entry.offset)
            data = f.read(read_size)

        if len(data) != read_size:
            raise IOError(
                f"Short read for {entry.path}: expected {read_size} bytes "
                f"at offset 0x{entry.offset:08X} in {entry.paz_file}, "
                f"got {len(data)} bytes."
            )

        if entry.encrypted:
            basename = os.path.basename(entry.path)
            data = decrypt(data, basename)
            result["decrypted"] = True

        if entry.compressed and entry.compression_type != 0:
            try:
                data = decompress(data, entry.orig_size, entry.compression_type)
                result["decompressed"] = True
            except (ValueError, Exception) as e:
                self._log_processing_warning(entry, e, extracting=True)

        rel_path = entry.path.replace("\\", "/").replace("/", os.sep)
        out_path = os.path.join(output_dir, rel_path)
        os.makedirs(os.path.dirname(out_path), exist_ok=True)

        with open(out_path, "wb") as f:
            f.write(data)

        result["size"] = len(data)
        result["path"] = out_path
        return result

    def read_entry_data(self, entry: PamtFileEntry) -> bytes:
        """Read and process a file entry's data in memory (decrypt + decompress).

        Returns the fully processed file data without writing to disk.
        If decompression fails (modded/corrupt files), returns raw data
        so the app can still browse and preview other files.
        """
        read_size = entry.comp_size if entry.compressed else entry.orig_size

        with open(entry.paz_file, "rb") as f:
            f.seek(entry.offset)
            data = f.read(read_size)

        if entry.encrypted:
            basename = os.path.basename(entry.path)
            data = decrypt(data, basename)

        if entry.compressed and entry.compression_type != 0:
            try:
                data = decompress(data, entry.orig_size, entry.compression_type)
            except (ValueError, Exception) as e:
                self._log_processing_warning(entry, e, extracting=False)
                # Return raw decrypted data so the app doesn't crash
                # The file will show as corrupt in preview but won't block browsing

        return data

    def _log_processing_warning(self, entry: PamtFileEntry, error: Exception, *, extracting: bool) -> None:
        """Log decompression issues once per file/reason with more accurate wording."""
        message = str(error)
        is_unsupported_type1_dds = (
            entry.path.lower().endswith(".dds")
            and entry.compression_type == 1
            and "Unsupported type-1 payload layout" in message
        )

        issue_key = (entry.path.lower(), "type1-dds" if is_unsupported_type1_dds else message)
        if issue_key in self._logged_processing_warnings:
            return
        self._logged_processing_warnings.add(issue_key)

        if is_unsupported_type1_dds:
            logger.info(
                "DDS preview limitation for %s: %s",
                entry.path,
                message,
            )
            return

        if extracting:
            logger.warning(
                "Decompression failed for %s (extracting raw data instead): %s",
                entry.path,
                message,
            )
        else:
            logger.warning(
                "Decompression failed for %s: %s",
                entry.path,
                message,
            )

    def get_tree(self) -> VfsNode:
        """Get the VFS tree root.

        The trie is built lazily — entries from each PAMT are inserted
        only on first access, not eagerly during ``load_pamt``. This
        keeps cold-load fast (the trie costs ~5 s to populate across
        all 34 groups) while preserving the contract that callers can
        still walk a complete ``_root`` whenever they need it.
        """
        if self._tree_dirty_groups:
            for group_dir in self._tree_dirty_groups:
                pamt = self._pamt_cache.get(group_dir)
                if not pamt:
                    continue
                for entry in pamt.file_entries:
                    self._add_to_tree(entry, group_dir)
            self._tree_dirty_groups.clear()
        return self._root

    def _add_to_tree(self, entry: PamtFileEntry, group_dir: str) -> None:
        """Add a file entry to the VFS tree."""
        parts = entry.path.replace("\\", "/").split("/")
        current = self._root

        for i, part in enumerate(parts):
            if not part:
                continue
            if i == len(parts) - 1:
                current.children[part] = VfsNode(
                    name=part,
                    is_dir=False,
                    entry=entry,
                    package_group=group_dir,
                )
            else:
                if part not in current.children:
                    current.children[part] = VfsNode(name=part, is_dir=True)
                current = current.children[part]

    @property
    def packages_path(self) -> str:
        return str(self._packages_path)

    @property
    def papgt_path(self) -> str:
        return str(self._packages_path / "meta" / "0.papgt")
