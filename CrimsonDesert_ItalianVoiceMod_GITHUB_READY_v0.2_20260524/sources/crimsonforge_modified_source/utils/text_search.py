"""Token-based search matching for the Explorer search bar.

Why this module exists
----------------------
The Explorer search bar previously used a naive ``query in haystack``
substring check. That works for short stems but produces silent false
positives once item names enter the picture: a user typing ``canta``
matched both ``Canta_PlateArmor_Armor`` (the right one) AND
``Eccanta_PlateArmor_Armor`` / ``Cantarts_Leather_Armor`` (unrelated
items whose internal names happen to contain the 5-letter sequence
``canta``). The user's natural-language phrase ``canta plate armor``
also matched ``eccanta plate armor`` for the same reason — substring
matching has no concept of word boundaries.

What it does
------------
Both the query and the per-row search corpus are split into
LOWERCASE TOKENS using one uniform tokenizer that handles whitespace,
underscores, hyphens, slashes, periods, and CamelCase boundaries. A
row is considered a hit when every query token is a prefix of at
least one corpus token (AND semantics).

The CamelCase split is essential — game-data internal names look
like ``Canta_PlateArmor_Armor``. Without splitting between ``Plate``
and ``Armor``, the token would be ``platearmor`` and the user's
``plate armor`` (with a space) wouldn't match.

The prefix rule preserves the natural feel of starts-with search
without leaking into the middle of unrelated tokens:

  * ``canta`` matches the token ``canta`` (exact prefix).
  * ``canta`` matches ``cantarts`` — ``canta`` is a prefix of it.
  * ``canta`` does NOT match ``eccanta`` — the prefix isn't at the
    start of the token.

For most modders the surviving false positive (``cantarts`` matches
``canta``) is acceptable: typing more of the name (``canta_p`` or
``canta plate``) immediately resolves it. The original ``canta``
matched ``Eccanta`` AND ``Cantarts``; this version drops the
``Eccanta`` hit, which is the one users actually complained about.
"""
from __future__ import annotations

import re

# Whitespace and the common name / path separators we always want to
# split on. Backslash is intentionally included for Windows paths.
_SEP_RE = re.compile(r"[\s_./\\\-:|]+")

# CamelCase boundary detector: split between a lowercase / digit and
# the uppercase that follows it (``PlateArmor`` -> ``Plate|Armor``)
# AND between two uppercase letters when the second is followed by a
# lowercase (``URLPath`` -> ``URL|Path``). Standard pattern used by
# Google's open-source style guides and by ``inflection.underscore``.
_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def tokenize(text: str) -> list[str]:
    """Split ``text`` into a list of lowercase tokens.

    Tokenization steps:

    1. Split on whitespace + ``_`` + ``.`` + ``/`` + ``\\`` + ``-`` + ``:`` + ``|``.
    2. Split each chunk on CamelCase boundaries.
    3. Lowercase every result; drop empties.

    Examples::

        tokenize("Canta_PlateArmor_Armor") -> ["canta", "plate", "armor", "armor"]
        tokenize("cd_phw_canta_plate_armor_d.dds") ->
            ["cd", "phw", "canta", "plate", "armor", "d", "dds"]
        tokenize("Mace of Ambition") -> ["mace", "of", "ambition"]
    """
    if not text:
        return []
    out: list[str] = []
    for chunk in _SEP_RE.split(text):
        if not chunk:
            continue
        for sub in _CAMEL_RE.split(chunk):
            if sub:
                out.append(sub.lower())
    return out


def tokens_for(*corpora: str) -> set[str]:
    """Tokenize one or more strings into a single deduplicated set.

    Hot-loop callers (the Explorer's 1.4 M-row filter, the Catalog
    Browser's 19 k-row filter) cache this result per row so each
    keystroke only re-tokenizes the query, never the corpus.
    """
    out: set[str] = set()
    for c in corpora:
        if c:
            out.update(tokenize(c))
    return out


