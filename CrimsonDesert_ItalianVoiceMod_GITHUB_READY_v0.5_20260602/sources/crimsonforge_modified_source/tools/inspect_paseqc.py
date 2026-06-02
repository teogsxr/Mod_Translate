#!/usr/bin/env python3
"""Pretty-print every printable ASCII run >= 4 chars in a .paseqc /
.paseq, then show the byte context around any 'Boss_Ogre_55515' or
0x000F492A occurrence, then look for known PA serializer keywords:
HP, Hp, Damage, AttackPower, Defence, MoveSpeed, Override, Multiplier,
Scale, Stat, _difficulty, _override, GroggyHP.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


KEYWORDS = (
    b"_spawnCharacterKey", b"_spawnHp", b"_overrideHp", b"_overrideDamage",
    b"_overrideAttackPower", b"_difficulty", b"DifficultyMultiplier",
    b"HpMultiplier", b"DamageMultiplier", b"StatScale", b"StatMultiplier",
    b"OverrideStat", b"phase1Hp", b"phase2Hp", b"phaseHp",
    b"_stage", b"_phase", b"_intensity",
    b"GroggyHp", b"PoiseHp", b"Stagger",
    b"AttackPower", b"Defence", b"Defense", b"MaxHp", b"MaxHP",
    b"MoveSpeed", b"AggroRange", b"AttackRange",
    b"_scaleStat", b"_scaleHp", b"_scaleDamage", b"_scaleDefence",
    b"_baseHp", b"_baseDamage", b"_baseDefense", b"_baseAttackPower",
    b"BossInfo", b"BossStat", b"BossPhase", b"BossDifficulty",
    b"NpcInfo", b"MonsterInfo", b"MercenaryInfo",
    b"override", b"Override", b"multiplier", b"Multiplier",
    b"scale_", b"_scale", b"Scale", b"stat_", b"_stat", b"Stat",
    b"hp_", b"_hp", b"Hp",
    b"damage", b"Damage",
    b"phase", b"Phase",
    b"_difficulty", b"Difficulty",
    b"GroggyResistance",
    b"Boss_Ogre_55515",
)


def find_runs(data: bytes, min_len: int = 4) -> list[tuple[int, str]]:
    out = []
    cur_start = -1
    cur = bytearray()
    for i, b in enumerate(data):
        if 32 <= b < 127:
            if cur_start < 0:
                cur_start = i
            cur.append(b)
        else:
            if len(cur) >= min_len:
                out.append((cur_start, cur.decode("ascii")))
            cur.clear()
            cur_start = -1
    if len(cur) >= min_len:
        out.append((cur_start, cur.decode("ascii")))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("file", help="Path to .paseq / .paseqc / etc")
    ap.add_argument("--out", default=None,
                    help="Write report to this path (default: <file>.txt)")
    args = ap.parse_args()

    path = Path(args.file)
    data = path.read_bytes()
    print(f"Loaded {path}: {len(data)} bytes")

    out_path = Path(args.out) if args.out else path.with_suffix(path.suffix + ".inspect.txt")
    lines: list[str] = []
    lines.append(f"=== {path.name} ({len(data)} bytes) ===")
    lines.append("")

    # ASCII string runs.
    runs = find_runs(data, 4)
    lines.append(f"ASCII runs >= 4 chars: {len(runs)}")
    lines.append("")
    for off, s in runs:
        lines.append(f"  @ 0x{off:08X}  {s!r}")
    lines.append("")

    # Keyword scan with byte context.
    lines.append("=" * 70)
    lines.append("KEYWORD HITS (case-sensitive bytes)")
    lines.append("=" * 70)
    for kw in KEYWORDS:
        pos = 0
        while True:
            i = data.find(kw, pos)
            if i < 0:
                break
            ctx = data[max(0, i - 40):i + len(kw) + 80]
            esc = "".join(chr(b) if 32 <= b < 127 else "." for b in ctx)
            lines.append(f"  {kw!r}  @ 0x{i:08X}  ctx={esc!r}")
            pos = i + 1

    # Hash 0x000F492A
    needle = b"\x2A\x49\x0F\x00"
    pos = 0
    lines.append("")
    lines.append("=" * 70)
    lines.append("HASH 0x000F492A (Boss_Ogre_55515) HITS")
    lines.append("=" * 70)
    while True:
        i = data.find(needle, pos)
        if i < 0:
            break
        ctx_pre = data[max(0, i - 64):i]
        ctx_post = data[i + 4:i + 4 + 128]
        esc_pre = "".join(chr(b) if 32 <= b < 127 else "." for b in ctx_pre)
        esc_post = "".join(chr(b) if 32 <= b < 127 else "." for b in ctx_post)
        # Try interpreting nearby 4-byte chunks as f32 in case there's a
        # vec3 or scalar override right next to it.
        import struct
        floats_after = []
        for k in range(0, 64, 4):
            o = i + 4 + k
            if o + 4 > len(data):
                break
            f = struct.unpack_from("<f", data, o)[0]
            u = struct.unpack_from("<I", data, o)[0]
            floats_after.append((k, u, f))
        lines.append(f"  @ 0x{i:08X}")
        lines.append(f"    pre  = {esc_pre!r}")
        lines.append(f"    post = {esc_post!r}")
        lines.append(f"    next 16 dwords (offset, u32 hex, f32):")
        for k, u, f in floats_after:
            lines.append(f"      +{k:2d}  0x{u:08X}  {f:>14.6g}")
        pos = i + 1

    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {out_path}  ({len(lines)} lines)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
