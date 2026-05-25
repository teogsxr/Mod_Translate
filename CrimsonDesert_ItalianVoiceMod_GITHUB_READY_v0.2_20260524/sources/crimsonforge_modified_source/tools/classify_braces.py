"""Classify every ``{...}`` brace token in the Korean paloc.

We enumerate every occurrence of balanced single-brace tokens,
then categorise each by its internal STRUCTURE (not content) so
the user can see at a glance the distinct token patterns the
game uses. Each pattern keyed by a compact shape string plus the
raw content type.

Output
------
Prints a frequency-sorted list of distinct shapes to stdout AND
writes a JSON file to the Desktop with full examples per pattern.
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

# Single-level balanced {...}. The game doesn't appear to nest
# braces within other braces at the paloc layer, so a non-greedy
# match between the braces is safe.
_BRACE_RE = re.compile(r"\{([^{}]*)\}")


def _classify_char(c: str) -> str:
    """Return a one-letter category for a single character so we
    can compose a shape signature.

        E = ASCII letter A-Za-z
        D = ASCII digit 0-9
        K = non-ASCII code point above U+007F (Korean, symbols, etc.)
        : # . _ - / \\ + * ? = , space  — punctuation kept literal
        otherwise  O  (other)
    """
    if c.isspace():
        return " "
    o = ord(c)
    if 0x41 <= o <= 0x5A or 0x61 <= o <= 0x7A:
        return "E"
    if 0x30 <= o <= 0x39:
        return "D"
    if o < 0x80:
        if c in ":#.,_-/\\+*?=<>[]()'\"":
            return c
        return "O"
    return "K"


def _shape(inner: str) -> str:
    """Compress a classification run into a shape signature.

    ``"Staticinfo:Knowledge:Knowledge_Schneider#작은 슈나이더"``
    becomes ``"E:E:E_E#K K"``.

    Runs of the SAME letter-or-digit category collapse to one
    symbol — so ``"Knowledge_Schneider"`` reads as ``"E_E"`` not
    ``"EEEEEEEEE_EEEEEEEEE"`` which would obliterate any useful
    structural signal.
    """
    out: list[str] = []
    prev = ""
    for c in inner:
        cat = _classify_char(c)
        if cat == prev and cat in {"E", "D", "K", " "}:
            continue
        out.append(cat)
        prev = cat
    return "".join(out)


# High-level pattern types — computed from the shape + content.
# We bucket each token into one of these so the output is easier
# to scan than the raw shape list (which can have hundreds of
# long patterns).
def _pattern_type(inner: str, shape: str) -> str:
    has_letter = any("A" <= c.lower() <= "z" for c in inner if c.isascii())
    has_korean = any(ord(c) >= 0x80 for c in inner)
    has_digit = any(c.isdigit() for c in inner)
    has_colon = ":" in inner
    has_hash = "#" in inner

    if not has_letter and not has_korean and has_digit:
        return "pure_number"
    if has_colon and has_hash and has_korean:
        return "namespaced_with_korean_label"   # E:E:E#K
    if has_colon and has_hash:
        return "namespaced_with_ascii_label"
    if has_colon and has_korean:
        return "namespaced_with_korean_rhs"
    if has_colon:
        return "namespaced_ascii"
    if has_hash and has_korean:
        return "hash_korean_label"
    if has_hash:
        return "hash_ascii"
    if has_korean and has_letter:
        return "mixed_english_korean"
    if has_korean:
        return "korean_only"
    if has_letter and has_digit:
        return "ascii_letters_and_digits"
    if has_letter:
        return "ascii_letters_only"
    return "other"


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

    pattern_counter: Counter[str] = Counter()
    shape_counter: Counter[str] = Counter()
    # per-pattern examples: pattern -> list of (full_token, shape, source_entry_key)
    examples: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    # shape -> sample tokens
    shape_samples: dict[str, list[str]] = defaultdict(list)
    total_tokens = 0

    for entry in entries:
        for m in _BRACE_RE.finditer(entry.value):
            inner = m.group(1)
            if not inner:
                continue
            token = m.group(0)
            total_tokens += 1
            s = _shape(inner)
            p = _pattern_type(inner, s)
            pattern_counter[p] += 1
            shape_counter[s] += 1
            if len(examples[p]) < 8:
                examples[p].append((token, s, str(entry.key)))
            if len(shape_samples[s]) < 5:
                shape_samples[s].append(token)

    print()
    print(f"Total {{...}} tokens scanned: {total_tokens:,}")
    print(f"Distinct content shapes: {len(shape_counter):,}")
    print(f"Distinct high-level pattern types: {len(pattern_counter):,}")
    print()

    # ── High-level pattern-type table ───────────────────
    print("HIGH-LEVEL PATTERN TYPES (sorted by frequency):")
    print(f"{'count':>10}  {'pct':>6}  pattern_type")
    print("-" * 74)
    for pattern, cnt in pattern_counter.most_common():
        pct = cnt * 100.0 / total_tokens
        print(f"{cnt:>10,}  {pct:>5.2f}%  {pattern}")
    print()

    # Give 3 examples of each pattern
    for pattern, cnt in pattern_counter.most_common():
        print(f"EXAMPLES — {pattern}  ({cnt:,} occurrences)")
        for token, shape, key in examples[pattern][:3]:
            print(f"    {token}")
            print(f"        shape={shape!r}  seen-in-key={key}")
        print()

    # ── Shape table (top 30 distinct shapes) ─────────────
    print("TOP 30 MOST FREQUENT SHAPE SIGNATURES:")
    print(f"{'count':>10}  shape                  example")
    print("-" * 74)
    for shape, cnt in shape_counter.most_common(30):
        sample = shape_samples[shape][0]
        display = sample if len(sample) < 40 else sample[:37] + "..."
        print(f"{cnt:>10,}  {shape:<22s}  {display}")

    # ── JSON dump ────────────────────────────────────────
    out_json = DESK / "brace_token_shapes.json"
    with out_json.open("w", encoding="utf-8") as f:
        json.dump({
            "total_tokens": total_tokens,
            "patterns": {
                p: {
                    "count": pattern_counter[p],
                    "examples": [t for t, _, _ in examples[p]],
                }
                for p in pattern_counter
            },
            "top_shapes": {
                s: {
                    "count": shape_counter[s],
                    "samples": shape_samples[s],
                }
                for s, _ in shape_counter.most_common(60)
            },
        }, f, ensure_ascii=False, indent=2)
    print()
    print(f"Full JSON written to: {out_json}")


if __name__ == "__main__":
    main()
