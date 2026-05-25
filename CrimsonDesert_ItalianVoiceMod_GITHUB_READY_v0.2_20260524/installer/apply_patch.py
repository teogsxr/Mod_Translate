#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import struct
import sys
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path

PA_MAGIC = 0x2145E233
MASK = 0xFFFFFFFF


def rol(x: int, k: int) -> int:
    return ((x << k) | (x >> (32 - k))) & MASK


def ror(x: int, k: int) -> int:
    return ((x >> k) | (x << (32 - k))) & MASK


def pa_checksum(data: bytes) -> int:
    length = len(data)
    if length == 0:
        return 0
    a = b = c = (length - PA_MAGIC) & MASK
    full_blocks = length // 12
    tail_start = full_blocks * 12
    if full_blocks:
        words = struct.unpack_from(f"<{full_blocks * 3}I", data, 0)
        wi = 0
        for _ in range(full_blocks):
            a = (a + words[wi]) & MASK
            b = (b + words[wi + 1]) & MASK
            c = (c + words[wi + 2]) & MASK
            wi += 3
            a = (a - c) & MASK; a ^= rol(c, 4);  c = (c + b) & MASK
            b = (b - a) & MASK; b ^= rol(a, 6);  a = (a + c) & MASK
            c = (c - b) & MASK; c ^= rol(b, 8);  b = (b + a) & MASK
            a = (a - c) & MASK; a ^= rol(c, 16); c = (c + b) & MASK
            b = (b - a) & MASK; b ^= rol(a, 19); a = (a + c) & MASK
            c = (c - b) & MASK; c ^= rol(b, 4);  b = (b + a) & MASK
    remaining = length - tail_start
    offset = tail_start
    if remaining >= 12: c = (c + (data[offset + 11] << 24)) & MASK
    if remaining >= 11: c = (c + (data[offset + 10] << 16)) & MASK
    if remaining >= 10: c = (c + (data[offset + 9] << 8)) & MASK
    if remaining >= 9:  c = (c + data[offset + 8]) & MASK
    if remaining >= 8:  b = (b + (data[offset + 7] << 24)) & MASK
    if remaining >= 7:  b = (b + (data[offset + 6] << 16)) & MASK
    if remaining >= 6:  b = (b + (data[offset + 5] << 8)) & MASK
    if remaining >= 5:  b = (b + data[offset + 4]) & MASK
    if remaining >= 4:  a = (a + (data[offset + 3] << 24)) & MASK
    if remaining >= 3:  a = (a + (data[offset + 2] << 16)) & MASK
    if remaining >= 2:  a = (a + (data[offset + 1] << 8)) & MASK
    if remaining >= 1:  a = (a + data[offset]) & MASK
    v82 = ((b ^ c) - rol(b, 14)) & MASK
    v83 = ((a ^ v82) - rol(v82, 11)) & MASK
    v84 = ((v83 ^ b) - ror(v83, 7)) & MASK
    v85 = ((v84 ^ v82) - rol(v84, 16)) & MASK
    t = ((v83 ^ v85) - rol(v85, 4)) & MASK
    v87 = ((t ^ v84) - rol(t, 14)) & MASK
    return ((v87 ^ v85) - ror(v87, 8)) & MASK


@dataclass
class PazTableEntry:
    index: int
    checksum: int
    size: int
    entry_offset: int


@dataclass
class FileEntry:
    path: str
    paz_file: str
    offset: int
    comp_size: int
    orig_size: int
    flags: int
    paz_index: int
    record_offset: int


@dataclass
class PamtData:
    path: str
    raw_data: bytes
    paz_table: list[PazTableEntry]
    file_entries: list[FileEntry]


def parse_pamt(pamt_path: Path, paz_dir: Path) -> PamtData:
    data = pamt_path.read_bytes()
    off = 0
    off += 4
    paz_count = struct.unpack_from("<I", data, off)[0]; off += 4
    off += 8
    paz_table: list[PazTableEntry] = []
    for i in range(paz_count):
        entry_offset = off
        checksum = struct.unpack_from("<I", data, off)[0]; off += 4
        size = struct.unpack_from("<I", data, off)[0]; off += 4
        paz_table.append(PazTableEntry(i, checksum, size, entry_offset))
        if i < paz_count - 1:
            off += 4
    folder_size = struct.unpack_from("<I", data, off)[0]; off += 4
    folder_end = off + folder_size
    folder_prefix = ""
    while off < folder_end:
        parent = struct.unpack_from("<I", data, off)[0]
        slen = data[off + 4]
        name = data[off + 5:off + 5 + slen].decode("utf-8", errors="replace")
        if parent == 0xFFFFFFFF:
            folder_prefix = name
        off += 5 + slen
    node_size = struct.unpack_from("<I", data, off)[0]; off += 4
    node_start = off
    nodes: dict[int, tuple[int, str]] = {}
    while off < node_start + node_size:
        rel = off - node_start
        parent = struct.unpack_from("<I", data, off)[0]
        slen = data[off + 4]
        name = data[off + 5:off + 5 + slen].decode("utf-8", errors="replace")
        nodes[rel] = (parent, name)
        off += 5 + slen

    def build_path(node_ref: int) -> str:
        parts = []
        cur = node_ref
        depth = 0
        while cur != 0xFFFFFFFF and depth < 64:
            if cur not in nodes:
                break
            parent, name = nodes[cur]
            parts.append(name)
            cur = parent
            depth += 1
        return "".join(reversed(parts))

    folder_count = struct.unpack_from("<I", data, off)[0]; off += 4
    off += 4
    off += folder_count * 16
    entries: list[FileEntry] = []
    pamt_stem = int(pamt_path.stem)
    while off + 20 <= len(data):
        record_offset = off
        node_ref, paz_offset, comp_size, orig_size, flags = struct.unpack_from("<IIIII", data, off)
        off += 20
        paz_index = flags & 0xFF
        node_path = build_path(node_ref)
        full_path = f"{folder_prefix}/{node_path}" if folder_prefix else node_path
        paz_file = str(paz_dir / f"{pamt_stem + paz_index}.paz")
        entries.append(FileEntry(full_path, paz_file, paz_offset, comp_size, orig_size, flags, paz_index, record_offset))
    return PamtData(str(pamt_path), data, paz_table, entries)


