"""Scan translated entries for broken placeholder tokens.

Why this exists
---------------
Even with the tokenizer from :mod:`core.translation_tokenizer`
protecting placeholders before they reach the AI, a subset of
entries still come back broken because:

  * The model hallucinated extra sentinels (``\u27E6CF99\u27E7``)
    that never existed in the source — decode skips them but the
    user may want to see every case for QA.

  * The model "helpfully" translated a token the tooling missed
    (a new token family Pearl Abyss added in a future patch, for
    instance). These show up as a placeholder in the source that
    is not present in the translated text.

  * A manual edit by the translator altered a brace namespace
    (``{Key:Key_Run}`` → ``{Key:Key_Running}``) that breaks the
    game's lookup.

This module is the QA surface. It compares the PROTECTED tokens
in ``source`` against what's in ``translated`` and reports every
discrepancy with enough detail for the UI to offer a surgical
fix.

What a "surgical fix" means here
--------------------------------
We NEVER re-translate or rewrite translated prose. Every fix is
a bounded string edit that touches only the broken placeholder:

  * MISSING — the token is in source but absent from translation.
    We append the token to the end of the translation (prefixed
    with a single space) OR, if we can identify a plausible
    insertion point, insert it there. The surrounding prose is
    untouched.

  * ALTERED — the token exists in translation but with a
    different namespace / identifier. We replace just the
    altered token with the source's original.

  * LEAKED_SENTINEL — a tokenizer sentinel (``\u27E6CFn\u27E7``)
    appears in the final translation. We strip just the sentinel.

  * EXTRA_TOKEN — a placeholder appears in the translation that
    was NOT in the source. We don't auto-remove these (might be
    a legitimate edit) but flag them for human review.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


class IssueKind(Enum):
    MISSING = "missing"
    ALTERED = "altered"
    LEAKED_SENTINEL = "leaked_sentinel"
    EXTRA_TOKEN = "extra_token"


@dataclass
class PlaceholderIssue:
    """One detected discrepancy between source and translation."""
    kind: IssueKind
    source_token: str              # the original protected token
    translated_fragment: str = ""  # what we found instead (for ALTERED / LEAKED / EXTRA)
    source_span: tuple = (0, 0)    # position of the source token
    translated_span: tuple = (0, 0)
    # Whether :func:`autofix_entry` knows how to fix this issue
    # without risking other text. EXTRA_TOKEN always requires a
    # human decision so is not auto-fixable.
    auto_fixable: bool = True


@dataclass
class ScanResult:
    """Summary of one source/translated pair."""
    source: str
    translated: str
    issues: List[PlaceholderIssue] = field(default_factory=list)

    @property
    def broken(self) -> bool:
        return bool(self.issues)

    @property
    def auto_fixable(self) -> int:
        return sum(1 for i in self.issues if i.auto_fixable)


# ── Token detection (mirrors translation_tokenizer.py families) ──

_SENTINEL_LEAK_RE = re.compile(
    # Tolerant to the same noise forms the tokenizer's decoder
    # accepts — whitespace or capitalisation drift inside the
    # bracket pair shouldn't hide the leak from QA.
    r"\u27E6\s*/?\s*CF\s*\d+\s*\u27E7",
    re.IGNORECASE,
)


def _find_source_tokens(text: str) -> list[tuple[str, tuple[int, int]]]:
    """Return every protected token in ``text`` with its character span.

    The regex set mirrors the encoder in ``core.translation_tokenizer``.
    Order-of-matching is non-overlapping, longest-first, so
    ``{ns#label}`` is captured as one token instead of split.
    """
    from core.translation_tokenizer import (
        _ANGLE_TAG_RE, _HASH_LABEL_BRACE_RE, _HASH_NUM_RE,
        _PERCENT_ARG_RE, _PERCENT_DOUBLE_RE, _PLAIN_BRACE_RE,
        _SQUARE_BRACKET_RE,
    )
    # Ordered list (longest / most-specific first).
    families = (
        _HASH_LABEL_BRACE_RE, _PLAIN_BRACE_RE, _ANGLE_TAG_RE,
        _SQUARE_BRACKET_RE, _PERCENT_DOUBLE_RE, _PERCENT_ARG_RE,
        _HASH_NUM_RE,
    )
    spans: list[tuple[int, int, str]] = []
    claimed = [False] * len(text)
    for rx in families:
        for m in rx.finditer(text):
            if any(claimed[m.start():m.end()]):
                continue
            spans.append((m.start(), m.end(), m.group(0)))
            for i in range(m.start(), m.end()):
                claimed[i] = True
    spans.sort(key=lambda x: x[0])
    return [(tok, (s, e)) for s, e, tok in spans]


def _token_signature(tok: str) -> str:
    """Return a stable signature for a token so we can compare
    instances across source and translated text without being
    fooled by a translator changing the display label inside
    a ``{ns#label}`` token.

    For ``{ns#label}`` we hash only the namespace (before ``#``)
    because the label is intentionally translatable. For every
    other family the signature is the token itself.
    """
    if tok.startswith("{") and "#" in tok and tok.endswith("}"):
        ns = tok[1:tok.index("#")]
        return f"{{{ns}#...}}"
    return tok


def _is_hash_label_brace(tok: str) -> bool:
    """Return True if ``tok`` looks like a ``{ns#label}`` token."""
    return (
        bool(tok)
        and tok.startswith("{")
        and tok.endswith("}")
        and "#" in tok
    )


def _altered_replacement(source_token: str, translated_fragment: str) -> str:
    """Produce the correct replacement for an ALTERED issue.

    For the general case the source token wholesale replaces the
    broken translated fragment. That restores every protected
    token family (angle tags, square brackets, percent args, plain
    braces with no ``#``) byte-for-byte.

    For **hash-label braces** we take a smarter path: preserve the
    translator's correctly-translated label on the right side of
    ``#`` and only restore the namespace from the source. This
    stops auto-fix from destroying a correct Arabic / Korean /
    Spanish / … label just because the namespace case changed or
    the AI decided to edit the identifier.

    If either side isn't a hash-label brace the safest thing is
    the existing whole-token replacement — a mismatch here means
    the AI didn't preserve the ``{...#...}`` shape at all, and
    we can't splice what isn't there.
    """
    if _is_hash_label_brace(source_token) and _is_hash_label_brace(translated_fragment):
        # Split on the FIRST '#' on each side — namespaces can
        # contain ':' but never '#', and labels can contain
        # anything except '{' / '}' (guarded by the regex).
        src_ns = source_token[1:source_token.index("#")]
        trl_label = translated_fragment[
            translated_fragment.index("#") + 1 : -1
        ]
        return "{" + src_ns + "#" + trl_label + "}"
    return source_token


# ── Public API ──────────────────────────────────────────────────

def _best_alteration_match(
    trl_tok: str,
    unmatched_source: list[tuple[str, tuple[int, int]]],
) -> Optional[tuple[str, tuple[int, int]]]:
    """Pick the best source token a translated token was probably
    derived from via AI editing.

    Matching rules, tried in order:

    1. Same family prefix AND matching shared prefix of ≥ 3
       characters (so ``{A:X}`` is paired with ``{A:XX}`` and
       ``{B:Y}`` with ``{B:YY}`` — not ``{A:X}`` for both).
    2. Same family prefix AND first character class match.

    Returns ``None`` if no remotely plausible match exists (the
    caller will classify as EXTRA_TOKEN).
    """
    if not trl_tok or not unmatched_source:
        return None
    # Rank candidates by longest common prefix.
    best: Optional[tuple[str, tuple[int, int]]] = None
    best_prefix = 0
    for src_tok, span in unmatched_source:
        if not src_tok or src_tok[0] != trl_tok[0]:
            continue
        if src_tok[0] not in "{<[%":
            continue
        # Compute shared leading character run.
        n = 0
        for a, b in zip(src_tok, trl_tok):
            if a != b:
                break
            n += 1
        if n > best_prefix:
            best = (src_tok, span)
            best_prefix = n
    # Require at least 2 chars in common (family char + one more)
    # to avoid pairing e.g. ``{FOO}`` with ``{BAR}``.
    if best is not None and best_prefix >= 2:
        return best
    # Fallback: same family prefix, take the first unmatched one.
    for src_tok, span in unmatched_source:
        if src_tok and src_tok[0] == trl_tok[0] and trl_tok[0] in "{<[%":
            return (src_tok, span)
    return None


def scan_entry(source: str, translated: str) -> ScanResult:
    """Compare two strings and return a :class:`ScanResult`.

    Never raises. Both inputs can be empty.

    De-duplication contract
    -----------------------
    When a translated token is the AI-altered version of a source
    token (e.g. source ``{Key:Run}`` → translation ``{Key:Running}``),
    we report ONE ``ALTERED`` issue — not ``MISSING + EXTRA_TOKEN``.
    The pairing pass below greedily matches each translated token
    that isn't already in the source against an unmatched source
    token of the same family + best shared-prefix length. Paired
    source tokens are removed from the MISSING pool so auto-fix
    doesn't also append the source token to the end of the line.
    """
    result = ScanResult(source=source, translated=translated)
    if not source and not translated:
        return result

    source_tokens = _find_source_tokens(source)
    translated_tokens = _find_source_tokens(translated)

    # Group source tokens by SIGNATURE so hash-label variants
    # (same namespace, different labels) compare as equivalent.
    from collections import Counter
    source_sigs = Counter(_token_signature(t) for t, _ in source_tokens)
    translated_sigs = Counter(_token_signature(t) for t, _ in translated_tokens)

    # Build a list of "unmatched source tokens" — i.e. source
    # occurrences that aren't accounted for by translation_sigs.
    # We shrink this list as we pair translated tokens against
    # source tokens (ALTERED) so the MISSING pass doesn't
    # double-count the same source token.
    unmatched_source: list[tuple[str, tuple[int, int]]] = []
    remaining_sig_budget = dict(source_sigs)
    for tok, span in source_tokens:
        sig = _token_signature(tok)
        used = min(translated_sigs.get(sig, 0), source_sigs[sig])
        # Track how many copies of this signature the translation
        # already has. We want to keep (count - used) unmatched.
        # This loop preserves per-span order by only keeping one
        # entry in unmatched for each "excess" occurrence.
        if remaining_sig_budget.get(sig, 0) > 0:
            translated_count = translated_sigs.get(sig, 0)
            if translated_count <= 0:
                # Translation has zero of this sig — this token is
                # unmatched.
                unmatched_source.append((tok, span))
            else:
                # Translation has at least one match; consume it.
                translated_sigs[sig] -= 1
        remaining_sig_budget[sig] = remaining_sig_budget.get(sig, 0) - 1

    # 2. ALTERED — a token in the translation that doesn't match
    # any signature in the source. Pair each against its best
    # available source candidate. Paired source tokens are
    # consumed so they don't also get reported as MISSING.
    altered_issues: list[PlaceholderIssue] = []
    extra_issues: list[PlaceholderIssue] = []
    for tok, span in translated_tokens:
        sig = _token_signature(tok)
        if source_sigs.get(sig, 0) > 0:
            source_sigs[sig] -= 1
            continue
        match = _best_alteration_match(tok, unmatched_source)
        if match is not None:
            src_tok, src_span = match
            unmatched_source.remove(match)
            altered_issues.append(PlaceholderIssue(
                kind=IssueKind.ALTERED,
                source_token=src_tok,
                translated_fragment=tok,
                source_span=src_span,
                translated_span=span,
                auto_fixable=True,
            ))
        else:
            extra_issues.append(PlaceholderIssue(
                kind=IssueKind.EXTRA_TOKEN,
                source_token="",
                translated_fragment=tok,
                translated_span=span,
                auto_fixable=False,
            ))

    # 1. MISSING — every remaining unmatched source token.
    for src_tok, span in unmatched_source:
        result.issues.append(PlaceholderIssue(
            kind=IssueKind.MISSING,
            source_token=src_tok,
            source_span=span,
            auto_fixable=True,
        ))

    # Ordering: MISSING first (user fixes absences before
    # substitutions), then ALTERED, then EXTRA.
    result.issues.extend(altered_issues)
    result.issues.extend(extra_issues)

    # 3. LEAKED_SENTINEL — a tokenizer sentinel that decode_after
    # didn't clean up (shouldn't happen in practice, but we catch
    # for QA). The tokenizer's decode is tolerant to noise, so
    # the only way a sentinel leaks is if the AI emitted a bogus
    # one the decoder skipped.
    for m in _SENTINEL_LEAK_RE.finditer(translated):
        result.issues.append(PlaceholderIssue(
            kind=IssueKind.LEAKED_SENTINEL,
            source_token="",
            translated_fragment=m.group(0),
            translated_span=(m.start(), m.end()),
            auto_fixable=True,
        ))

    return result


def autofix_entry(
    source: str,
    translated: str,
    result: Optional[ScanResult] = None,
) -> tuple[str, int]:
    """Apply every auto-fixable issue to ``translated`` in place.

    Returns ``(fixed_text, n_fixes_applied)``. Only touches the
    specific substring of each fix — translated prose outside the
    placeholder region is untouched byte-for-byte. EXTRA_TOKEN
    issues are skipped (flagged, not fixed).
    """
    if result is None:
        result = scan_entry(source, translated)
    if not result.issues:
        return translated, 0

    text = translated
    fixes = 0

    # 1. Apply ALTERED fixes from RIGHT to LEFT so earlier spans
    # stay valid while we splice. Altered-token spans are in the
    # TRANSLATED text so we modify that.
    #
    # Hash-label brace special case
    # -----------------------------
    # For tokens of the shape ``{ns#label}``, the label is
    # INTENTIONALLY translatable — it's the user-visible Korean /
    # Arabic / Spanish / … string that renders in-game. An
    # ALTERED issue means the namespace (the part before ``#``)
    # drifted from the source, but the translated label on the
    # other side of ``#`` is what the user actually wants to
    # keep. Blindly replacing the whole token with the source
    # would discard the correct translation and revert to the
    # source language — the exact bug the v1.22.9 user reported
    # for ``{StaticInfo:Knowledge:...#ملكة سلطعون البزموت}``.
    #
    # So we splice: source namespace + "#" + translated label +
    # "}". This restores the broken namespace while preserving
    # the translator's (or AI's) work on the label side.
    altered = [i for i in result.issues if i.kind == IssueKind.ALTERED]
    altered.sort(key=lambda i: -i.translated_span[0])
    for issue in altered:
        start, end = issue.translated_span
        replacement = _altered_replacement(
            issue.source_token, issue.translated_fragment,
        )
        text = text[:start] + replacement + text[end:]
        fixes += 1

    # 2. Strip LEAKED_SENTINELs (also right-to-left to preserve
    # downstream spans). We re-scan after altered fixes so spans
    # point at the current text.
    rescanned = scan_entry(source, text)
    leaked = [i for i in rescanned.issues
              if i.kind == IssueKind.LEAKED_SENTINEL]
    leaked.sort(key=lambda i: -i.translated_span[0])
    for issue in leaked:
        start, end = issue.translated_span
        # Strip the sentinel. If it was surrounded by single
        # spaces, leave one space so the surrounding prose
        # doesn't end up concatenated.
        before = text[:start]
        after = text[end:]
        # Collapse consecutive whitespace introduced by the strip.
        if before.endswith(" ") and after.startswith(" "):
            after = after.lstrip(" ")
        text = before + after
        fixes += 1

    # 3. MISSING — append to end (single leading space unless the
    # text is empty). If we could identify a placement hint in a
    # future version we'd use it, but for now the end-append is
    # safe and non-invasive to translated prose.
    missing = [i for i in rescanned.issues if i.kind == IssueKind.MISSING]
    for issue in missing:
        sep = " " if (text and not text.endswith((" ", "\n"))) else ""
        text = text + sep + issue.source_token
        fixes += 1

    return text, fixes


def scan_batch(pairs: list[tuple[str, str]]) -> list[ScanResult]:
    """Run :func:`scan_entry` on a batch of (source, translated)
    pairs. Returns the results list in the same order.
    """
    return [scan_entry(s, t) for s, t in pairs]


def summarise_by_kind(results: list[ScanResult]) -> dict[str, int]:
    """Count issues by :class:`IssueKind` across a batch."""
    from collections import Counter
    counter: Counter[str] = Counter()
    for r in results:
        for issue in r.issues:
            counter[issue.kind.value] += 1
    return dict(counter)
