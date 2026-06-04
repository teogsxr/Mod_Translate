"""Build distributable mesh-mod packages from edited OBJ files.

This module supports two Explorer export styles:

1. Standalone installer ZIP
   - Rebuilds the target PAC/PAM/PAMLOD binaries
   - Patches the affected PAZ/PAMT/PAPGT files fully in memory
   - Writes a ZIP with install/uninstall scripts

2. Mod-manager ZIP
   - Rebuilds the target PAC/PAM/PAMLOD binaries
   - Ships loose rebuilt mesh files under ``files/``
   - Adds ``manifest.json`` metadata for loose-file aware managers

The standalone output is bigger but can be installed by end users
without tools. The mod-manager output is much smaller and is intended
for import into CDUMM, Crimson Browser, and similar archive builders.
"""

from __future__ import annotations

import json
import os
import struct
import zipfile
from dataclasses import dataclass, asdict
from datetime import datetime, UTC
from typing import Callable, Optional

from core.checksum_engine import pa_checksum
from core.compression_engine import compress
from core.crypto_engine import encrypt
from core.mesh_importer import build_mesh, import_obj, transfer_pam_edit_to_pamlod_mesh
from core.mesh_parser import is_mesh_file
from core.pamt_parser import (
    PamtData,
    PamtFileEntry,
    find_file_entry,
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
from core.vfs_manager import VfsManager
from utils.logger import get_logger

logger = get_logger("core.mesh_ship_builder")


@dataclass
class MeshShipRequest:
    """One mesh file to rebuild and ship."""

    entry: PamtFileEntry
    package_group: str
    obj_path: str


@dataclass
class MeshShipAsset:
    """Manifest entry describing one rebuilt asset."""

    entry_path: str
    package_group: str
    format: str
    obj_path: str
    vertices: int
    faces: int
    submeshes: int
    generated_from: str = ""
    note: str = ""


@dataclass
class BuiltMeshShipPackage:
    """Patched archive files plus manifest metadata."""

    patched_files: dict[str, bytes]
    manifest: dict


@dataclass
class BuiltMeshManagerPackage:
    """Loose rebuilt mesh files plus metadata for mod-manager ZIPs."""

    loose_files: dict[str, bytes]
    manifest: dict
    modinfo: dict


def default_mesh_ship_mod_name(entries: list[PamtFileEntry]) -> str:
    """Return a friendly default mod name for selected mesh entries."""
    if not entries:
        return "Crimson Desert Mesh Mod"
    if len(entries) == 1:
        stem = os.path.splitext(os.path.basename(entries[0].path))[0]
        return f"Crimson Desert - {stem} Mesh Mod"
    return f"Crimson Desert - Mesh Mod Pack ({len(entries)} assets)"


def build_mesh_ship_package(
    vfs: VfsManager,
    requests: list[MeshShipRequest],
    mod_name: str,
    author: str,
    version: str,
    include_paired_lod: bool = True,
    progress_callback: Optional[Callable[[int, str], None]] = None,
) -> BuiltMeshShipPackage:
    """Build patched game files for a mesh-mod distribution ZIP."""
    modified_by_path, manifest_assets = _prepare_mesh_assets(
        vfs,
        requests,
        include_paired_lod=include_paired_lod,
        progress_callback=progress_callback,
    )

    current_step = max(1, len(modified_by_path) + 4)

    def report(message: str, step: int) -> None:
        pct = min(100, int((step / current_step) * 100))
        logger.info("[%d%%] %s", pct, message)
        if progress_callback:
            progress_callback(pct, message)

    report("Building patched archive files...", len(modified_by_path))

    patched_files = _build_patched_archive_files(
        vfs,
        modified_by_path,
        progress_callback,
        len(modified_by_path),
        current_step,
    )

    manifest = {
        "schema_version": 1,
        "kind": "mesh_ship_package",
        "mod_name": mod_name,
        "author": author,
        "version": version,
        "created_utc": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "include_paired_lod": include_paired_lod,
        "asset_count": len(manifest_assets),
        "archive_file_count": len(patched_files),
        "assets": [asdict(asset) for asset in manifest_assets],
    }
    return BuiltMeshShipPackage(patched_files=patched_files, manifest=manifest)


def build_mesh_manager_package(
    vfs: VfsManager,
    requests: list[MeshShipRequest],
    mod_name: str,
    author: str,
    version: str,
    include_paired_lod: bool = True,
    progress_callback: Optional[Callable[[int, str], None]] = None,
) -> BuiltMeshManagerPackage:
    """Build a small loose-file ZIP for mod-manager workflows."""
    modified_by_path, manifest_assets = _prepare_mesh_assets(
        vfs,
        requests,
        include_paired_lod=include_paired_lod,
        progress_callback=progress_callback,
    )

    loose_files = {
        item["entry"].path: item["new_data"]
        for _, item in sorted(modified_by_path.items(), key=lambda pair: pair[0])
    }
    game_build = _detect_game_build(vfs.packages_path)
    created_utc = datetime.now(UTC).replace(microsecond=0).isoformat()
    manifest = {
        "format": "v1",
        "schema_version": 1,
        "kind": "mesh_loose_mod",
        "game": "Crimson Desert",
        "title": mod_name,
        "name": mod_name,
        "mod_name": mod_name,
        "author": author,
        "version": version,
        "created_utc": created_utc,
        "generator": "CrimsonForge",
        "generator_url": "https://github.com/hzeemr/crimsonforge",
        "game_build": game_build,
        "include_paired_lod": include_paired_lod,
        "asset_count": len(manifest_assets),
        "file_count": len(loose_files),
        "files_root": "files",
        "assets": [asdict(asset) for asset in manifest_assets],
        "files": [
            {
                "path": asset.entry_path,
                "package_group": asset.package_group,
                "format": asset.format,
                "generated_from": asset.generated_from,
                "note": asset.note,
            }
            for asset in manifest_assets
        ],
    }
    modinfo = {
        "title": mod_name,
        "name": mod_name,
        "author": author,
        "version": version,
        "game": "Crimson Desert",
        "format": "v1",
        "type": "mesh_loose_mod",
        "description": (
            f"Loose mesh mod generated by CrimsonForge for {len(manifest_assets)} asset(s). "
            "Import into CDUMM, Crimson Browser, or another loose-file aware Crimson Desert mod manager."
        ),
        "generator": "CrimsonForge",
        "generator_url": "https://github.com/hzeemr/crimsonforge",
        "game_build": game_build,
        "created_utc": created_utc,
    }
    return BuiltMeshManagerPackage(
        loose_files=loose_files,
        manifest=manifest,
        modinfo=modinfo,
    )


def _prepare_mesh_assets(
    vfs: VfsManager,
    requests: list[MeshShipRequest],
    include_paired_lod: bool = True,
    progress_callback: Optional[Callable[[int, str], None]] = None,
) -> tuple[dict[str, dict], list[MeshShipAsset]]:
    """Rebuild requested meshes and return in-memory entry payloads."""
    if not requests:
        raise ValueError("Select at least one mesh asset to ship.")

    normalized_requests: list[MeshShipRequest] = []
    seen_paths: set[str] = set()
    for req in requests:
        entry_path = req.entry.path.lower()
        if entry_path in seen_paths:
            raise ValueError(f"Duplicate mesh selected: {req.entry.path}")
        if not is_mesh_file(req.entry.path):
            raise ValueError(
                f"{req.entry.path} is not a supported editable mesh. "
                "Only .pac, .pam, and .pamlod files can be shipped."
            )
        if not req.obj_path or not os.path.isfile(req.obj_path):
            raise ValueError(f"Edited OBJ not found for {req.entry.path}")
        seen_paths.add(entry_path)
        normalized_requests.append(req)

    total_steps = max(1, len(normalized_requests) * 3 + 2)
    current_step = 0

    def report(message: str) -> None:
        pct = min(100, int((current_step / total_steps) * 100))
        logger.info("[%d%%] %s", pct, message)
        if progress_callback:
            progress_callback(pct, message)

    explicit_paths = {req.entry.path.lower() for req in normalized_requests}
    modified_by_path: dict[str, dict] = {}
    manifest_assets: list[MeshShipAsset] = []

    for req in normalized_requests:
        current_step += 1
        ext = os.path.splitext(req.obj_path)[1].lower()
        fmt_label = "FBX" if ext == ".fbx" else "OBJ"
        report(f"Rebuilding {os.path.basename(req.entry.path)} from {fmt_label}...")

        original_data = vfs.read_entry_data(req.entry)
        if ext == ".fbx":
            from core.mesh_importer import import_fbx
            imported = import_fbx(req.obj_path)
        else:
            imported = import_obj(req.obj_path)
        imported.path = req.entry.path
        imported.format = _entry_format(req.entry)
        rebuilt_data = build_mesh(imported, original_data)

        pamt_data = vfs.load_pamt(req.package_group)
        modified_by_path[req.entry.path.lower()] = {
            "entry": req.entry,
            "package_group": req.package_group,
            "pamt_data": pamt_data,
            "new_data": rebuilt_data,
        }
        manifest_assets.append(
            MeshShipAsset(
                entry_path=req.entry.path,
                package_group=req.package_group,
                format=imported.format,
                obj_path=req.obj_path,
                vertices=imported.total_vertices,
                faces=imported.total_faces,
                submeshes=len(imported.submeshes),
            )
        )

        if imported.format != "pam" or not include_paired_lod:
            continue

        paired_path = req.entry.path[:-4] + ".pamlod"
        if paired_path.lower() in explicit_paths or paired_path.lower() in modified_by_path:
            continue

        paired_entry = find_file_entry(pamt_data, paired_path)
        if not paired_entry:
            continue

        current_step += 1
        report(f"Generating paired LOD for {os.path.basename(req.entry.path)}...")

        paired_original = vfs.read_entry_data(paired_entry)
        paired_mesh = transfer_pam_edit_to_pamlod_mesh(
            imported,
            original_data,
            paired_original,
            paired_entry.path,
        )
        paired_data = build_mesh(paired_mesh, paired_original)
        modified_by_path[paired_entry.path.lower()] = {
            "entry": paired_entry,
            "package_group": req.package_group,
            "pamt_data": pamt_data,
            "new_data": paired_data,
        }
        manifest_assets.append(
            MeshShipAsset(
                entry_path=paired_entry.path,
                package_group=req.package_group,
                format="pamlod",
                obj_path=req.obj_path,
                vertices=paired_mesh.total_vertices,
                faces=paired_mesh.total_faces,
                submeshes=len(paired_mesh.submeshes) if paired_mesh.submeshes else 0,
                generated_from=req.entry.path,
                note="Auto-generated paired LOD from edited PAM mesh.",
            )
        )
    return modified_by_path, manifest_assets


def write_mesh_ship_zip(
    output_path: str,
    package: BuiltMeshShipPackage,
    mod_name: str,
    author: str,
    version: str,
) -> None:
    """Write a mesh distribution ZIP from a built package."""
    install_bat = _bat_install(mod_name, author, version, list(package.patched_files.keys()))
    uninstall_bat = _bat_uninstall(mod_name)
    readme = _readme(mod_name, author, version, package.manifest)

    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for rel_path, data in sorted(package.patched_files.items()):
            zf.writestr(f"data/{rel_path}", data)
        zf.writestr("install.bat", install_bat)
        zf.writestr("uninstall.bat", uninstall_bat)
        zf.writestr("README.txt", readme)
        zf.writestr("manifest.json", json.dumps(package.manifest, indent=2, ensure_ascii=False))


def write_mesh_manager_zip(
    output_path: str,
    package: BuiltMeshManagerPackage,
    mod_name: str,
    author: str,
    version: str,
) -> None:
    """Write a small manager-friendly ZIP with loose rebuilt mesh files."""
    readme = _readme_manager(mod_name, author, version, package.manifest)

    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for rel_path, data in sorted(package.loose_files.items()):
            zf.writestr(f"files/{rel_path}", data)
        zf.writestr("manifest.json", json.dumps(package.manifest, indent=2, ensure_ascii=False))
        zf.writestr("modinfo.json", json.dumps(package.modinfo, indent=2, ensure_ascii=False))
        zf.writestr("README.txt", readme)


def _build_patched_archive_files(
    vfs: VfsManager,
    modified_by_path: dict[str, dict],
    progress_callback: Optional[Callable[[int, str], None]],
    current_step: int,
    total_steps: int,
) -> dict[str, bytes]:
    """Patch PAZ/PAMT/PAPGT files fully in memory and return changed files."""

    def report(message: str, step_offset: int = 0) -> None:
        pct = min(100, int(((current_step + step_offset) / total_steps) * 100))
        logger.info("[%d%%] %s", pct, message)
        if progress_callback:
            progress_callback(pct, message)

    grouped: dict[str, list[dict]] = {}
    for item in modified_by_path.values():
        grouped.setdefault(item["package_group"], []).append(item)

    game_path = vfs.packages_path
    papgt_path = os.path.join(game_path, "meta", "0.papgt")
    papgt_data = parse_papgt(papgt_path)
    papgt_raw = bytearray(papgt_data.raw_data)
    patched_files: dict[str, bytes] = {}

    for group_idx, group_key in enumerate(sorted(grouped.keys())):
        report(f"Patching archive group {group_key}...", group_idx)

        group_items = grouped[group_key]
        pamt_data: PamtData = group_items[0]["pamt_data"]
        pamt_raw = bytearray(pamt_data.raw_data)
        paz_buffers: dict[str, bytearray] = {}

        for item in group_items:
            entry: PamtFileEntry = item["entry"]
            new_data: bytes = item["new_data"]
            processed = new_data

            if entry.compressed and entry.compression_type != 0:
                processed = compress(processed, entry.compression_type)
            if entry.encrypted:
                processed = encrypt(processed, os.path.basename(entry.path))

            paz_path = entry.paz_file
            if paz_path not in paz_buffers:
                with open(paz_path, "rb") as f:
                    paz_buffers[paz_path] = bytearray(f.read())

            paz_data = paz_buffers[paz_path]
            aligned = (len(paz_data) + 15) & ~15
            if aligned > len(paz_data):
                paz_data.extend(b"\x00" * (aligned - len(paz_data)))
            new_offset = len(paz_data)
            paz_data.extend(processed)

            update_pamt_file_entry(
                pamt_raw,
                entry,
                new_comp_size=len(processed),
                new_orig_size=len(new_data),
                new_offset=new_offset,
            )

        for paz_path, paz_data in paz_buffers.items():
            new_crc = pa_checksum(bytes(paz_data))
            new_size = len(paz_data)
            paz_index = _resolve_paz_index(pamt_data, paz_path)
            table_entry = next((row for row in pamt_data.paz_table if row.index == paz_index), None)
            if table_entry is None:
                raise ValueError(f"Could not resolve PAZ table entry for {paz_path}")
            update_pamt_paz_entry(pamt_raw, table_entry, new_crc, new_size)
            patched_files[f"{group_key}/{os.path.basename(paz_path)}"] = bytes(paz_data)

        new_pamt_crc = update_pamt_self_crc(pamt_raw)
        patched_files[f"{group_key}/0.pamt"] = bytes(pamt_raw)

        crc_offset = get_pamt_crc_offset(papgt_data, int(group_key))
        if crc_offset is None:
            raise ValueError(f"PAPGT does not contain a CRC slot for package group {group_key}")
        update_papgt_pamt_crc(papgt_raw, crc_offset, new_pamt_crc)

    report("Updating PAPGT checksum...", len(grouped))
    update_papgt_self_crc(papgt_raw)
    patched_files["meta/0.papgt"] = bytes(papgt_raw)
    return patched_files


def _resolve_paz_index(pamt_data: PamtData, paz_path: str) -> int:
    paz_basename = os.path.basename(paz_path)
    paz_num = int(os.path.splitext(paz_basename)[0])
    pamt_stem = int(os.path.splitext(os.path.basename(pamt_data.path))[0])
    return paz_num - pamt_stem


def _entry_format(entry: PamtFileEntry) -> str:
    ext = os.path.splitext(entry.path.lower())[1]
    if ext == ".pac":
        return "pac"
    if ext == ".pamlod":
        return "pamlod"
    if ext == ".pam":
        return "pam"
    raise ValueError(f"Unsupported mesh format: {entry.path}")


def _bat_install(mod_name: str, author: str, version: str, files: list[str]) -> str:
    lines = [
        "@echo off",
        "setlocal EnableDelayedExpansion",
        "chcp 65001 >nul 2>&1",
        f"title {mod_name} v{version}",
        "echo.",
        f"echo  {mod_name}",
        f"echo  by {author} - v{version}",
        "echo  Installing Crimson Desert mesh mod package...",
        "echo.",
        'set "GP="',
        "for %%D in (",
        '    "C:\\Program Files (x86)\\Steam\\steamapps\\common\\Crimson Desert"',
        '    "C:\\Program Files\\Steam\\steamapps\\common\\Crimson Desert"',
        '    "D:\\SteamLibrary\\steamapps\\common\\Crimson Desert"',
        '    "E:\\SteamLibrary\\steamapps\\common\\Crimson Desert"',
        '    "F:\\SteamLibrary\\steamapps\\common\\Crimson Desert"',
        ') do ( if exist "%%~D\\meta\\0.papgt" ( set "GP=%%~D" & goto :found ) )',
        'for /f "tokens=2*" %%A in (\'reg query "HKCU\\Software\\Valve\\Steam" /v SteamPath 2^>nul\') do set "SP=%%B"',
        'if defined SP if exist "!SP!\\steamapps\\common\\Crimson Desert\\meta\\0.papgt" set "GP=!SP!\\steamapps\\common\\Crimson Desert"',
        'if not defined GP ( echo [ERROR] Game not found. & pause & exit /b 1 )',
        ":found",
        'echo [OK] Game found: !GP!',
        'set "DATA=%~dp0data"',
        "echo.",
    ]
    for rel_path in sorted(set(files)):
        batch_path = rel_path.replace("/", "\\")
        lines.append(f'copy /Y "!DATA!\\{batch_path}" "!GP!\\{batch_path}" >nul && echo   Copied: {batch_path}')
    lines.extend([
        "echo.",
        "echo [DONE] Mesh mod installed successfully.",
        "echo Launch Crimson Desert to test the new meshes.",
        "echo To uninstall: run uninstall.bat",
        "echo.",
        "pause",
    ])
    return "\r\n".join(lines)


def _bat_uninstall(mod_name: str) -> str:
    return "\r\n".join([
        "@echo off",
        "setlocal EnableDelayedExpansion",
        "chcp 65001 >nul 2>&1",
        f"title Uninstall {mod_name}",
        "echo.",
        f"echo  Uninstall {mod_name}",
        "echo  Steam will verify and restore the original game files.",
        "echo.",
        'set /p C="Proceed? (Y/N): "',
        'if /i not "!C!"=="Y" exit /b 0',
        "start steam://validate/3321460",
        "echo [OK] Steam Verify Integrity has been opened.",
        "echo Wait for Steam to finish restoring the original files.",
        "echo.",
        "pause",
    ])


def _readme(mod_name: str, author: str, version: str, manifest: dict) -> str:
    asset_lines = []
    for asset in manifest.get("assets", []):
        extra = ""
        if asset.get("generated_from"):
            extra = f" [generated from {asset['generated_from']}]"
        asset_lines.append(f"  - {asset['entry_path']}{extra}")

    assets_block = "\n".join(asset_lines) if asset_lines else "  - No assets listed"
    return (
        f"{mod_name}\n"
        f"{'=' * len(mod_name)}\n\n"
        f"Author: {author}\n"
        f"Version: {version}\n"
        f"Generated: {manifest.get('created_utc', '')}\n"
        f"Assets: {manifest.get('asset_count', 0)}\n\n"
        "INSTALL\n"
        "  1. Extract this ZIP anywhere on your computer.\n"
        "  2. Run install.bat.\n"
        "  3. Launch Crimson Desert.\n\n"
        "UNINSTALL\n"
        "  Run uninstall.bat to open Steam Verify Integrity.\n\n"
        "PATCHED ASSETS\n"
        f"{assets_block}\n\n"
        "Generated by CrimsonForge\n"
        "https://github.com/hzeemr/crimsonforge\n"
    )


def _readme_manager(mod_name: str, author: str, version: str, manifest: dict) -> str:
    asset_lines = []
    for asset in manifest.get("assets", []):
        extra = ""
        if asset.get("generated_from"):
            extra = f" [generated from {asset['generated_from']}]"
        asset_lines.append(f"  - {asset['entry_path']}{extra}")

    assets_block = "\n".join(asset_lines) if asset_lines else "  - No assets listed"
    return (
        f"{mod_name}\n"
        f"{'=' * len(mod_name)}\n\n"
        f"Author: {author}\n"
        f"Version: {version}\n"
        f"Generated: {manifest.get('created_utc', '')}\n"
        f"Game Build: {manifest.get('game_build', 'unknown')}\n"
        f"Loose Files: {manifest.get('file_count', 0)}\n\n"
        "INSTALL\n"
        "  Import this ZIP into CDUMM, Crimson Browser, or another loose-file aware Crimson Desert mod manager.\n"
        "  The manager should rebuild the affected archives on the target machine.\n\n"
        "PACKAGE CONTENTS\n"
        "  - files/<entry path> rebuilt mesh files\n"
        "  - manifest.json package metadata\n"
        "  - modinfo.json manager-friendly mod metadata\n\n"
        "MESH FILES\n"
        f"{assets_block}\n\n"
        "Generated by CrimsonForge\n"
        "https://github.com/hzeemr/crimsonforge\n"
    )


def _detect_game_build(packages_path: str) -> str:
    """Return a compact build string for manifests when possible."""
    meta_dir = os.path.join(packages_path, "meta")
    version_text = ""
    paver_path = os.path.join(meta_dir, "0.paver")
    if os.path.isfile(paver_path):
        try:
            with open(paver_path, "rb") as f:
                data = f.read(6)
            if len(data) >= 6:
                major, minor, patch = struct.unpack_from("<HHH", data, 0)
                version_text = f"v{major}.{minor:02d}.{patch:02d}"
        except Exception:
            version_text = ""

    papgt_crc = ""
    papgt_path = os.path.join(meta_dir, "0.papgt")
    if os.path.isfile(papgt_path):
        try:
            with open(papgt_path, "rb") as f:
                papgt_crc = f"CRC 0x{pa_checksum(f.read()):08X}"
        except Exception:
            papgt_crc = ""

    if version_text and papgt_crc:
        return f"{version_text} | {papgt_crc}"
    if version_text:
        return version_text
    if papgt_crc:
        return papgt_crc
    return "unknown"
