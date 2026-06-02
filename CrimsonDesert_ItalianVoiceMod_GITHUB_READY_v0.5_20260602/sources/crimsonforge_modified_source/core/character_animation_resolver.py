"""Strict, token-based animation resolver for character mesh exports.

Replaces the legacy substring-search ``_discover_paa_candidates`` in
the explorer, which produced false positives (e.g. surfacing the
sequencer cinematic ``cd_seq_10_damiandinner_phm1_ing_00.paa`` as a
"damian animation" because the substring ``damian`` appears inside
the compound word ``damiandinner``).

The strict 1+1 chain that justifies this resolver
---------------------------------------------------
* Forensic on every PAA in the production VFS: the engine's
  per-character action chart is enumerated by ``actionchart/*.paa_metabin``
  files. Damian has 2,228 such metabins under strict token-match
  (``"damian"`` appearing as a complete underscore-delimited segment),
  which is 95 fewer than substring-match (2,323) — the difference
  is exactly the compound-word false positives.
* Each metabin's basename matches a real PAA in ``character/``
  (verified 2,228 / 2,228 = 100% on damian, see step 60 + 61 forensics
  in test_only/scratch/popsweep/).
* PAC filenames encode the character token in the basename. We
  extract it by removing pure-numeric segments, the ``cd_`` wrapper,
  known rig prefixes (``phw`` / ``phm`` / ``m0002`` / ...), and known
  role keywords (``nude``, ``head``, ``hand``, ...). The remaining
  alphabetic token is the character (``damian`` for the hero PAC,
  ``redriverhog`` for the animal PAC).

What this module does NOT do
----------------------------
* Decode ``character/phw_<charname>.pamt`` (the per-character master
  manifest is encrypted with a different key/scheme than the
  shipped PAMTs and isn't decodable yet — see step 62 forensic).
  When that's cracked it will provide the engine's authoritative
  list and replace this resolver's strict-but-name-based logic.
* Parse PAA bone-name sets to verify they actually skin against
  the rig. Adding that would be a strictly tighter check but
  requires reading every candidate PAA (~5,000+ files); the
  token-match alone already excludes the false positives the
  user reported.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

# Reuse the rig-prefix table the skeleton resolver verified against
# real game data so we never disagree on what counts as a rig.
from core.skeleton_resolver import KNOWN_RIG_PREFIXES


# ── Role-token deny list ──────────────────────────────────────────

# Tokens that name a body part / accessory / variant rather than a
# character. Extracted from the production VFS by walking every
# ``cd_<rig>_*.pac`` filename and frequency-ranking the tokens that
# DON'T identify a character (the other tokens — what's left after
# subtracting these — are the character names). Lower-cased.
_ROLE_TOKENS: frozenset[str] = frozenset({
    # Body / skin slots
    "nude", "uw", "underwear",
    # Head + face parts
    "head", "head_sub", "hair", "fuzz", "beard", "mustache",
    "eye", "eyebrow", "eyeleft", "eyeright", "eyeline", "iris",
    "pupil", "tooth", "teeth", "tongue", "mouth", "ear", "nose",
    # Limbs & torso
    "hand", "foot", "lb", "ub", "shoulder", "sho", "neck",
    # Accessory layers
    "belt", "cloak", "mask", "helm", "helmet", "hood",
    "glove", "jacket", "vest", "bag", "parthide", "inner",
    "sub", "outer", "armor", "weapon", "shield", "saddle",
    # Misc role keywords seen in PAC names
    "test", "lod", "lodalpha", "alpha", "preview",
    # Common per-character qualifier (e.g. "_player" in
    # cd_phw_00_hair_00_0008_01_player.pac which IS a hair
    # variant, not a character — there is no "player" character)
    "player",
})


def _looks_like_model_id(token: str) -> bool:
    """``True`` when ``token`` is a model-id token like ``m0002`` /
    ``r0028`` / ``t0211`` (single letter + 4+ digits). These are
    rig identifiers, not character names.
    """
    if len(token) < 5:
        return False
    return token[0].isalpha() and token[1:].isdigit()


# ── Public dataclass ──────────────────────────────────────────────

@dataclass
class AnimationCandidates:
    """Result of an animation lookup for a character.

    ``character_specific`` is the strict bucket the FBX exporter
    should default to — every entry has the character's token as a
    delimited segment of the PAA filename, which means the engine's
    naming convention itself classifies it as belonging to this
    character.

    ``rig_shared`` are PAAs that match the rig family but DON'T
    name the character (e.g. ``cd_phw_basic_*.paa``). They apply
    to every character with this rig at the engine level. Surfaced
    separately so the UI can show them as a secondary bucket.

    ``rig_token`` and ``char_token`` are the matchers we used —
    useful for the UI to explain to the user what the resolver
    actually searched for.

    ``failure_reason`` is non-empty when extracting the character
    token failed (typical for generic accessory PACs that don't
    name a character at all). The exporter should treat that as
    "rig-shared only" without surfacing an error.
    """
    char_token: str = ""
    rig_token: str = ""
    character_specific: list[str] = field(default_factory=list)
    rig_shared: list[str] = field(default_factory=list)
    failure_reason: str = ""


# ── Public API ────────────────────────────────────────────────────

def extract_character_token(pac_path: str) -> str:
    """Extract the character token from a PAC path.

    Returns the lower-cased token (e.g. ``"damian"``,
    ``"redriverhog"``) or the empty string when the PAC name
    doesn't carry one (generic accessory PACs like
    ``cd_phw_00_head_00_0111.pac``).

    Strict procedure:

      1. Take the basename, drop the extension, lower-case.
      2. Split on ``_``.
      3. Discard tokens that are: empty, ``cd``, pure-numeric
         (e.g. ``00`` / ``0001``), known rig prefixes (``phw``,
         ``phm``, ``ngm``, …), model IDs (``m0002`` / ``r0028`` /
         ``t0211``), known role tokens (``nude``, ``head``,
         ``hand``, …), or shorter than 3 characters (lone letter
         qualifiers like ``w`` / ``z`` / ``t`` that appear as
         role-suffix flags).
      4. The LAST surviving token is the character. When zero
         tokens survive, return ``""``.

    Returning the LAST surviving token (not the first) handles
    both naming conventions:

      * Hero PACs: ``cd_phw_00_nude_00_0001_damian`` → last
        survivor is ``damian``.
      * Monster PACs: ``cd_m0002_00_redriverhog_00_0001`` →
        only survivor is ``redriverhog``.
    """
    base = os.path.splitext(
        os.path.basename(pac_path.replace("\\", "/"))
    )[0].lower()
    parts = base.split("_")
    # The engine's convention is ``cd_<rig>_..._<char>``. Anything
    # not starting with ``cd_`` doesn't carry a character token by
    # this naming scheme — refuse to invent one.
    if not parts or parts[0] != "cd":
        return ""
    candidates: list[str] = []
    rig_set = {p.lower() for p in KNOWN_RIG_PREFIXES}
    for tok in parts[1:]:
        if not tok:
            continue
        if tok.isdigit():
            continue
        if len(tok) < 3:
            continue
        if tok in rig_set:
            continue
        if _looks_like_model_id(tok):
            continue
        if tok in _ROLE_TOKENS:
            continue
        candidates.append(tok)
    return candidates[-1] if candidates else ""


def extract_rig_token(pac_path: str) -> str:
    """Extract the rig token from a PAC path.

    For hero PACs (``cd_phw_*``), returns the rig prefix (``phw``).
    For monster PACs (``cd_m0002_*``), returns the model id
    (``m0002``). For any path without a recognisable rig, returns
    the empty string.

    The rig token is what the rig-shared PAA bucket matches
    against. Unlike :func:`extract_character_token` this rule is
    a positional read (``parts[1]``) rather than a deny-list walk
    because the rig is always the FIRST identifier after ``cd_``.
    """
    base = os.path.splitext(
        os.path.basename(pac_path.replace("\\", "/"))
    )[0].lower()
    parts = base.split("_")
    if len(parts) < 2:
        return ""
    if parts[0] != "cd":
        return ""
    rig = parts[1]
    if not rig:
        return ""
    rig_set = {p.lower() for p in KNOWN_RIG_PREFIXES}
    if rig in rig_set:
        return rig
    if _looks_like_model_id(rig):
        return rig
    # Unknown rig → caller decides whether to ignore or treat the
    # token verbatim. We return it so the caller has the option.
    return rig


def _iter_paa_paths(vfs) -> list[str]:
    """Return every ``.paa`` path the VFS knows about.

    Pulls from ``VfsManager._pamt_cache`` directly because that's
    the live game state. Returns lower-cased paths so caller
    comparisons stay case-insensitive without re-lowering.
    """
    out: list[str] = []
    cache = getattr(vfs, "_pamt_cache", None) or {}
    for _gid, pamt in cache.items():
        for entry in getattr(pamt, "file_entries", []):
            p = getattr(entry, "path", "")
            if not p:
                continue
            pl = p.replace("\\", "/").lower()
            if pl.endswith(".paa"):
                out.append(pl)
    return out


def find_animations_for_character(
    body_pac_path: str,
    vfs,
    *,
    explicit_rig_token: str | None = None,
) -> AnimationCandidates:
    """Resolve the animations that belong to a character.

    Walks every ``.paa`` path in the VFS and buckets each one by
    strict token match:

      * **character_specific** — the character token appears as a
        complete underscore-delimited segment of the PAA's
        basename. This is the engine's own naming convention for
        per-character actions.
      * **rig_shared** — the rig token appears as the second
        underscore-delimited segment of the basename (i.e. the
        path looks like ``<dir>/cd_<rig>_*``) AND the character
        token is NOT in the basename. These animations are part
        of the rig family rather than a specific character.

    Token comparison uses the BASENAME only — directory components
    don't get to drag a PAA into a character's bucket. Substring
    matching is never used; ``cd_seq_10_damiandinner_phm1_ing_00.paa``
    will NOT match damian under this resolver because ``damian``
    isn't a complete token in that filename (the segment is
    ``damiandinner``).

    Returns at most a few thousand entries per bucket. Internally
    sorted so the picker UI sees a deterministic order.

    ``explicit_rig_token`` lets the caller override the rig
    extraction (e.g. when the skeleton resolver picked a rig that
    doesn't match what's encoded in the PAC name). When omitted
    the rig is inferred from the PAC name via
    :func:`extract_rig_token`.
    """
    out = AnimationCandidates(
        char_token=extract_character_token(body_pac_path),
        rig_token=(explicit_rig_token or extract_rig_token(body_pac_path)).lower(),
    )
    if not out.char_token and not out.rig_token:
        out.failure_reason = (
            f"could not extract a character or rig token from "
            f"{os.path.basename(body_pac_path)!r} — name doesn't "
            f"follow the cd_<rig>_..._<char> convention"
        )
        return out

    char_tok = out.char_token
    rig_tok = out.rig_token

    char_bucket: list[str] = []
    rig_bucket: list[str] = []

    for pl in _iter_paa_paths(vfs):
        # Tokenize the basename only — we don't want directory
        # components like 'character/damian/foo.paa' to make every
        # foo.paa a "character" animation.
        stem = os.path.splitext(os.path.basename(pl))[0]
        tokens = stem.split("_")
        if not tokens:
            continue

        in_char = bool(char_tok) and (char_tok in tokens)
        # Rig match: the second token (after cd_) is the rig.
        in_rig = (
            bool(rig_tok)
            and len(tokens) >= 2
            and tokens[0] == "cd"
            and tokens[1] == rig_tok
        )

        if in_char:
            char_bucket.append(pl)
        elif in_rig:
            rig_bucket.append(pl)

    out.character_specific = sorted(char_bucket)
    out.rig_shared = sorted(rig_bucket)
    if not out.character_specific and not out.rig_shared:
        out.failure_reason = (
            f"no PAA matched character token {char_tok!r} or "
            f"rig token {rig_tok!r}"
        )
    return out
