"""Shared services for patching game archives safely."""

import os
from dataclasses import dataclass, field
from typing import Optional

from core.backup_manager import BackupManager
from core.checksum_engine import checksum_file, verify_pamt_checksum, verify_papgt_checksum
from core.compression_engine import compress
from core.crypto_engine import encrypt
from core.paloc_parser import PalocEntry, parse_paloc, splice_values_in_raw
from core.pamt_parser import (
    PamtData,
    PamtFileEntry,
    find_file_entry,
    parse_pamt,
    update_pamt_file_entry,
    update_pamt_paz_entry,
    update_pamt_self_crc,
)
from core.papgt_manager import (
    get_pamt_crc_offset,
    parse_papgt,
    update_papgt_pamt_crc,
    update_papgt_self_crc,
)
from core.paz_write_utils import build_space_map, write_entry_payload
from core.repack_engine import ModifiedFile, RepackEngine, RepackResult
from core.vfs_manager import VfsManager
from utils.platform_utils import atomic_write, get_file_timestamps, set_file_timestamps


@dataclass
class TranslationGamePatchResult:
    """Result of a translation patch operation."""

    success: bool
    message: str
    paz_crc: int = 0
    pamt_crc: int = 0
    papgt_crc: int = 0
    backup_dir: str = ""
    errors: list[str] = field(default_factory=list)


def _find_group_entry(packages_path: str, group: str, filename: str) -> tuple[str, str, PamtData, PamtFileEntry]:
    group_dir = os.path.join(packages_path, group)
    pamt_path = os.path.join(group_dir, "0.pamt")
    if not os.path.isfile(pamt_path):
        raise FileNotFoundError(f"PAMT index not found: {pamt_path}")

    pamt_data = parse_pamt(pamt_path, paz_dir=group_dir)
    entry = None
    filename_lower = filename.lower()
    for candidate in pamt_data.file_entries:
        if candidate.path.lower().endswith(filename_lower):
            entry = candidate
            break
    if not entry:
        raise FileNotFoundError(f"Cannot find {filename} in package group {group}")

    return group_dir, pamt_path, pamt_data, entry


def collect_translation_target_files(packages_path: str, group: str, filename: str) -> set[str]:
    """Collect package-relative files touched by a translation patch."""
    _, _, _, entry = _find_group_entry(packages_path, group, filename)
    touched = {
        os.path.relpath(entry.paz_file, packages_path),
        os.path.join(group, "0.pamt"),
        os.path.join("meta", "0.papgt"),
    }
    return touched


def collect_relative_repack_targets(packages_path: str, relative_paths: list[str]) -> set[str]:
    """Collect package-relative files touched by exact-path replacements."""
    resolved = _resolve_modified_files(packages_path, relative_paths)
    touched = {os.path.join("meta", "0.papgt")}
    for modified in resolved:
        touched.add(os.path.join(modified.package_group, "0.pamt"))
        touched.add(os.path.relpath(modified.entry.paz_file, packages_path))
    return touched


def _resolve_modified_files(packages_path: str, relative_paths: list[str]) -> list[ModifiedFile]:
    entries_by_relpath = {path.replace("\\", "/").lower(): path for path in relative_paths}
    resolved: list[ModifiedFile] = []

    for item in sorted(os.listdir(packages_path)):
        group_dir = os.path.join(packages_path, item)
        pamt_path = os.path.join(group_dir, "0.pamt")
        if not os.path.isfile(pamt_path):
            continue

        pamt_data = parse_pamt(pamt_path, paz_dir=group_dir)
        for rel_lower, original_rel in list(entries_by_relpath.items()):
            entry = find_file_entry(pamt_data, rel_lower)
            if entry and entry.path.replace("\\", "/").lower() == rel_lower:
                resolved.append(ModifiedFile(
                    data=b"",
                    entry=entry,
                    pamt_data=pamt_data,
                    package_group=item,
                ))
                del entries_by_relpath[rel_lower]

    if entries_by_relpath:
        missing = ", ".join(sorted(entries_by_relpath.values()))
        raise FileNotFoundError(f"Could not locate target files in the game archives: {missing}")

    return resolved


def repack_relative_files(
    packages_path: str,
    replacements: dict[str, bytes],
    create_backup: bool = True,
    backup_dir: str = "",
    preserve_timestamps: bool = True,
    verify_after: bool = True,
    progress_callback=None,
) -> RepackResult:
    """Repack files addressed by exact game-relative paths."""
    relative_paths = list(replacements.keys())
    modified_files = _resolve_modified_files(packages_path, relative_paths)
    replacements_by_lower = {
        key.replace("\\", "/").lower(): value
        for key, value in replacements.items()
    }
    for modified in modified_files:
        relpath = modified.entry.path.replace("\\", "/").lower()
        modified.data = replacements_by_lower[relpath]

    engine = RepackEngine(packages_path, backup_dir=backup_dir)
    papgt_path = os.path.join(packages_path, "meta", "0.papgt")
    return engine.repack(
        modified_files=modified_files,
        papgt_path=papgt_path,
        create_backup=create_backup,
        verify_after=verify_after,
        preserve_timestamps=preserve_timestamps,
        progress_callback=progress_callback,
    )