def atomic_write(path: Path, data: bytes) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def pad16(data: bytes) -> bytes:
    pad = (-len(data)) % 16
    return data if pad == 0 else data + (b"\x00" * pad)


def build_space_map(entries: list[FileEntry]) -> dict[tuple[str, int], int]:
    by_paz: dict[str, list[FileEntry]] = {}
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


def write_entry_payload(entry: FileEntry, payload: bytes, space_map: dict[tuple[str, int], int]) -> int:
    padded = pad16(payload)
    max_space = space_map.get((entry.paz_file, entry.offset), entry.comp_size)
    paz_path = Path(entry.paz_file)
    if len(padded) <= max_space:
        with paz_path.open("r+b") as f:
            f.seek(entry.offset)
            f.write(padded)
        return entry.offset
    paz_size = paz_path.stat().st_size
    aligned = (paz_size + 15) & ~15
    with paz_path.open("r+b") as f:
        f.seek(entry.offset)
        f.write(b"\x00" * entry.comp_size)
        f.seek(paz_size)
        if aligned > paz_size:
            f.write(b"\x00" * (aligned - paz_size))
        f.write(padded)
    return aligned


def parse_papgt(path: Path) -> bytearray:
    data = path.read_bytes()
    if len(data) < 12:
        raise RuntimeError(f"PAPGT troppo piccolo: {path}")
    return bytearray(data)


def get_papgt_crc_offset(game_path: Path, folder_name: str) -> int:
    dirs = [p.name for p in sorted(game_path.iterdir()) if p.is_dir() and (p / "0.pamt").is_file()]
    if folder_name not in dirs:
        raise RuntimeError(f"Cartella pacchetto {folder_name} non trovata in {game_path}")
    index = dirs.index(folder_name)
    return 12 + index * 12 + 8


def verify_pamt(path: Path) -> tuple[bool, int, int]:
    data = path.read_bytes()
    stored = struct.unpack_from("<I", data, 0)[0]
    computed = pa_checksum(data[12:])
    return stored == computed, stored, computed


def verify_papgt(path: Path) -> tuple[bool, int, int]:
    data = path.read_bytes()
    stored = struct.unpack_from("<I", data, 4)[0]
    computed = pa_checksum(data[12:])
    return stored == computed, stored, computed


def backup_files(game_path: Path, files: list[Path]) -> Path:
    stamp = time.strftime("%Y%m%d_%H%M%S")
    backup_dir = game_path / "crimson_desert_it_voice_backup" / stamp
    backup_dir.mkdir(parents=True, exist_ok=True)
    for src in files:
        rel = src.relative_to(game_path)
        dst = backup_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    return backup_dir


