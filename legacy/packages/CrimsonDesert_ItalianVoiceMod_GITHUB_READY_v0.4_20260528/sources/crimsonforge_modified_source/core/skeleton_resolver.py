"""Shared skeleton (.pab) resolver for mesh + animation FBX export.

Why this module exists
----------------------
Pearl Abyss character skeletons are **shared at the class level**,
not per-mesh. Every Kliff armour/cloak/boot PAC ships with its own
geometry but points at the single ``phm_01.pab`` rig. Likewise every
Damiane asset points at ``phw_01.pab``.

Pre-v1.22.4 we had two resolvers:

  * A **correct** prefix-based one in ``ui/tab_explorer.py`` used by
    the PAA animation FBX export path.
  * A **broken** sibling-basename one I added in v1.22.3 to the PAC
    mesh FBX export path. That one searches for a ``.pab`` whose
    filename matches the PAC's basename — which is guaranteed to
    miss for character meshes.

Reports from real users (character-mesh FBX export failing every
time) made the mesh-path oversight obvious. This module lifts the
correct logic into one place so both FBX export paths use it.

Prefix ecosystem
----------------
The prefix after ``cd_`` identifies the **rig family**, not the
individual asset. Verified against real game archives:

  ================   =======================================
  Prefix             Typical skeleton / notes
  ================   =======================================
  cd_phm_*           phm_01.pab   hero male (Kliff, 178 bones)
  cd_phw_*           phw_01.pab   hero female (Damiane)
  cd_ptm_*           ptm_01.pab   template male (169 bones)
  cd_ptw_*           ptw_01.pab   template female (rare)
  cd_pfm_*           pfm_01.pab   face male
  cd_pfw_*           pfw_01.pab   face female
  cd_ppdm_*          ppdm_01.pab  pair-detail male (eye variants)
  cd_ppdw_*          ppdw_01.pab  pair-detail female
  cd_pgm_*           pgm_01.pab   gear male
  cd_pgw_*           pgw_01.pab   gear female
  cd_prh_*           prh_01.pab   player ride horse
  cd_rd_*            rd_*.pab     ride/mount variants
  nhm_*              nhm_01.pab   NPC human male   (no cd_)
  nhw_*              nhw_01.pab   NPC human female (no cd_)
  cd_ngm_*           ngm_01.pab   NPC goblin male
  ================   =======================================

Animation files often embed the rig prefix mid-filename
(``cd_seq_*_phm1_*`` / ``*_phw_*``) rather than at the start. The
resolver accepts an explicit list of sub-patterns for those cases.

API shape
---------
The resolver is a pure module — no Qt, no UI, no disk I/O except
through the small ``SkeletonVfs`` protocol that wraps the existing
``VfsManager.load_pamt`` / ``read_entry_data`` pair. That keeps the
core logic unit-testable against synthetic fixtures.

Top-level entry points:

  detect_rig_prefix(filename) -> str | None
      Pure string match. Given an asset filename, return the
      canonical 3- or 4-letter rig prefix (``'phm'``, ``'ppdm'``,
      ``'rd'``, …) or ``None`` when no pattern matches.

  rank_skeleton_candidates(rig_prefix, pab_paths, asset_path) -> list[str]
      Order a list of ``.pab`` paths from best to worst candidate
      for the asset under consideration. Does not touch the disk;
      ranking is purely lexical + structural.

  resolve_skeleton(asset_path, vfs, manual_override=None) -> SkeletonResolution
      Full resolution: runs the prefix detect, ranks candidates
      found through the VFS, loads the chosen PAB, and returns a
      dataclass with the parsed skeleton plus enough metadata for
      the UI to explain what happened.

UI layer notes
--------------
The UI should:
  1. Always call :func:`resolve_skeleton` first.
  2. If ``resolution.skeleton`` is populated, use it directly.
  3. If it's None, surface ``resolution.reason`` to the user and
     offer a manual-browse fallback that calls
     :func:`load_skeleton_from_path` with the user-picked path.
  4. Remember the manual override via ``rig_prefix`` in config so
     the next export for the same class skips the dialog.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional, Protocol, Sequence


# ── Prefix detection ─────────────────────────────────────────────────

# Ordered list of (canonical_prefix, substrings_that_trigger_it).
# Order matters — more specific patterns go first so 'pgm' doesn't
# eat a string that really meant 'pgmX' for some future rig.
#
# Each pattern is matched as a **substring** of the lowered filename
# (not a regex) to keep the logic fast and the false-positive surface
# small. A hyphen-style boundary (`_phm_`, `_phm1_`, …) is required
# on either side when the prefix appears mid-string so we don't
# match 'phm' inside 'symphm_' or similar.
_PREFIX_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    # 4-letter prefixes have to come before the 3-letter ones that
    # are substrings of them — 'ppdm' must win over 'pdm' if we ever
    # add 'pdm', and 'ppdm' before 'ppd'.
    ("ppdm", ("cd_ppdm_", "_ppdm_", "_ppdm1_")),
    ("ppdw", ("cd_ppdw_", "_ppdw_", "_ppdw1_")),
    # 3-letter prefixes ordered roughly by how common they are.
    ("phm",  ("cd_phm_",  "_phm_",  "_phm1_",  "_phm2_",  "_phm3_",  "_phm8_")),
    ("phw",  ("cd_phw_",  "_phw_",  "_phw1_",  "_phw2_",  "_phw3_",  "_phw8_")),
    ("ptm",  ("cd_ptm_",  "_ptm_",  "_ptm1_",  "_ptm2_")),
    ("ptw",  ("cd_ptw_",  "_ptw_",  "_ptw1_")),
    ("pfm",  ("cd_pfm_",  "_pfm_",  "_pfm1_")),
    ("pfw",  ("cd_pfw_",  "_pfw_",  "_pfw1_")),
    ("pgm",  ("cd_pgm_",  "_pgm_",  "_pgm1_")),
    ("pgw",  ("cd_pgw_",  "_pgw_",  "_pgw1_")),
    ("prh",  ("cd_prh_",  "_prh_",  "cd_rd_prh_")),
    ("ngm",  ("cd_ngm_",  "_ngm_")),
    ("ngw",  ("cd_ngw_",  "_ngw_")),
    # NPC-family prefixes that appear without the 'cd_' wrapper.
    ("nhm",  ("nhm_",)),
    ("nhw",  ("nhw_",)),
    # Ride/mount catch-all.
    ("rd",   ("cd_rd_",)),
)


# Expose the ordered prefix list for tests and for the manual-browse
# dialog (which shows an ordered dropdown of rig classes).
KNOWN_RIG_PREFIXES: tuple[str, ...] = tuple(p for p, _ in _PREFIX_PATTERNS)


def detect_rig_prefix(filename: str) -> Optional[str]:
    """Detect the rig class prefix from an asset filename.

    Accepts both the basename and full paths. Case-insensitive.
    Returns the canonical prefix (``'phm'``, ``'ppdm'``, ...) or
    ``None`` when no pattern matches.

    Pattern matching is in two phases:

    1. **Bare-rig start match** — when the filename *starts with*
       ``<prefix>_`` (e.g. ``phm_01.pab``, ``nhm_guard.pac``) we
       return that prefix directly. This is the canonical form
       used for shared class rigs.

    2. **Substring match** — asset filenames usually embed the
       prefix with a boundary (``cd_phm_``, ``_phm_``, …). The
       ordered pattern list handles these, with more specific
       (4-letter) prefixes checked before shorter ones so ``ppdm``
       doesn't get out-muscled by a hypothetical ``pdm``.

    This function is a pure string operation — no disk access.
    """
    if not filename:
        return None
    name = os.path.basename(filename).lower()

    # Phase 1: bare start-of-name match. Iterate in the same order as
    # _PREFIX_PATTERNS so 4-letter prefixes (ppdm, ppdw) get first
    # refusal over 3-letter ones (ptm, pgm).
    for prefix, _ in _PREFIX_PATTERNS:
        if name.startswith(prefix + "_"):
            return prefix

    # Phase 2: substring match for embedded forms like cd_phm_* or
    # mid-string _phm_.
    for prefix, patterns in _PREFIX_PATTERNS:
        for pattern in patterns:
            if pattern in name:
                return prefix
    return None


# ── Candidate ranking ────────────────────────────────────────────────

_PAB_EXT_RE = re.compile(r"\.pab$", re.IGNORECASE)


def _same_directory(asset_path: str, pab_path: str) -> bool:
    """True when the asset and PAB share the same archive directory."""
    a = asset_path.replace("\\", "/").rsplit("/", 1)
    p = pab_path.replace("\\", "/").rsplit("/", 1)
    if len(a) < 2 or len(p) < 2:
        return False
    return a[0].lower() == p[0].lower()


def rank_skeleton_candidates(
    rig_prefix: Optional[str],
    pab_paths: Iterable[str],
    asset_path: str = "",
) -> list[str]:
    """Rank candidate .pab paths from best to worst for this asset.

    Ranking rules (ties broken by the next rule down):

    1.  Filename starts with ``<rig_prefix>_`` (exact class match).
        Within this bucket, prefer the shortest basename — ``phm_01.pab``
        wins over ``phm_01_lod2.pab`` which wins over
        ``phm_01_experimental.pab``. This is the key rule that
        picks the canonical shared rig.
    2.  Lives in the same archive directory as the asset.
    3.  Shortest overall filename (rough "most generic" heuristic).
    4.  Lexical order (deterministic tiebreak so tests stay stable).

    PAB paths that aren't valid strings are silently dropped.
    Duplicates are collapsed preserving first-occurrence order.
    """
    unique: list[str] = []
    seen: set[str] = set()
    for p in pab_paths:
        if not isinstance(p, str) or not p:
            continue
        key = p.replace("\\", "/").lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(p.replace("\\", "/"))

    def _rank_key(path: str) -> tuple:
        base = os.path.basename(path).lower()
        prefix_match = bool(
            rig_prefix and base.startswith(rig_prefix.lower() + "_")
        )
        same_dir = bool(asset_path and _same_directory(asset_path, path))
        return (
            0 if prefix_match else 1,
            0 if same_dir else 1,
            len(base),
            base,
        )

    return sorted(unique, key=_rank_key)


# ── VFS protocol ─────────────────────────────────────────────────────

class SkeletonVfs(Protocol):
    """Minimum VFS surface required to resolve a skeleton.

    ``VfsManager`` from :mod:`core.vfs_manager` already satisfies
    this protocol. The tests use a tiny fake that wraps a path→bytes
    dict, so production code and tests share the same resolver.
    """

    def list_pab_paths(self) -> list[str]:
        """Return every ``.pab`` path visible through the VFS."""
        ...

    def read_pab_bytes(self, path: str) -> bytes:
        """Return the raw bytes of the PAB at *path*.

        Raises ``LookupError`` when the path isn't known.
        """
        ...


class VfsManagerAdapter:
    """Wrap a real ``VfsManager`` so it conforms to :class:`SkeletonVfs`.

    The adapter scans every loaded PAMT for ``.pab`` entries once
    and caches the result. A second call with a different VfsManager
    instance would need its own adapter (the cache is per-instance).

    Also caches PARSED skeletons, keyed by VFS path. Without this
    cache, ``resolve_skeleton`` with palette validation re-parses
    every PAB candidate on every export call (50+ PABs in a typical
    install, ~5-15 ms each). With the cache, the first export
    populates it once, every subsequent export is essentially free.
    """

    def __init__(self, vfs):
        self._vfs = vfs
        self._pab_index: dict[str, object] | None = None   # path -> entry
        # path -> parsed Skeleton object. Lazily populated by
        # ``read_parsed_pab`` so we never parse the same PAB twice.
        self._parsed_pab_cache: dict[str, object] = {}

    def _ensure_index(self) -> dict[str, object]:
        if self._pab_index is not None:
            return self._pab_index

        index: dict[str, object] = {}
        pamt_cache = getattr(self._vfs, "_pamt_cache", None) or {}
        for _group, pamt_data in pamt_cache.items():
            for entry in getattr(pamt_data, "file_entries", []):
                path = getattr(entry, "path", "")
                if path and path.lower().endswith(".pab"):
                    index[path.replace("\\", "/")] = entry
        self._pab_index = index
        return index

    def list_pab_paths(self) -> list[str]:
        return list(self._ensure_index().keys())

    def read_pab_bytes(self, path: str) -> bytes:
        index = self._ensure_index()
        key = path.replace("\\", "/")
        entry = index.get(key)
        if entry is None:
            # Also accept basename-only queries for convenience.
            base_key = os.path.basename(key).lower()
            for k, v in index.items():
                if os.path.basename(k).lower() == base_key:
                    entry = v
                    break
        if entry is None:
            raise LookupError(f"PAB not found in VFS: {path}")
        return self._vfs.read_entry_data(entry)

    def read_parsed_pab(self, path: str):
        """Return a parsed ``Skeleton`` for the PAB at ``path``,
        cached. Returns ``None`` on read or parse failure.

        The cache is per-adapter, lifetime = adapter lifetime. This
        is what makes ``resolve_skeleton`` with ``pac_bytes`` fast on
        repeat calls — the first export pays for parsing every
        candidate PAB once; subsequent exports through the same
        adapter are O(N_candidates) bytes scans against pre-parsed
        skeletons (no I/O, no re-parse).
        """
        key = path.replace("\\", "/")
        cached = self._parsed_pab_cache.get(key)
        if cached is not None:
            return cached
        try:
            raw = self.read_pab_bytes(path)
        except Exception:
            self._parsed_pab_cache[key] = None
            return None
        try:
            parsed = _parse_pab(raw, path)
        except Exception:
            self._parsed_pab_cache[key] = None
            return None
        self._parsed_pab_cache[key] = parsed
        return parsed


# ── Resolution entry point ───────────────────────────────────────────

@dataclass
class SkeletonResolution:
    """Result of a full skeleton resolution attempt.

    ``skeleton`` is the parsed :class:`core.skeleton_parser.Skeleton`
    when resolution succeeded, ``None`` otherwise. ``source`` is one
    of three strict, deterministic values:

      * ``"manual"``         — caller named the .pab via override
      * ``"palette_match"``  — winner picked by PAC section-0 hash
                               overlap against PAB bone-hash tables
      * ``"prefix_match"``   — winner picked by rig prefix encoded in
                               the asset filename (no pac_bytes case)

    There is no ``sibling_path`` or ``fallback_scan`` — the resolver
    refuses rather than guess. When it can't pick deterministically
    ``skeleton`` is ``None`` and ``reason`` carries the diagnostic.

    ``pab_path`` is the VFS-relative path of the chosen rig, empty
    when nothing was picked. Useful for logging and for config
    persistence (remembering per-prefix manual choices).
    """
    skeleton: object = None                # core.skeleton_parser.Skeleton
    pab_path: str = ""
    source: str = ""
    reason: str = ""
    rig_prefix: Optional[str] = None
    candidates_tried: list[str] = field(default_factory=list)


def _parse_pab(raw: bytes, source_path: str):
    """Thin wrapper around ``core.skeleton_parser.parse_pab``.

    Importing lazily so :mod:`core.skeleton_resolver` stays cheap
    to import (unit tests that only exercise the string logic don't
    pay the cost of loading the PAB parser).
    """
    from core.skeleton_parser import parse_pab   # noqa: WPS433 — lazy import
    return parse_pab(raw, source_path)


def _extract_pac_palette(
    pac_bytes: bytes, valid_hash_universe: set,
) -> list[int]:
    """Locate the PAC's per-mesh skinning palette table.

    Scans every 4-aligned u32 boundary for the longest contiguous
    run of values whose low-24 bits are members of
    ``valid_hash_universe`` (typically the union of every known
    PAB's bone-hash table). The palette table is, by construction,
    a contiguous array of bone-hash u32 entries — random PAC bytes
    almost never form 5+ contiguous matches against the union set
    (probability ≈ (~0.001)⁵ even at 16M bone-hash density), so
    the longest run found IS the palette.

    Returns the palette in slot-index order (low-24 bits only), or
    an empty list if no qualifying run exists. The 5-entry minimum
    is the same threshold ``core.mesh_parser._scan_pac_skin_palette``
    uses for its skeleton-bound decode pass.
    """
    n = len(pac_bytes)
    best_off = -1
    best_len = 0
    i = 0
    while i + 4 <= n:
        word = (pac_bytes[i]
                | (pac_bytes[i + 1] << 8)
                | (pac_bytes[i + 2] << 16))
        if word in valid_hash_universe:
            run_len = 0
            j = i
            while j + 4 <= n:
                w2 = (pac_bytes[j]
                      | (pac_bytes[j + 1] << 8)
                      | (pac_bytes[j + 2] << 16))
                if w2 in valid_hash_universe:
                    run_len += 1
                    j += 4
                else:
                    break
            if run_len > best_len:
                best_len = run_len
                best_off = i
            i = j
        else:
            i += 1
    if best_len < 5 or best_off < 0:
        return []
    return [
        pac_bytes[best_off + k * 4]
        | (pac_bytes[best_off + k * 4 + 1] << 8)
        | (pac_bytes[best_off + k * 4 + 2] << 16)
        for k in range(best_len)
    ]


def resolve_skeleton(
    asset_path: str,
    vfs: SkeletonVfs,
    manual_override: Optional[str] = None,
    pac_bytes: Optional[bytes] = None,
) -> SkeletonResolution:
    """Strictly deterministic skeleton resolution — never guesses.

    Four (and only four) explicit paths, each producing a
    deterministic answer or a refusal — never a guess:

      1. **Manual override** — caller named the .pab. Use it.

      2. **Palette-table coverage** — caller provided ``pac_bytes``.
         The PAC's actual per-mesh skinning palette table (a
         contiguous array of u32 bone-hash entries inside section 0)
         is located by the same longest-run scan
         ``mesh_parser._scan_pac_skin_palette`` uses for skin
         decoding. Each PAB is then scored by how many of its
         bone hashes appear in that table. The PAB with maximum
         coverage wins. **No PAB covers any palette entry → REFUSE**
         (the PAC's palette uses hashes that aren't in any installed
         PAB — likely a stale install or an unrecognised mod). **No
         palette table found → REFUSE** (rigid prop; the caller must
         resolve via the engine socket-attach mechanism).

         Why this beats whole-PAC byte-overlap scoring
         ---------------------------------------------
         A previous revision scored PABs against every 4-aligned
         u24 in the PAC. That universe is dominated by vertex
         positions, UVs, and packed normals — random byte noise. On
         a small accessory or monster PAC the noise floor (~5–6%
         per bone) put deep-bone-count alien rigs (golem 419 bones,
         phm_01 434 bones) within a few hits of the correct rig,
         and rank-order ties flipped the wrong winner.

         The palette table itself is small (~30 entries for an
         accessory, ~200 for a body), structurally distinct (every
         entry MUST be a valid bone hash from some PAB), and a
         correct rig covers ~100% of it while every other rig
         covers ~0%. Scoring against the table — not the byte
         soup — restores a clean signal-to-noise ratio.

      3. **Prefix match** — no ``pac_bytes`` given. Detect the rig
         prefix from the asset basename, filter VFS PABs to those
         whose basename starts with ``<prefix>_``, and pick the
         canonical (shortest) one. **No prefix detected → REFUSE**;
         we have no deterministic signal to pick a rig. Multiple
         prefix-PABs are tried in canonical order; first parseable
         non-empty wins.

      4. **Refuse** — no manual override, no ``pac_bytes``, no
         prefix. There is no deterministic signal. Return
         ``skeleton=None`` with an explicit ``reason``; the UI
         surfaces it and the user picks via manual override.

    Never raises — all exceptions are captured into ``reason``.
    """
    resolution = SkeletonResolution(rig_prefix=detect_rig_prefix(asset_path))

    # ── 1) Manual override (explicit, user-chosen) ───────────────────
    if manual_override:
        try:
            raw = vfs.read_pab_bytes(manual_override)
        except Exception as e:
            resolution.reason = (
                f"manual override {manual_override!r} could not be read: {e}"
            )
            return resolution
        try:
            parsed = _parse_pab(raw, manual_override)
        except Exception as e:
            resolution.reason = (
                f"manual override {manual_override!r} failed to parse: {e}"
            )
            return resolution
        if not getattr(parsed, "bones", None):
            resolution.reason = (
                f"manual override {manual_override!r} has zero bones"
            )
            return resolution
        resolution.skeleton = parsed
        resolution.pab_path = manual_override
        resolution.source = "manual"
        return resolution

    # ── Enumerate the PAB universe ───────────────────────────────────
    try:
        all_pabs = vfs.list_pab_paths()
    except Exception as e:
        resolution.reason = f"VFS enumeration failed: {e}"
        return resolution
    if not all_pabs:
        resolution.reason = "no .pab files visible through the VFS"
        return resolution

    ordered = rank_skeleton_candidates(
        resolution.rig_prefix, all_pabs, asset_path=asset_path,
    )
    resolution.candidates_tried = list(ordered)
    get_parsed = getattr(vfs, "read_parsed_pab", None)

    def _load_parsed(candidate: str):
        """Return parsed Skeleton (or None) for a candidate PAB,
        using the adapter cache when available so a multi-PAC export
        pays the parse cost once across calls."""
        if get_parsed is not None:
            return get_parsed(candidate)
        try:
            raw = vfs.read_pab_bytes(candidate)
        except Exception:
            return None
        try:
            return _parse_pab(raw, candidate)
        except Exception:
            return None

    # ── 2) Palette-table coverage (deterministic from PAC bytes) ────
    if pac_bytes is not None:
        # Pre-parse every candidate once and stash for both the
        # union build and the per-PAB coverage score below.
        parsed_by_path: dict[str, object] = {}
        all_hashes_union: set = set()
        for candidate in ordered:
            parsed = _load_parsed(candidate)
            if parsed is None or not getattr(parsed, "bones", None):
                continue
            hashes = getattr(parsed, "bone_hashes", None) or []
            if not hashes:
                continue
            parsed_by_path[candidate] = parsed
            all_hashes_union.update(hashes)

        if not all_hashes_union:
            resolution.reason = (
                "no parseable PAB with bone hashes found in VFS — "
                "cannot decode PAC palette"
            )
            return resolution

        palette = _extract_pac_palette(pac_bytes, all_hashes_union)
        if not palette:
            resolution.reason = (
                "PAC contains no skin-palette table — likely a rigid "
                "prop (must be attached via the engine socket table, "
                "not skin-bound to a skeleton)"
            )
            return resolution
        palette_set = set(palette)

        # Score every parsed PAB by how many palette entries it
        # covers. Iterate in rank order so that genuine ties (rare:
        # only LOD/variant copies of the same rig) resolve to the
        # canonical short-basename winner.
        best_score = 0
        best_candidate: Optional[str] = None
        best_skeleton = None
        for candidate in ordered:
            parsed = parsed_by_path.get(candidate)
            if parsed is None:
                continue
            hashes = getattr(parsed, "bone_hashes", None) or []
            score = sum(1 for h in hashes if h in palette_set)
            if score > best_score:
                best_score = score
                best_candidate = candidate
                best_skeleton = parsed

        if best_candidate is None or best_score == 0:
            resolution.reason = (
                f"PAC palette has {len(palette)} entries but no PAB "
                f"covers any of them — palette uses hashes from a "
                f"rig that isn't loaded in the VFS"
            )
            return resolution

        resolution.skeleton = best_skeleton
        resolution.pab_path = best_candidate
        resolution.source = "palette_match"
        return resolution

    # ── 3) Prefix match (deterministic from filename) ───────────────
    # No pac_bytes was provided. The only deterministic signal left
    # is the rig prefix encoded in the asset filename.
    if not resolution.rig_prefix:
        resolution.reason = (
            f"could not detect rig prefix from {asset_path!r} and "
            f"no pac_bytes were provided — no deterministic signal "
            f"available, refusing to guess"
        )
        return resolution

    target = resolution.rig_prefix.lower() + "_"
    prefix_matches = [
        p for p in ordered
        if os.path.basename(p).lower().startswith(target)
    ]
    if not prefix_matches:
        resolution.reason = (
            f"no PAB starting with {resolution.rig_prefix!r}_ found "
            f"in VFS ({len(all_pabs)} PAB(s) searched)"
        )
        return resolution

    for candidate in prefix_matches:
        parsed = _load_parsed(candidate)
        if parsed is None or not getattr(parsed, "bones", None):
            continue
        resolution.skeleton = parsed
        resolution.pab_path = candidate
        resolution.source = "prefix_match"
        return resolution

    resolution.reason = (
        f"all {len(prefix_matches)} PAB(s) with prefix "
        f"{resolution.rig_prefix!r}_ failed to parse or had zero bones"
    )
    return resolution


def load_skeleton_from_path(path: str, read_bytes: Callable[[], bytes]):
    """Convenience helper for the manual-browse flow.

    Given a user-picked path and a lazy reader that returns the raw
    bytes (from disk or VFS), parse and return the skeleton. Returns
    ``None`` when parsing fails or the skeleton has no bones.
    """
    try:
        raw = read_bytes()
    except Exception:
        return None
    try:
        parsed = _parse_pab(raw, path)
    except Exception:
        return None
    if not getattr(parsed, "bones", None):
        return None
    return parsed
