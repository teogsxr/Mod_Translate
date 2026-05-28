"""Extract every Korean-paloc entry that contains any English text.

Output
------
Writes two files to the user's Desktop:

    korean_protected_corpus.jsonl
        One JSON object per line (JSON Lines format — easy to stream-
        process, git-diff, and partially load). Schema per line:
            {
              "index":     int,     # row index in the original paloc
              "key":       str,     # paloc key
              "value":     str,     # full Korean value, including English fragments
              "english":   [        # every English / ASCII fragment found inside `value`
                {"text": str, "start": int, "end": int}
              ]
            }

    korean_protected_corpus.txt
        Human-readable mirror of the same data — one entry per block,
        easy to eyeball. Also marks the English fragments inline with
        guillemets so users can visually confirm what was detected.

How we decide what counts as "English"
--------------------------------------
We mark a character run as English-ish if ALL bytes are:

    * ASCII letters (A-Z, a-z)
    * ASCII digits (0-9)
    * Common placeholder / format punctuation:
      % { } < > [ ] / \\ . _ - + : # & | ~ ^ * ? = , ' " space

And the run contains at least ONE ASCII letter (pure-digit / pure-
punctuation runs like "0001" or "..." don't count — they're not
English).

This intentionally captures:
    - Placeholder tokens:      %0 %1 {ItemName} {0} <b> </color>
    - File references:         cd_phm_00_cloak.pac, character/foo.dds
    - Format specifiers:       %s %d \\n \\t
    - Technical markers:       0xff, 0x1000
    - Inline English phrases:  "Hello", "LoginReward"
    - Compound tokens:         RR-3000, BG-Green

It intentionally DOES NOT capture:
    - Pure Korean strings     (no trigger — Hangul is not ASCII letters)
    - Pure numbers / punct    (fail the "needs a letter" check)
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

# Ensure the repo root is importable when this is run as a script.
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from core.paloc_parser import parse_paloc
from core.vfs_manager import VfsManager

GAME = r"C:\Program Files (x86)\Steam\steamapps\common\Crimson Desert"
DESK = Path(r"C:\Users\hzeem\Desktop")

KOREAN_PALOC = "localizationstring_kor.paloc"


# Character class that forms an "English-ish" run. We walk the string
# looking for maximal contiguous runs of these characters. The "needs
# at least one letter" filter is applied AFTER the run is found.
_ENGLISH_RUN_RE = re.compile(
    r"[A-Za-z0-9%{}\[\]<>/\\._\-+:#&|~^*?=,'\"\s]+"
)

_HAS_LETTER_RE = re.compile(r"[A-Za-z]")


def find_english_fragments(value: str) -> list[tuple[str, int, int]]:
    """Return ``[(fragment, start_char, end_char)]`` for every English
    fragment inside ``value``. Fragments are stripped of leading /
    trailing whitespace so the positions tighten onto the real content.
    """
    out: list[tuple[str, int, int]] = []
    for m in _ENGLISH_RUN_RE.finditer(value):
        raw = m.group(0)
        # Require at least one ASCII letter — skip pure-digit or
        # pure-punctuation runs.
        if not _HAS_LETTER_RE.search(raw):
            continue
        # Tighten the match by stripping leading/trailing whitespace.
        stripped = raw.strip()
        if not stripped:
            continue
        lead = len(raw) - len(raw.lstrip())
        start = m.start() + lead
        end = start + len(stripped)
        out.append((stripped, start, end))
    return out


def main() -> None:
    print(f"Loading Korean paloc from game: {GAME}")
    vfs = VfsManager(GAME)

    # Find the Korean paloc via basename lookup across every group.
    korean_entry = None
    for group in vfs.list_package_groups():
        try:
            pamt = vfs.load_pamt(group)
            for e in pamt.file_entries:
                if os.path.basename(e.path).lower() == KOREAN_PALOC:
                    korean_entry = e
                    break
            if korean_entry is not None:
                break
        except Exception:
            continue

    if korean_entry is None:
        raise SystemExit(f"ERROR: {KOREAN_PALOC} not found in any loaded PAMT.")

    print(f"Found: {korean_entry.path}")
    raw = vfs.read_entry_data(korean_entry)
    print(f"Decompressed size: {len(raw):,} bytes")

    entries = parse_paloc(raw)
    print(f"Parsed {len(entries):,} entries")

    out_jsonl = DESK / "korean_protected_corpus.jsonl"
    out_text  = DESK / "korean_protected_corpus.txt"

    matched = 0
    total_fragments = 0

    with out_jsonl.open("w", encoding="utf-8") as fj, \
         out_text.open("w", encoding="utf-8") as ft:

        # Human-readable header for the text file.
        ft.write(
            "# CrimsonForge Korean corpus — entries containing English text\n"
            f"# Source file:  {korean_entry.path}\n"
            f"# Total Korean entries: {len(entries):,}\n"
            "# English fragments are surrounded by >>...<< in the value\n"
            "# " + "=" * 72 + "\n\n"
        )

        for idx, entry in enumerate(entries):
            fragments = find_english_fragments(entry.value)
            if not fragments:
                continue

            matched += 1
            total_fragments += len(fragments)

            record = {
                "index": idx,
                "key": entry.key,
                "value": entry.value,
                "english": [
                    {"text": t, "start": s, "end": e}
                    for t, s, e in fragments
                ],
            }
            fj.write(json.dumps(record, ensure_ascii=False) + "\n")

            # Render the text version with English wrapped in >><<.
            # We walk fragments from right to left so earlier indices
            # stay valid as we splice markers in.
            marked = entry.value
            for text, start, end in sorted(fragments, key=lambda x: -x[1]):
                marked = marked[:start] + ">>" + marked[start:end] + "<<" + marked[end:]

            ft.write(f"[{idx}]  key: {entry.key}\n")
            ft.write(f"      value: {marked}\n")
            ft.write(f"      english_fragments ({len(fragments)}):\n")
            for t, s, e in fragments:
                ft.write(f"        @{s}-{e}  {t!r}\n")
            ft.write("\n")

    print()
    print("=" * 72)
    print(f"Wrote entries with English content to:")
    print(f"  JSONL:  {out_jsonl}")
    print(f"  TXT:    {out_text}")
    print()
    print(f"Summary:")
    print(f"  Total Korean entries:           {len(entries):>10,}")
    print(f"  Entries containing English:     {matched:>10,}  ({matched*100/len(entries):.1f}%)")
    print(f"  Total English fragments found:  {total_fragments:>10,}")
    print(f"  Avg fragments per matched entry:{total_fragments/max(1,matched):>10.2f}")


if __name__ == "__main__":
    main()
