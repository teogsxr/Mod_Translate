"""Placeholder-locking pipeline for AI translation.

Problem
-------
Pearl Abyss paloc strings contain many non-prose tokens — line
breaks (``<br/>``), sentinels (``[EMPTY]``), printf-style args
(``%0`` / ``%1`` / ``%%``), document numbering (``#27``), and
namespaced game data references (``{emoji:...}``, ``{Key:...}``,
``{Staticinfo:Knowledge:Knowledge_Hp#생명}``). If we hand these
straight to an LLM, several things go wrong:

  1. The model translates parts of the token ("Staticinfo" →
     "정적정보", ``#생명`` → "#Life"), and the game's placeholder
     resolver then fails to find the key and renders the raw
     broken token in-game.

  2. The model drops or collapses ``<br/>`` tags so line breaks
     vanish from every translated line.

  3. The model "helpfully" translates ``[EMPTY]`` → "[비어 있음]"
     and breaks whatever game logic checks for the literal
     ``[EMPTY]`` sentinel.

Solution
--------
Round-trip every token through **opaque Unicode sentinels** that
look nothing like prose (``⟦CF0⟧``, ``⟦CF1⟧``, …). Models leave
opaque sentinels alone far more reliably than they preserve
domain-specific syntax.

Pipeline
~~~~~~~~
1. :func:`encode_for_translation` walks the source string and
   replaces every protected token with a sentinel. It returns

       (encoded_text, token_table)

   where ``token_table[i]`` is the original string that
   ``⟦CFi⟧`` replaced.

2. The caller sends ``encoded_text`` to the AI with the usual
   translation prompt. A one-line instruction in the system
   prompt (:data:`PROMPT_INSTRUCTION`) tells the model to
   preserve sentinel tokens verbatim.

3. :func:`decode_after_translation` restores the originals by
   substituting each ``⟦CFi⟧`` back to its original string.
   Sentinels the model accidentally mangled (case change,
   stray spaces, etc.) are recovered via a tolerant regex pass.

Special case: embedded Korean labels in ``{ns#라벨}``
-----------------------------------------------------
About 23% of ``{...}`` tokens in the Korean paloc are of the
form ``{Staticinfo:Knowledge:Knowledge_Hp#생명}``. The ``#``
separates the lookup namespace (must stay untranslated — it's a
game-data key) from the display label (must be translated — it's
the Korean name that renders in the UI).

When we encode one of these, we split on the first ``#``, lock
the namespace prefix, keep the Korean label in the encoded
string as ordinary prose so the AI can translate it, and wrap
the reassembly in a single sentinel + suffix pattern:

    source:   {Staticinfo:Knowledge:Knowledge_Hp#생명}
    encoded:  ⟦CF0⟧생명⟦/CF0⟧
    AI sees:  ⟦CF0⟧생명⟦/CF0⟧ → returns ⟦CF0⟧Life⟦/CF0⟧
    decoded:  {Staticinfo:Knowledge:Knowledge_Hp#Life}

The paired opening / closing sentinels survive every mainstream
LLM's attention pattern because the identical-prefix design
signals "structural markers" to the tokenizer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Tuple


# ── Sentinel format ────────────────────────────────────────────
#
# ``⟦`` U+27E6 and ``⟧`` U+27E7 are mathematical white-square
# brackets. They almost never appear in user text, survive
# tokenisation cleanly (Claude, GPT-4, Gemini all treat them as
# distinct tokens), and are visible enough that a translator
# reviewing the AI output can spot mangled ones at a glance.
_SENTINEL_OPEN = "\u27E6"
_SENTINEL_CLOSE = "\u27E7"
_TAG_PREFIX = "CF"

# Paired form for hash-label tokens with an embedded Korean label
# ⟦CFnn⟧<translatable>⟦/CFnn⟧. The slash mirrors XML/HTML close-
# tag style which every major LLM tokeniser has strong priors for.
_PAIR_OPEN = "⟦CF{}⟧"
_PAIR_CLOSE = "⟦/CF{}⟧"
_SIMPLE = "⟦CF{}⟧"


# ── Pattern ordering ──────────────────────────────────────────
#
# Order matters — the first regex that matches a region wins. We
# encode LONGEST / MOST-SPECIFIC patterns first so a bare ``{``
# doesn't eat a ``{Staticinfo:...#라벨}`` match.

# Single {...} with a hash+content tail. The Korean label after
# '#' is captured separately so we can keep it in the encoded
# stream for AI translation.
_HASH_LABEL_BRACE_RE = re.compile(r"\{([^{}#]+)#([^{}]*)\}")

# Single {...} with no hash — fully opaque.
_PLAIN_BRACE_RE = re.compile(r"\{[^{}]+\}")

# <br/> and any other angle-tag (per census all are <br/> but we
# match the broader pattern so future content with <b> / <color>
# still round-trips cleanly).
_ANGLE_TAG_RE = re.compile(r"<(/)?[A-Za-z][A-Za-z0-9_]*[^<>\n]*>")

# [EMPTY] and friends — any all-ASCII identifier in square
# brackets. The paloc census shows [EMPTY] is the only real
# hit but we stay permissive.
_SQUARE_BRACKET_RE = re.compile(r"\[[A-Za-z0-9_:# .-]+\]")

# Printf-style %0 / %1 / %% / %s / %d / %1$s. ``%%`` first so
# the bare `%N` regex doesn't consume its first ``%``.
_PERCENT_DOUBLE_RE = re.compile(r"%%")
_PERCENT_ARG_RE = re.compile(r"%(?:\d+\$)?[A-Za-z]|%\d+")

# Hash-numbering inside prose (e.g. "낙서 #27"). We require the
# ``#`` to be preceded by whitespace or start-of-string so we
# don't accidentally eat the '#' inside a {...#label} token that
# somehow escaped earlier regexes.
_HASH_NUM_RE = re.compile(r"(?:(?<=\s)|(?<=^))#\d+")


# ── Public API ────────────────────────────────────────────────

# A few patterns encode as paired sentinels (for hash-label
# braces), the rest as simple ones. The table stores both so
# decode_after_translation can restore without knowing which
# shape produced the index.
@dataclass
class _TokenEntry:
    original: str        # the literal source fragment to restore
    encoded: str         # how it appears in the text sent to the AI
    # For paired (hash-label brace) entries, these two hold the
    # pieces we splice back around the AI-translated label:
    prefix: str = ""     # e.g. "{Staticinfo:Knowledge:Knowledge_Hp#"
    suffix: str = ""     # always "}" in the current dataset


# System-prompt line the engine adds. Short + imperative; the
# explicit list of sentinels helps anchor the attention.
PROMPT_INSTRUCTION = (
    "CRITICAL: Tokens of the form ⟦CF<number>⟧ and ⟦/CF<number>⟧ "
    "are non-translatable placeholders inserted by the tooling. "
    "Preserve every sentinel VERBATIM — same digits, same bracket "
    "characters, same order. Text BETWEEN a paired ⟦CFn⟧…⟦/CFn⟧ "
    "pair IS translatable content and should be translated into "
    "the target language like any other prose. Never invent new "
    "sentinels and never drop existing ones."
)


def encode_for_translation(source: str) -> Tuple[str, List[_TokenEntry]]:
    """Encode every protected token as a sentinel.

    Returns ``(encoded, table)``. ``table[i]`` holds the record
    for sentinel ``⟦CFi⟧`` (or the paired pair for hash-label
    braces) so :func:`decode_after_translation` can reverse the
    substitution.

    Side-effect-free — the source string is not mutated.
    """
    if not source:
        return source, []

    table: list[_TokenEntry] = []
    encoded = source

    # 1) Hash-label braces first (most specific) — we do these as
    #    a single regex sub with a replacement callable.
    def _sub_hash_brace(m: re.Match) -> str:
        idx = len(table)
        namespace = m.group(1)
        label = m.group(2)
        prefix = "{" + namespace + "#"
        suffix = "}"
        table.append(_TokenEntry(
            original=m.group(0),
            encoded=_PAIR_OPEN.format(idx) + label + _PAIR_CLOSE.format(idx),
            prefix=prefix,
            suffix=suffix,
        ))
        return _PAIR_OPEN.format(idx) + label + _PAIR_CLOSE.format(idx)

    encoded = _HASH_LABEL_BRACE_RE.sub(_sub_hash_brace, encoded)

    # 2) Plain braces (fully opaque).
    def _sub_simple(regex: re.Pattern, text: str) -> str:
        def _repl(m: re.Match) -> str:
            idx = len(table)
            token = m.group(0)
            sentinel = _SIMPLE.format(idx)
            table.append(_TokenEntry(original=token, encoded=sentinel))
            return sentinel
        return regex.sub(_repl, text)

    encoded = _sub_simple(_PLAIN_BRACE_RE, encoded)
    encoded = _sub_simple(_ANGLE_TAG_RE, encoded)
    encoded = _sub_simple(_SQUARE_BRACKET_RE, encoded)
    # Percent forms: %%  MUST be encoded BEFORE  %N so the
    # `%N` regex doesn't chew up one half of a `%%`.
    encoded = _sub_simple(_PERCENT_DOUBLE_RE, encoded)
    encoded = _sub_simple(_PERCENT_ARG_RE, encoded)
    # Hash numbering in prose — last because the paired brace
    # regex above already consumed any '#' that was part of a
    # {...#label} token.
    encoded = _sub_simple(_HASH_NUM_RE, encoded)

    return encoded, table


# Tolerant regex for finding sentinels in the AI's output. We
# accept a tiny amount of capitalisation / whitespace noise that
# some models emit, then normalise before indexing. Models that
# leave the sentinel perfectly intact match the strict form on
# the first pass.
_SENTINEL_RE = re.compile(
    r"\u27E6\s*(/?)\s*CF\s*(\d+)\s*\u27E7",
    re.IGNORECASE,
)


def decode_after_translation(
    translated: str, table: List[_TokenEntry],
) -> str:
    """Restore every protected token in ``translated`` using ``table``.

    Handles two shapes:

      * ``⟦CFn⟧``               → ``table[n].original`` (simple)
      * ``⟦CFn⟧<label>⟦/CFn⟧``  → ``prefix + label + suffix``
                                   (paired, preserves the AI-
                                    translated label)

    Un-paired sentinels are replaced with the original literal.
    Missing sentinels (the AI dropped them) are left missing —
    we emit a log warning higher up the stack so callers can
    decide whether to retry.
    """
    if not table:
        return translated

    # Walk the translated text, find each sentinel in order, and
    # replace it. We iterate by repeated regex search-from-end so
    # positions stay valid as we splice in replacement text that
    # might be longer or shorter than the sentinel.
    text = translated

    # Step 1: paired sentinels. Look for opening sentinel whose
    # corresponding close follows later in the string.
    # Repeat until no more paired matches exist.
    while True:
        m = _SENTINEL_RE.search(text)
        if m is None:
            break
        is_close = bool(m.group(1))
        idx_str = m.group(2)
        try:
            idx = int(idx_str)
        except ValueError:
            # Can't parse — strip the sentinel and continue.
            text = text[:m.start()] + text[m.end():]
            continue
        if idx >= len(table):
            # Out-of-range sentinel — the AI hallucinated one.
            text = text[:m.start()] + text[m.end():]
            continue

        entry = table[idx]
        if is_close:
            # Close without an open — just drop it.
            text = text[:m.start()] + text[m.end():]
            continue

        # If this is a paired-sentinel entry, look for its
        # corresponding close. Otherwise it's a simple one.
        if entry.prefix or entry.suffix:
            # Paired: find the matching ⟦/CFn⟧ AFTER the open.
            close_re = re.compile(
                r"\u27E6\s*/\s*CF\s*" + str(idx) + r"\s*\u27E7",
                re.IGNORECASE,
            )
            close_m = close_re.search(text, m.end())
            if close_m is None:
                # AI dropped the close. Best-effort: take the rest
                # of the string up to the next open or end as the
                # label, then reassemble.
                next_open = _SENTINEL_RE.search(text, m.end())
                label_end = next_open.start() if next_open else len(text)
                label = text[m.end():label_end]
                replacement = entry.prefix + label + entry.suffix
                text = text[:m.start()] + replacement + text[label_end:]
            else:
                label = text[m.end():close_m.start()]
                replacement = entry.prefix + label + entry.suffix
                text = text[:m.start()] + replacement + text[close_m.end():]
        else:
            # Simple sentinel — swap in the original.
            text = text[:m.start()] + entry.original + text[m.end():]

    return text


def count_sentinels_per_entry(table: List[_TokenEntry]) -> dict[str, int]:
    """Diagnostics helper — how many of each kind are in a table."""
    simple = sum(1 for t in table if not t.prefix and not t.suffix)
    paired = len(table) - simple
    return {"simple": simple, "paired": paired, "total": len(table)}