def match_prefilter(query_tokens: list[str],
                    corpus_tokens: set[str]) -> bool:
    """Fast inner predicate for already-tokenized inputs.

    Returns True when every token in ``query_tokens`` is a prefix of
    at least one token in ``corpus_tokens``. The caller is responsible
    for deciding what an empty query / empty corpus should mean —
    this function says ``True`` for an empty query (no constraints)
    and ``False`` for a non-empty query against an empty corpus.
    """
    if not query_tokens:
        return True
    if not corpus_tokens:
        return False
    for qt in query_tokens:
        for ct in corpus_tokens:
            if ct.startswith(qt):
                break
        else:
            return False
    return True


def match(query: str, *corpora: str) -> bool:
    """Return True when every token in ``query`` is a prefix of some
    token extracted from one of ``corpora``.

    ``corpora`` accepts multiple strings so the caller doesn't have
    to pre-join the file path with the alias terms. Empty / missing
    strings are simply skipped.

    An empty query matches everything (so an empty filter doesn't
    hide rows). An empty corpus rejects every non-empty query.

    Note: this convenience wrapper re-tokenizes the corpus on every
    call. Hot-loop callers should use :func:`tokens_for` to cache the
    corpus token set on their row objects and call
    :func:`match_prefilter` instead.
    """
    q_tokens = tokenize(query)
    if not q_tokens:
        return True
    corpus: set[str] = set()
    for c in corpora:
        if c:
            corpus.update(tokenize(c))
    if not corpus:
        return False
    for qt in q_tokens:
        if not any(ct.startswith(qt) for ct in corpus):
            return False
    return True


# ───────────────────────────────────────────────────────────────────
# Enterprise query parser — boolean operators, phrases, fields,
# wildcards. Used by the Explorer's advanced search.
# ───────────────────────────────────────────────────────────────────

# Field qualifiers we recognise (case-insensitive).
_KNOWN_FIELDS = {"ext", "name", "path", "content", "size", "type"}


class Clause:
    """A single search clause: one of phrase / token / wildcard / field.

    Attributes:
        kind: 'phrase', 'token', 'wildcard', 'field'
        value: the literal text (lowercased for case-insensitive)
        field: only set for kind='field' — the field name
        negated: if True, this clause MUST NOT match
        compiled: pre-compiled regex for wildcard clauses (None
            otherwise). Cached once at parse time so the explorer's
            1.4M-row loop doesn't recompile fnmatch's regex per row.
    """
    __slots__ = ("kind", "value", "field", "negated", "compiled")

    def __init__(self, kind: str, value: str, field: str = "", negated: bool = False):
        self.kind = kind
        self.value = value
        self.field = field
        self.negated = negated
        self.compiled = None
        if kind == "wildcard":
            import fnmatch as _fn
            import re as _re
            try:
                self.compiled = _re.compile(
                    _fn.translate(value), _re.IGNORECASE,
                )
            except Exception:
                # Malformed glob — leave compiled=None so callers
                # can fall back to fnmatch.fnmatch (which still
                # produces a sensible 'no match' for bad input).
                self.compiled = None

    def __repr__(self) -> str:
        n = "!" if self.negated else ""
        if self.field:
            return f"{n}{self.field}:{self.value}"
        return f"{n}<{self.kind}:{self.value}>"


class ParsedQuery:
    """A parsed query: list of OR'd groups, each group is AND of clauses.

    For example, the query ``canta plate OR mace AND ambition``
    parses to two AND-groups:

        [[canta, plate], [mace, ambition]]

    A row matches when ANY group matches (i.e., when ALL clauses in
    that group match). Negated clauses (``-eccanta`` or ``NOT eccanta``)
    must NOT match for the group to qualify.

    Empty query → matches everything.
    """
    __slots__ = ("groups", "raw")

    def __init__(self, groups: list[list[Clause]], raw: str = ""):
        self.groups = groups
        self.raw = raw

    def is_empty(self) -> bool:
        return not self.groups or all(not g for g in self.groups)

    def needs_content(self) -> bool:
        """Return True if any clause uses field='content' — these are slow."""
        return any(c.field == "content" for g in self.groups for c in g)