def patch_translation_to_game(
    packages_path: str,
    group: str,
    filename: str,
    replacements_by_key: dict[str, str],
    create_backup: bool = True,
    backup_dir: str = "",
    progress_callback=None,
) -> TranslationGamePatchResult:
    """Patch translated paloc values into the live game archives."""
    result = TranslationGamePatchResult(success=False, message="")
    total_steps = 9

    def step(n: int, message: str) -> None:
        if progress_callback:
            progress_callback(n, total_steps, message)

    try:
        group_dir, pamt_path, fresh_pamt, entry = _find_group_entry(packages_path, group, filename)
        papgt_path = os.path.join(packages_path, "meta", "0.papgt")

        step(1, "Splicing translations into original paloc data...")
        vfs = VfsManager(packages_path)
        original_raw = vfs.read_entry_data(entry)
        original_entries = parse_paloc(original_raw)
        orig_by_key = {paloc_entry.key: paloc_entry for paloc_entry in original_entries}

        replacements: list[tuple[PalocEntry, str]] = []
        for key, translated_text in replacements_by_key.items():
            if not translated_text:
                continue
            orig_entry = orig_by_key.get(key)
            if not orig_entry:
                continue
            if translated_text != orig_entry.value:
                replacements.append((orig_entry, translated_text))

        paloc_raw = splice_values_in_raw(original_raw, replacements) if replacements else original_raw

        step(2, "Compressing with LZ4...")
        if entry.compression_type == 2:
            compressed = compress(paloc_raw, 2)
        else:
            compressed = paloc_raw

        step(3, "Encrypting with ChaCha20...")
        basename = os.path.basename(entry.path)
        if entry.encrypted:
            encrypted = encrypt(compressed, basename)
        else:
            encrypted = compressed

        new_comp_size = len(encrypted)
        new_orig_size = len(paloc_raw)

        step(4, "Creating backup...")
        if create_backup:
            backup_root = backup_dir or os.path.join(packages_path, "..", "crimsonforge_backups")
            bm = BackupManager(backup_root)
            backup_record = bm.create_backup(
                [entry.paz_file, pamt_path, papgt_path],
                description=f"Translation patch: {filename}",
            )
            result.backup_dir = backup_record.backup_dir

        step(5, "Writing to PAZ archive...")
        space_map = build_space_map(fresh_pamt.file_entries)
        new_offset, _ = write_entry_payload(entry, encrypted, space_map)

        step(6, "Computing PAZ checksum...")
        new_paz_crc = checksum_file(entry.paz_file)
        new_paz_size = os.path.getsize(entry.paz_file)
        result.paz_crc = new_paz_crc

        step(7, "Updating PAMT index...")
        pamt_data = parse_pamt(pamt_path, paz_dir=group_dir)
        pamt_raw = bytearray(pamt_data.raw_data)
        for table_entry in pamt_data.paz_table:
            if table_entry.index == entry.paz_index:
                update_pamt_paz_entry(pamt_raw, table_entry, new_paz_crc, new_paz_size)
                break

        for file_entry in pamt_data.file_entries:
            if file_entry.record_offset == entry.record_offset:
                update_pamt_file_entry(
                    pamt_raw,
                    file_entry,
                    new_comp_size,
                    new_orig_size,
                    new_offset=new_offset,
                )
                break

        new_pamt_crc = update_pamt_self_crc(pamt_raw)
        result.pamt_crc = new_pamt_crc

        ts_pamt = get_file_timestamps(pamt_path)
        atomic_write(pamt_path, bytes(pamt_raw))
        set_file_timestamps(pamt_path, ts_pamt["modified"], ts_pamt["accessed"])

        step(8, "Updating PAPGT root index...")
        papgt_data = parse_papgt(papgt_path)
        papgt_raw = bytearray(papgt_data.raw_data)
        folder_number = int(group)
        pamt_crc_offset = get_pamt_crc_offset(papgt_data, folder_number)
        update_papgt_pamt_crc(papgt_raw, pamt_crc_offset, new_pamt_crc)
        new_papgt_crc = update_papgt_self_crc(papgt_raw)
        result.papgt_crc = new_papgt_crc

        ts_papgt = get_file_timestamps(papgt_path)
        atomic_write(papgt_path, bytes(papgt_raw))
        set_file_timestamps(papgt_path, ts_papgt["modified"], ts_papgt["accessed"])

        step(9, "Verifying checksums...")
        ok_papgt, _, _ = verify_papgt_checksum(papgt_path)
        ok_pamt, _, _ = verify_pamt_checksum(pamt_path)
        if not ok_papgt:
            raise RuntimeError("PAPGT checksum verification failed after patch.")
        if not ok_pamt:
            raise RuntimeError("PAMT checksum verification failed after patch.")

        result.success = True
        result.message = f"Patched {len(replacements)} translated strings into {filename}"
        return result

    except Exception as exc:
        result.errors.append(str(exc))
        result.message = str(exc)
        return result