def main() -> int:
    parser = argparse.ArgumentParser(description="Applica la mod audio italiana di Crimson Desert.")
    parser.add_argument("--game-path", default=r"C:\Program Files (x86)\Steam\steamapps\common\Crimson Desert")
    parser.add_argument("--package-dir", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--no-backup", action="store_true")
    parser.add_argument("--skip-hash", action="store_true")
    args = parser.parse_args()

    game_path = Path(args.game_path).resolve()
    package_dir = Path(args.package_dir).resolve()
    manifest_path = package_dir / "data" / "manifest.json"
    payload_path = package_dir / "data" / "wem_replacements_0006.zip"
    payload_dir = package_dir / "data" / "wem_replacements_0006"
    pamt_path = game_path / "0006" / "0.pamt"
    papgt_path = game_path / "meta" / "0.papgt"

    if not manifest_path.is_file():
        raise RuntimeError(f"Manifest non trovato: {manifest_path}")
    if not payload_path.is_file() and not payload_dir.is_dir():
        raise RuntimeError(f"Payload audio non trovato: {payload_path} o {payload_dir}")
    if not pamt_path.is_file() or not papgt_path.is_file():
        raise RuntimeError(f"Percorso gioco non valido: {game_path}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = manifest["entries"]
    print(f"Gioco: {game_path}")
    print(f"Audio da applicare: {len(entries)}")

    pamt = parse_pamt(pamt_path, game_path / "0006")
    entry_by_path = {e.path.replace("\\", "/").lower(): e for e in pamt.file_entries}
    missing = [m["path"] for m in entries if m["path"].replace("\\", "/").lower() not in entry_by_path]
    if missing:
        print("ERRORE: alcuni file non esistono in questa versione del gioco.")
        for item in missing[:25]:
            print("  " + item)
        if len(missing) > 25:
            print(f"  ... altri {len(missing) - 25}")
        return 2

    paz_paths = sorted({Path(entry_by_path[m["path"].lower()].paz_file) for m in entries})
    backup_dir = None
    if not args.no_backup:
        print("Creo backup di 0006 e meta/0.papgt...")
        backup_dir = backup_files(game_path, [papgt_path, pamt_path, *paz_paths])
        print(f"Backup: {backup_dir}")

    pamt_raw = bytearray(pamt.raw_data)
    space_map = build_space_map(pamt.file_entries)
    modified_paz: set[Path] = set()

    zf = zipfile.ZipFile(payload_path, "r") if payload_path.is_file() else None
    try:
        for idx, item in enumerate(entries, start=1):
            rel_path = item["path"].replace("\\", "/")
            if zf is not None:
                data = zf.read(rel_path)
            else:
                source = payload_dir / Path(rel_path)
                if not source.is_file():
                    raise RuntimeError(f"Payload audio mancante: {source}")
                data = source.read_bytes()
            if not args.skip_hash:
                digest = hashlib.sha256(data).hexdigest()
                if digest != item["sha256"]:
                    raise RuntimeError(f"SHA256 diverso per {rel_path}")
            entry = entry_by_path[rel_path.lower()]
            new_offset = write_entry_payload(entry, data, space_map)
            struct.pack_into("<I", pamt_raw, entry.record_offset + 4, new_offset)
            struct.pack_into("<I", pamt_raw, entry.record_offset + 8, len(data))
            struct.pack_into("<I", pamt_raw, entry.record_offset + 12, int(item.get("orig_size", len(data))))
            entry.offset = new_offset
            entry.comp_size = len(data)
            entry.orig_size = int(item.get("orig_size", len(data)))
            modified_paz.add(Path(entry.paz_file))
            if idx == 1 or idx % 500 == 0 or idx == len(entries):
                print(f"  {idx}/{len(entries)}")
    finally:
        if zf is not None:
            zf.close()

    print("Ricalcolo checksum PAZ...")
    for paz_path in sorted(modified_paz):
        data = paz_path.read_bytes()
        crc = pa_checksum(data)
        size = len(data)
        paz_index = int(paz_path.stem) - int(pamt_path.stem)
        table = next((x for x in pamt.paz_table if x.index == paz_index), None)
        if table is None:
            raise RuntimeError(f"PAZ index {paz_index} non trovato in PAMT")
        struct.pack_into("<I", pamt_raw, table.entry_offset, crc)
        struct.pack_into("<I", pamt_raw, table.entry_offset + 4, size)
        print(f"  {paz_path.name}: size={size} crc=0x{crc:08X}")

    pamt_crc = pa_checksum(bytes(pamt_raw[12:]))
    struct.pack_into("<I", pamt_raw, 0, pamt_crc)
    atomic_write(pamt_path, bytes(pamt_raw))
    print(f"PAMT aggiornato: crc=0x{pamt_crc:08X}")

    papgt_raw = parse_papgt(papgt_path)
    crc_offset = get_papgt_crc_offset(game_path, "0006")
    struct.pack_into("<I", papgt_raw, crc_offset, pamt_crc)
    papgt_crc = pa_checksum(bytes(papgt_raw[12:]))
    struct.pack_into("<I", papgt_raw, 4, papgt_crc)
    atomic_write(papgt_path, bytes(papgt_raw))
    print(f"PAPGT aggiornato: crc=0x{papgt_crc:08X}")

    ok_pamt, stored_pamt, computed_pamt = verify_pamt(pamt_path)
    ok_papgt, stored_papgt, computed_papgt = verify_papgt(papgt_path)
    if not ok_pamt or not ok_papgt:
        raise RuntimeError(
            "Verifica checksum fallita: "
            f"PAMT stored=0x{stored_pamt:08X} computed=0x{computed_pamt:08X}; "
            f"PAPGT stored=0x{stored_papgt:08X} computed=0x{computed_papgt:08X}"
        )

    print("")
    print("Installazione completata.")
    if backup_dir:
        print(f"Backup conservato in: {backup_dir}")
    print("Avvia il gioco e seleziona la lingua voce inglese/0006 se necessario.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print("")
        print("ERRORE:", exc)
        raise SystemExit(1)