def _scan_query(text: str) -> list[str]:
    """Tokenize a query string preserving phrases and operators.

    Splits on whitespace but respects double-quoted phrases as single
    tokens. Operators (``AND``, ``OR``, ``NOT``) survive as separate
    tokens. Field-qualified values like ``ext:.dds`` stay glued.
    """
    tokens: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c.isspace():
            i += 1
            continue
        if c == '"' or c == "'":
            quote = c
            j = i + 1
            while j < n and text[j] != quote:
                j += 1
            tokens.append(text[i:j + 1] if j < n else text[i:])
            i = j + 1
        else:
            j = i
            # Read until next whitespace, but allow ":" mid-token for fields
            while j < n and not text[j].isspace():
                # Special: stop at quote that starts a value (field:"value")
                j += 1
            tokens.append(text[i:j])
            i = j
    return tokens


def parse_query(text: str) -> ParsedQuery:
    """Parse an enterprise search query into ``ParsedQuery``.

    Syntax:
        token              — prefix-matched against name/path tokens
        "exact phrase"     — substring match (kept verbatim, lowercased)
        *.dds / cd_phm_*   — fnmatch glob (any token containing * or ?)
        ext:.dds           — extension filter
        name:hel_0363      — filename-only substring
        path:character     — path-only substring
        content:bytes      — search inside file contents (slow)
        size:>1mb          — file size > 1 MiB
        size:<500kb        — file size < 500 KiB
        AND / (space)      — both must match (default)
        OR                 — either matches (creates a new AND-group)
        NOT foo / -foo     — negate the next clause
    """
    if not text or not text.strip():
        return ParsedQuery([], raw=text or "")

    raw_tokens = _scan_query(text)
    groups: list[list[Clause]] = [[]]
    pending_negation = False

    for tok in raw_tokens:
        if not tok:
            continue
        upper = tok.upper()
        if upper == "OR":
            if groups[-1]:
                groups.append([])
            continue
        if upper == "AND":
            continue  # default semantics; ignore the keyword
        if upper == "NOT":
            pending_negation = True
            continue
        negated = pending_negation
        pending_negation = False

        if tok.startswith("-") and len(tok) > 1:
            negated = True
            tok = tok[1:]

        clause = _make_clause(tok, negated)
        if clause is None:
            continue
        groups[-1].append(clause)

    # Drop empty trailing groups (e.g. user typed "foo OR ")
    groups = [g for g in groups if g]
    return ParsedQuery(groups, raw=text)


def _make_clause(tok: str, negated: bool) -> Clause | None:
    """Convert one raw token into a Clause."""
    if not tok:
        return None

    # Quoted phrase: "exact" or 'exact'
    if (len(tok) >= 2 and tok[0] in '"\'' and tok[-1] == tok[0]):
        inner = tok[1:-1]
        if not inner:
            return None
        return Clause("phrase", inner.lower(), negated=negated)

    # Field qualifier: foo:bar
    if ":" in tok:
        head, _, tail = tok.partition(":")
        head_l = head.lower()
        if head_l in _KNOWN_FIELDS and tail:
            # Strip surrounding quotes from the value
            if len(tail) >= 2 and tail[0] in '"\'' and tail[-1] == tail[0]:
                tail = tail[1:-1]
            return Clause("field", tail.lower(), field=head_l, negated=negated)

    # Wildcard token
    if "*" in tok or "?" in tok:
        return Clause("wildcard", tok.lower(), negated=negated)

    # Bare extension shortcut: tokens like ``.pac`` (3-15 chars,
    # starts with a single ``.``, no further dots) are promoted to
    # an EXTENSION match rather than a plain substring. Substring
    # would let ``.pac`` match files named ``*.paccd`` /
    # ``*.pac_xml`` / ``*.paccdesc`` (the substring ``.pac`` appears
    # inside all of them), which is almost never what the user
    # means when they type ``.pac damian`` or ``hel ext-style .pac``.
    # The bare-token single-keystroke fast path in
    # ``tab_explorer._refilter`` already handles this for the
    # ``.pac`` (no space) case; this branch is the equivalent for
    # multi-token queries that go through the complex evaluator.
    if (len(tok) >= 2 and len(tok) <= 15
            and tok.startswith(".") and tok.count(".") == 1
            and "*" not in tok and "?" not in tok
            and ":" not in tok):
        return Clause("field", tok.lower(), field="ext", negated=negated)

    # Plain token — case-insensitive prefix match against tokens
    return Clause("token", tok.lower(), negated=negated)


