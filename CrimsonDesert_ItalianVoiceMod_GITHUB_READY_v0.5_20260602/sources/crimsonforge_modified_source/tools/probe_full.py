"""Full no-filter memory snapshot tool for Crimson Desert.

Captures EVERY readable byte in the game's heap range
[0x140000000, 0x800000000] to disk. Two snapshots taken at different
moments (e.g. before vs after boss damage) can then be diffed to
reveal EVERY 4-byte word that changed -- no filtering, no value
heuristics. The HP cell is whichever fp32 decreased after a hit.

Workflow
--------
With game open near the boss but NOT engaged:

    python -X utf8 tools/probe_full.py snapshot 1

Then engage the boss, take some damage, leave it alive:

    python -X utf8 tools/probe_full.py snapshot 2

Diff the two snapshots:

    python -X utf8 tools/probe_full.py diff 1 2

Output:
    tools/dumps/snap_<N>/regions.csv     -- list of regions captured
    tools/dumps/snap_<N>/<base>.bin.gz   -- raw bytes of each region
    tools/dumps/diff_<A>_<B>.csv         -- every 4-byte word that differs
    tools/dumps/diff_<A>_<B>_summary.txt -- counts + obvious HP candidates

Disk usage: ~1-2 GB per snapshot (compressed).

The diff is unfiltered. The summary output highlights the easy wins
(decreased fp32 in HP-shape range) but the full CSV has EVERY change
so you can grep for anything.
"""
from __future__ import annotations

import argparse
import csv
import ctypes
import ctypes.wintypes as wt
import gzip
import struct
import sys
import time
from pathlib import Path

THIS = Path(__file__).resolve().parent
DUMPS = THIS / "dumps"

PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ           = 0x0010
MEM_COMMIT      = 0x1000
PAGE_GUARD      = 0x100
PAGE_NOACCESS   = 0x01
TH32CS_SNAPPROCESS = 0x00000002

k32 = ctypes.WinDLL("kernel32", use_last_error=True)


class MEMORY_BASIC_INFORMATION64(ctypes.Structure):
    _fields_ = [
        ("BaseAddress",       ctypes.c_ulonglong),
        ("AllocationBase",    ctypes.c_ulonglong),
        ("AllocationProtect", wt.DWORD),
        ("__align",           wt.DWORD),
        ("RegionSize",        ctypes.c_ulonglong),
        ("State",             wt.DWORD),
        ("Protect",           wt.DWORD),
        ("Type",              wt.DWORD),
        ("__align2",          wt.DWORD),
    ]


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize",              wt.DWORD),
        ("cntUsage",            wt.DWORD),
        ("th32ProcessID",       wt.DWORD),
        ("th32DefaultHeapID",   ctypes.c_void_p),
        ("th32ModuleID",        wt.DWORD),
        ("cntThreads",          wt.DWORD),
        ("th32ParentProcessID", wt.DWORD),
        ("pcPriClassBase",      ctypes.c_long),
        ("dwFlags",             wt.DWORD),
        ("szExeFile",           ctypes.c_wchar * 260),
    ]


def find_process(name: str) -> int:
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap == -1:
        return 0
    pe = PROCESSENTRY32W()
    pe.dwSize = ctypes.sizeof(pe)
    if not k32.Process32FirstW(snap, ctypes.byref(pe)):
        k32.CloseHandle(snap)
        return 0
    while True:
        if pe.szExeFile.lower() == name.lower():
            pid = pe.th32ProcessID
            k32.CloseHandle(snap)
            return pid
        if not k32.Process32NextW(snap, ctypes.byref(pe)):
            break
    k32.CloseHandle(snap)
    return 0


def open_handle(pid: int) -> int:
    return k32.OpenProcess(
        PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid
    )


