"""Enumerate EVERY placeholder / protected-token pattern in the
Korean paloc, not just ``{...}``.

This is the full census of "things a translator must not touch".
We pattern-match on every class of token the game uses — ``%`` /
``#`` / ``$`` / ``\\`` / angle-tags / square-brackets / etc. —
then classify each occurrence by sub-pattern and report counts +
examples.

Output
------
Prints a tiered report to stdout and writes a JSON file to the
Desktop with the full census plus examples per sub-pattern.

Token families detected
-----------------------
``%``    : %0 %1 ... (positional), %s %d %f %u %x (C-format),
           %1$s style, bare %
``#``    : #0 #1 ... (positional), #Word, #Name
``$``    : $var, $0, $Gold_Amount
``\\``   : \\n \\t \\r (escapes inside string literals)
``<>``   : <b> </b> <color=red> <br/> <size=120%> etc.
``[]``   : [PlayerName], [0], [Item]
``()``   : (Damage), (MP Cost) — only when ALL-ASCII inside
``{{}}``  : {{escaped literal brace}}
``@``    : @item, @player
``^``    : ^ff0000 (colour prefix)
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from core.paloc_parser import parse_paloc
from core.vfs_manager import VfsManager

GAME = r"C:\Program Files (x86)\Steam\steamapps\common\Crimson Desert"
DESK = Path(r"C:\Users\hzeem\Desktop")


# ── Pattern catalog ──────────────────────────────────────────────
#
# Ordered tuple of (family, regex, sub-classifier).  The regex
# captures the WHOLE token; the sub-classifier narrows it into a
# specific pattern bucket for per-bucket counting.
#
# Notes
# -----
# * Single ``{...}`` is handled by the separate classify_braces.py
#   scanner. We include it here again so this file is a complete
#   census, but the rules inside are identical.
# * The ``<tag>`` regex is permissive — it accepts any <...> that
#   doesn't span a newline. We then filter out obvious false
#   positives (like ``<1`` or angle brackets used mathematically).

_PATTERNS: tuple[tuple[str, re.Pattern, callable], ...] = (
    # %-family: %N / %1$s / %s / %d / %x / %f / %u / %ld / %lu / bare %
    (
        "percent",
        re.compile(r"%(?:\d+\$)?[A-Za-z]|%\d+|%%"),
        lambda m: (
            "double_percent" if m == "%%"
            else "dollar_arg" if "$" in m
            else "digit_arg" if m[1:].isdigit()
            else "format_specifier"
        ),
    ),
    # #-family: #digits, #WordLikeIdentifier, or bare #
    (
        "hash",
        re.compile(r"#\d+|#[A-Za-z_][A-Za-z0-9_]{0,40}"),
        lambda m: (
            "digit_positional" if m[1:].isdigit()
            else "named_token"
        ),
    ),
    # $-family: $digit or $identifier
    (
        "dollar",
        re.compile(r"\$\d+|\$[A-Za-z_][A-Za-z0-9_]{0,40}"),
        lambda m: (
            "digit_positional" if m[1:].isdigit()
            else "named_token"
        ),
    ),
    # @-family: @identifier (leading sigil style)
    (
        "at",
        re.compile(r"@[A-Za-z_][A-Za-z0-9_]{0,40}"),
        lambda m: "named_token",
    ),
    # ^-family: ^ff0000 colour codes (six-hex), or ^reset, ^0
    (
        "caret",
        re.compile(r"\^[0-9A-Fa-f]{6}|\^[A-Za-z0-9_]+"),
        lambda m: (
            "six_hex_colour" if re.fullmatch(r"\^[0-9A-Fa-f]{6}", m)
            else "named_token"
        ),
    ),
    # \-escape: \n \t \r \\ (note these are literal backslash +
    # letter inside the paloc string, not Python escapes).
    (
        "backslash_escape",
        re.compile(r"\\[nrtbfavs0\\\"']"),
        lambda m: {
            "\\n": "newline",
            "\\r": "carriage_return",
            "\\t": "tab",
            "\\\\": "literal_backslash",
            "\\\"": "literal_quote",
            "\\'": "literal_apos",
            "\\0": "null",
        }.get(m, "other_escape"),
    ),
    # <tag>: HTML/Unity-style rich-text tags
    (
        "angle_tag",
        re.compile(r"<(/)?([A-Za-z][A-Za-z0-9_]*)([^<>\n]*)>"),
        lambda m: (
            "close" if m.startswith("</")
            else "self_close" if m.endswith("/>")
            else "open_with_attrs" if "=" in m
            else "open"
        ),
    ),
    # [bracket] — only if inside is ASCII-only (else it's probably
    # Korean prose, not a placeholder).
    (
        "square_bracket",
        re.compile(r"\[[A-Za-z0-9_:# .-]+\]"),
        lambda m: (
            "digit" if re.fullmatch(r"\[\d+\]", m)
            else "identifier"
        ),
    ),
    # {{literal}} — double-brace escape pattern used by some engines
    (
        "double_brace",
        re.compile(r"\{\{|\}\}"),
        lambda m: "escape_open" if m == "{{" else "escape_close",
    ),
    # Single {...} — also covered by classify_braces.py; repeated
    # here so this scanner gives the whole placeholder census.
    (
        "single_brace",
        re.compile(r"\{([^{}]+)\}"),
        lambda m: (
            "hash_label" if "#" in m
            else "namespaced" if ":" in m
            else "bare_identifier"
        ),
    ),
)


def main() -> None:
    print("Loading Korean paloc ...")
    vfs = VfsManager(GAME)
    korean_entry = None
    for g in vfs.list_package_groups():
        try:
            pamt = vfs.load_pamt(g)
            for e in pamt.file_entries:
                if os.path.basename(e.path).lower() == "localizationstring_kor.paloc":
                    korean_entry = e
                    break
            if korean_entry is not None:
                break
        except Exception:
            continue
    if korean_entry is None:
        raise SystemExit("Could not find Korean paloc.")

    entries = parse_paloc(vfs.read_entry_data(korean_entry))
    print(f"Parsed {len(entries):,} entries")

    # family -> sub_pattern -> count
    family_counts: dict[str, Counter[str]] = {
        fam: Counter() for fam, _, _ in _PATTERNS
    }
    # family -> sub_pattern -> [sample tokens]
    samples: dict[str, dict[str, list[str]]] = {
        fam: defaultdict(list) for fam, _, _ in _PATTERNS
    }
    # family -> sub_pattern -> [sample (source_key, truncated_value)]
    contexts: dict[str, dict[str, list[tuple[str, str]]]] = {
        fam: defaultdict(list) for fam, _, _ in _PATTERNS
    }

    total_tokens_by_family: Counter[str] = Counter()

    for entry in entries:
        for family, pattern, sub_classifier in _PATTERNS:
            for m in pattern.finditer(entry.value):
                token = m.group(0)
                sub = sub_classifier(token)
                family_counts[family][sub] += 1
                total_tokens_by_family[family] += 1
                if len(samples[family][sub]) < 6:
                    samples[family][sub].append(token)
                if len(contexts[family][sub]) < 3:
                    v = entry.value
                    if len(v) > 120:
                        v = v[:117] + "..."
                    contexts[family][sub].append((str(entry.key), v))

    grand_total = sum(total_tokens_by_family.values())

    # ── Header ──────────────────────────────────────────
    print()
    print(f"Total placeholder tokens across all families: {grand_total:,}")
    print()
    print("FAMILY TOTALS (sorted):")
    print(f"  {'count':>10}  {'pct':>6}  family")
    print("  " + "-" * 56)
    for fam, cnt in total_tokens_by_family.most_common():
        pct = cnt * 100.0 / grand_total if grand_total else 0
        print(f"  {cnt:>10,}  {pct:>5.2f}%  {fam}")
    print()

    # ── Per-family breakdown ────────────────────────────
    for family, _, _ in _PATTERNS:
        fam_total = total_tokens_by_family[family]
        if fam_total == 0:
            print(f"FAMILY: {family}  (no occurrences)")
            print()
            continue
        print(f"FAMILY: {family}  (total: {fam_total:,})")
        print(f"  {'count':>10}  {'pct':>6}  {'sub_pattern':<30} examples")
        print("  " + "-" * 80)
        for sub, cnt in family_counts[family].most_common():
            pct = cnt * 100.0 / fam_total
            samples_str = ", ".join(
                (s if len(s) < 28 else s[:25] + "...")
                for s in samples[family][sub][:4]
            )
            print(f"  {cnt:>10,}  {pct:>5.2f}%  {sub:<30} {samples_str}")
        # Show up to 2 full-context examples for each sub
        print()
        for sub, _ in family_counts[family].most_common(3):
            for key, v in contexts[family][sub][:2]:
                display = v if len(v) < 100 else v[:97] + "..."
                print(f"    [{sub}] key={key}:")
                print(f"        {display}")
            print()

    # ── JSON dump ───────────────────────────────────────
    out = DESK / "placeholder_census.json"
    dump = {
        "total_tokens": grand_total,
        "family_totals": dict(total_tokens_by_family),
        "families": {
            fam: {
                sub: {
                    "count": cnt,
                    "samples": list(samples[fam][sub]),
                    "contexts": [
                        {"key": k, "value": v}
                        for k, v in contexts[fam][sub]
                    ],
                }
                for sub, cnt in family_counts[fam].most_common()
            }
            for fam, _, _ in _PATTERNS
        },
    }
    with out.open("w", encoding="utf-8") as f:
        json.dump(dump, f, ensure_ascii=False, indent=2)
    print(f"Full JSON census written to: {out}")


if __name__ == "__main__":
    main()