def _size_to_bytes(spec: str) -> int | None:
    """Parse '1mb', '500kb', '2gb', '100' (bytes) into integer bytes."""
    spec = spec.strip().lower()
    if not spec:
        return None
    units = {"kb": 1024, "k": 1024, "mb": 1024 ** 2, "m": 1024 ** 2,
             "gb": 1024 ** 3, "g": 1024 ** 3, "b": 1, "": 1}
    for u in sorted(units, key=len, reverse=True):
        if spec.endswith(u):
            num_part = spec[:-len(u)] if u else spec
            try:
                return int(float(num_part) * units[u])
            except ValueError:
                return None
    return None


def evaluate_clause(clause: Clause, *, name: str, path: str,
                    ext: str, tokens: set[str], extra: str,
                    size: int = 0, type_desc: str = "",
                    content_loader=None) -> bool:
    """Evaluate a single clause against a row's data.

    All inputs (except size and content_loader) should already be
    lowercased for the case-insensitive match.

    ``content_loader`` is a zero-arg callable returning the file's
    raw bytes. Only invoked when the clause requires content search,
    so the hot path stays cheap.
    """
    import fnmatch as _fn
    val = clause.value
    fld = clause.field
    kind = clause.kind

    if kind == "phrase":
        ok = val in path or val in extra or val in name
        return ok != clause.negated

    if kind == "wildcard":
        # Prefer the parse-time pre-compiled regex (see Clause.__init__).
        # Falls back to fnmatch.fnmatch only if compilation failed —
        # e.g. for malformed globs we still produce a defined result.
        cre = getattr(clause, "compiled", None)
        if cre is not None:
            ok = cre.match(path) is not None or cre.match(name) is not None
        else:
            ok = _fn.fnmatch(path, val) or _fn.fnmatch(name, val)
        return ok != clause.negated

    if kind == "field":
        if fld == "ext":
            wanted = val if val.startswith(".") else f".{val}"
            return (ext == wanted) != clause.negated
        if fld == "name":
            return (val in name) != clause.negated
        if fld == "path":
            return (val in path) != clause.negated
        if fld == "type":
            return (val in type_desc.lower()) != clause.negated
        if fld == "size":
            op = ">"
            num_str = val
            if val.startswith((">=", "<=")):
                op = val[:2]
                num_str = val[2:]
            elif val[:1] in "><":
                op = val[0]
                num_str = val[1:]
            limit = _size_to_bytes(num_str)
            if limit is None:
                return not clause.negated  # malformed → permissive
            ok = (op == ">"  and size >  limit) or \
                 (op == "<"  and size <  limit) or \
                 (op == ">=" and size >= limit) or \
                 (op == "<=" and size <= limit)
            return ok != clause.negated
        if fld == "content":
            if content_loader is None:
                return False  # caller didn't supply loader → no match
            try:
                data = content_loader()
            except Exception:
                return clause.negated
            ok = val.encode("utf-8", "ignore") in data
            return ok != clause.negated
        return not clause.negated  # unknown field — permissive

    # token: prefix-match against any token in the corpus
    if kind == "token":
        for ct in tokens:
            if ct.startswith(val):
                return not clause.negated
        # Fall back to substring on extra — covers raw paths users paste
        if val in path or val in extra:
            return not clause.negated
        return clause.negated

    return not clause.negated


def match_query(parsed: ParsedQuery, *, name: str, path: str,
                ext: str, tokens: set[str], extra: str,
                size: int = 0, type_desc: str = "",
                content_loader=None) -> bool:
    """Match a row against a ``ParsedQuery``.

    Returns True if ANY OR-group fully matches (all its clauses
    evaluate True). Empty query matches everything.
    """
    if parsed.is_empty():
        return True
    for group in parsed.groups:
        if not group:
            continue
        if all(evaluate_clause(c, name=name, path=path, ext=ext,
                               tokens=tokens, extra=extra, size=size,
                               type_desc=type_desc, content_loader=content_loader)
               for c in group):
            return True
    return False