def read_memory(handle, addr: int, size: int) -> bytes:
    """Best-effort read with binary fallback."""
    buf = (ctypes.c_ubyte * size)()
    read = ctypes.c_size_t(0)
    if k32.ReadProcessMemory(
        handle, ctypes.c_void_p(addr), buf, size, ctypes.byref(read)
    ) and read.value > 0:
        return bytes(buf[: read.value])
    if size <= 4096:
        return b""
    half = (size // 2) & ~0xFFF   # page-align
    if half == 0:
        return b""
    a = read_memory(handle, addr, half)
    b = read_memory(handle, addr + half, size - half)
    return a + b


def iter_regions(handle, start: int, end: int, max_size: int):
    """Yield (base, size) for committed readable pages."""
    addr = start
    mbi = MEMORY_BASIC_INFORMATION64()
    while addr < end:
        ok = k32.VirtualQueryEx(
            handle, ctypes.c_void_p(addr),
            ctypes.byref(mbi), ctypes.sizeof(mbi))
        if not ok:
            break
        next_addr = mbi.BaseAddress + mbi.RegionSize
        readable = (
            mbi.State == MEM_COMMIT
            and not (mbi.Protect & PAGE_GUARD)
            and mbi.Protect != PAGE_NOACCESS
        )
        if readable and mbi.RegionSize <= max_size:
            yield mbi.BaseAddress, mbi.RegionSize
        if next_addr <= addr:
            break
        addr = next_addr


def cmd_snapshot(args):
    pid = find_process("CrimsonDesert.exe")
    if not pid:
        print("CrimsonDesert.exe not running")
        return 1
    handle = open_handle(pid)
    if not handle:
        print(f"OpenProcess failed: {ctypes.get_last_error()}")
        return 1
    print(f"opened pid {pid}")

    out_dir = DUMPS / f"snap_{args.name}"
    out_dir.mkdir(parents=True, exist_ok=True)
    regions_csv = out_dir / "regions.csv"

    print(f"snapshot '{args.name}' starting -- writing to {out_dir}")
    t0 = time.time()
    total_bytes = 0
    region_count = 0
    with open(regions_csv, "w", newline="", encoding="utf-8") as rf:
        wr = csv.writer(rf)
        wr.writerow(["base_hex", "size", "filename"])
        for base, size in iter_regions(
            handle, args.start, args.end, args.max_region
        ):
            data = read_memory(handle, base, size)
            if not data:
                continue
            fname = f"{base:016x}.bin.gz"
            with gzip.open(out_dir / fname, "wb", compresslevel=1) as f:
                f.write(data)
            wr.writerow([f"0x{base:016x}", len(data), fname])
            region_count += 1
            total_bytes += len(data)
            if region_count % 50 == 0:
                print(f"  {region_count} regions, "
                      f"{total_bytes / 1024 / 1024:.1f} MB so far, "
                      f"{time.time() - t0:.1f}s")
    dt = time.time() - t0
    print(f"\nsnapshot done: {region_count} regions, "
          f"{total_bytes / 1024 / 1024:.1f} MB raw, {dt:.1f}s")
    print(f"wrote {regions_csv}")
    k32.CloseHandle(handle)
    return 0


def cmd_diff(args):
    a_dir = DUMPS / f"snap_{args.a}"
    b_dir = DUMPS / f"snap_{args.b}"
    if not a_dir.exists():
        print(f"missing {a_dir}")
        return 1
    if not b_dir.exists():
        print(f"missing {b_dir}")
        return 1

    # Build region map for B for fast lookup
    b_regions = {}
    with open(b_dir / "regions.csv", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            b_regions[int(row["base_hex"], 16)] = (
                int(row["size"]), row["filename"],
            )

    diff_csv = DUMPS / f"diff_{args.a}_{args.b}.csv"
    summary  = DUMPS / f"diff_{args.a}_{args.b}_summary.txt"

    print(f"diffing snap_{args.a} vs snap_{args.b}")
    t0 = time.time()
    total_words_compared = 0
    total_diffs = 0
    decreased_hp_shaped = []   # (addr, old_f32, new_f32) where new<old
    increased = []
    other_kinds = 0

    with open(diff_csv, "w", newline="", encoding="utf-8") as df:
        w = csv.writer(df)
        w.writerow([
            "addr_hex", "old_hex", "new_hex",
            "old_u32", "new_u32", "old_i32", "new_i32",
            "old_f32", "new_f32", "kind",
        ])

        with open(a_dir / "regions.csv", encoding="utf-8") as f:
            r = csv.DictReader(f)
            for row in r:
                base = int(row["base_hex"], 16)
                a_size = int(row["size"])
                if base not in b_regions:
                    continue
                b_size, b_fname = b_regions[base]
                size = min(a_size, b_size)
                # Load both region payloads
                with gzip.open(a_dir / row["filename"], "rb") as f1:
                    a_data = f1.read()
                with gzip.open(b_dir / b_fname, "rb") as f2:
                    b_data = f2.read()
                if a_data == b_data:
                    total_words_compared += size // 4
                    continue
                # Walk 4-byte words
                for off in range(0, size - 3, 4):
                    total_words_compared += 1
                    aw = a_data[off: off + 4]
                    bw = b_data[off: off + 4]
                    if aw == bw:
                        continue
                    addr = base + off
                    a_u = struct.unpack("<I", aw)[0]
                    b_u = struct.unpack("<I", bw)[0]
                    a_i = struct.unpack("<i", aw)[0]
                    b_i = struct.unpack("<i", bw)[0]
                    a_f = struct.unpack("<f", aw)[0]
                    b_f = struct.unpack("<f", bw)[0]

                    kind = "changed"
                    if (
                        not (a_f != a_f) and not (b_f != b_f)   # not NaN
                        and 1 <= a_f <= 1_000_000
                        and 0 <= b_f < a_f
                    ):
                        kind = "fp32_decreased_HP_shape"
                        decreased_hp_shaped.append((addr, a_f, b_f))
                    elif (
                        not (a_f != a_f) and not (b_f != b_f)
                        and 1 <= b_f <= 1_000_000
                        and 0 <= a_f < b_f
                    ):
                        kind = "fp32_increased_HP_shape"
                        increased.append((addr, a_f, b_f))
                    else:
                        other_kinds += 1

                    w.writerow([
                        f"0x{addr:016x}",
                        aw.hex(), bw.hex(),
                        a_u, b_u, a_i, b_i,
                        f"{a_f:.6g}", f"{b_f:.6g}",
                        kind,
                    ])
                    total_diffs += 1

    dt = time.time() - t0
    # Sort decreased_hp_shaped by magnitude of decrease (interesting first)
    decreased_hp_shaped.sort(
        key=lambda t: -(t[1] - t[2])  # biggest decrease first
    )

    with open(summary, "w", encoding="utf-8") as f:
        f.write(f"=== diff snap_{args.a} vs snap_{args.b} ===\n")
        f.write(f"compared {total_words_compared:,} 4-byte words in "
                f"{dt:.1f}s\n")
        f.write(f"total diffs: {total_diffs:,}\n")
        f.write(f"  fp32 decreased & HP-shaped: "
                f"{len(decreased_hp_shaped):,}\n")
        f.write(f"  fp32 increased & HP-shaped: {len(increased):,}\n")
        f.write(f"  other kinds: {other_kinds:,}\n\n")

        f.write("=== TOP 100 fp32 DECREASES (best HP candidates) ===\n")
        for addr, a, b in decreased_hp_shaped[:100]:
            delta = a - b
            f.write(f"  0x{addr:016x}  {a:>12.4f} -> {b:>12.4f}  "
                    f"(-{delta:>10.4f})\n")

        f.write("\n=== TOP 50 fp32 INCREASES (potential 'kills', "
                "buffs, regen) ===\n")
        for addr, a, b in increased[:50]:
            delta = b - a
            f.write(f"  0x{addr:016x}  {a:>12.4f} -> {b:>12.4f}  "
                    f"(+{delta:>10.4f})\n")

    print(f"\ndiff complete in {dt:.1f}s")
    print(f"  total 4-byte words compared: {total_words_compared:,}")
    print(f"  total diffs (any change):    {total_diffs:,}")
    print(f"  fp32 HP-shape decreases:     "
          f"{len(decreased_hp_shaped):,}")
    print(f"\nfull diff:    {diff_csv}")
    print(f"summary:      {summary}")
    if decreased_hp_shaped:
        print(f"\nTop 5 HP-shape decreases (likely HP/Stamina/etc.):")
        for addr, a, b in decreased_hp_shaped[:5]:
            print(f"  0x{addr:016x}  {a:.2f} -> {b:.2f}")
    return 0


def cmd_clear(args):
    import shutil
    if DUMPS.exists():
        shutil.rmtree(DUMPS)
        print(f"cleared {DUMPS}")
    return 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    s_snap = sub.add_parser("snapshot",
        help="capture all readable heap pages to disk")
    s_snap.add_argument("name", help="label, e.g. 1, before, baseline")
    s_snap.add_argument("--start", type=lambda s: int(s, 0),
                        default=0x140000000)
    s_snap.add_argument("--end",   type=lambda s: int(s, 0),
                        default=0x800000000)
    s_snap.add_argument("--max-region", type=int,
                        default=512 * 1024 * 1024,
                        help="skip regions larger than this (default 512MB)")
    s_snap.set_defaults(func=cmd_snapshot)

    s_diff = sub.add_parser("diff", help="diff two snapshots")
    s_diff.add_argument("a", help="first snapshot label")
    s_diff.add_argument("b", help="second snapshot label")
    s_diff.set_defaults(func=cmd_diff)

    s_clr = sub.add_parser("clear", help="delete all dumps")
    s_clr.set_defaults(func=cmd_clear)

    args = ap.parse_args()
    sys.exit(args.func(args) or 0)


if __name__ == "__main__":
    main()
