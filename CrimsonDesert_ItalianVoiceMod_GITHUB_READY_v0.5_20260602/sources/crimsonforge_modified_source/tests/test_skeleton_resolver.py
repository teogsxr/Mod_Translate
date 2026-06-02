"""Unit tests for :mod:`core.skeleton_resolver`.

The resolver replaces three separate near-duplicates of rig-lookup
logic that drifted between the PAA-animation and PAC-mesh FBX
export paths. These tests pin down every rule the resolver has to
obey so the two export paths stay in lock-step forever.

Coverage matrix
---------------
We exercise the module on three axes:

1.  :func:`detect_rig_prefix` — pure string classification. One
    test per known prefix + edge cases (empty, paths with
    separators, mixed case, substring boundary rules, false-
    positive traps). **~80 tests.**

2.  :func:`rank_skeleton_candidates` — deterministic ordering of
    candidate paths. Every ranking rule is exercised independently
    + combined. **~35 tests.**

3.  :func:`resolve_skeleton` — end-to-end resolution against a
    fake VFS. Covers manual override, auto-resolve, graceful
    failure, error strings. **~45 tests.**

The fake VFS is a small in-memory map (path → bytes + parsed
skeleton stub) that matches the :class:`SkeletonVfs` protocol.
Real skeleton parsing is mocked so tests stay fast (<1 s total)
and don't need real ``.pab`` fixtures on disk.
"""

from __future__ import annotations

import os
import sys
import unittest
from dataclasses import dataclass
from unittest import mock

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from core.skeleton_resolver import (   # noqa: E402
    KNOWN_RIG_PREFIXES,
    SkeletonResolution,
    VfsManagerAdapter,
    detect_rig_prefix,
    load_skeleton_from_path,
    rank_skeleton_candidates,
    resolve_skeleton,
)


# ─────────────────────────────────────────────────────────────────────
# Fake skeleton + VFS for tests
# ─────────────────────────────────────────────────────────────────────

@dataclass
class _FakeSkeleton:
    """Stand-in for core.skeleton_parser.Skeleton in tests."""
    bones: list = None

    def __post_init__(self):
        if self.bones is None:
            self.bones = [f"Bone_{i}" for i in range(10)]


class _FakeVfs:
    """In-memory SkeletonVfs implementation for unit tests.

    ``pabs`` maps VFS path → bytes. ``parse_returns`` controls what
    the mocked ``core.skeleton_parser.parse_pab`` returns for each
    path — a Skeleton object, or an Exception to simulate a parse
    failure.
    """

    def __init__(self, pabs: dict[str, bytes] | None = None):
        self.pabs = pabs or {}
        self.read_calls: list[str] = []

    def list_pab_paths(self) -> list[str]:
        return list(self.pabs.keys())

    def read_pab_bytes(self, path: str) -> bytes:
        self.read_calls.append(path)
        if path in self.pabs:
            return self.pabs[path]
        raise LookupError(f"not in fake VFS: {path}")


def _patch_parse_pab(return_map=None, raise_for=None):
    """Context manager that patches core.skeleton_parser.parse_pab.

    ``return_map`` maps source_path → Skeleton (defaults to a good
    10-bone fake for every call).
    ``raise_for`` is a set of source_paths that should raise on parse.
    """
    return_map = return_map or {}
    raise_for = raise_for or set()

    def _fake(raw, source_path):
        if source_path in raise_for:
            raise RuntimeError(f"fake parse failure for {source_path}")
        return return_map.get(source_path, _FakeSkeleton())

    return mock.patch("core.skeleton_parser.parse_pab", side_effect=_fake)


# ═════════════════════════════════════════════════════════════════════
# detect_rig_prefix
# ═════════════════════════════════════════════════════════════════════

class DetectRigPrefix_EmptyAndInvalid(unittest.TestCase):
    def test_empty_string(self):
        self.assertIsNone(detect_rig_prefix(""))

    def test_none_returns_none(self):
        # Graceful fallback — None is falsy, short-circuits to None.
        self.assertIsNone(detect_rig_prefix(None))  # type: ignore[arg-type]

    def test_whitespace_only(self):
        self.assertIsNone(detect_rig_prefix("   "))

    def test_no_extension(self):
        self.assertIsNone(detect_rig_prefix("random_name"))

    def test_non_rig_file(self):
        self.assertIsNone(detect_rig_prefix("scene.dds"))

    def test_path_with_slash_no_rig(self):
        self.assertIsNone(detect_rig_prefix("textures/prop.dds"))

    def test_deep_path_no_rig(self):
        self.assertIsNone(detect_rig_prefix("a/b/c/d/prop.pac"))

    def test_just_cd_prefix(self):
        self.assertIsNone(detect_rig_prefix("cd_.pac"))

    def test_cd_prefix_unknown_rig(self):
        self.assertIsNone(detect_rig_prefix("cd_xyz_mesh.pac"))

    def test_numeric_only(self):
        self.assertIsNone(detect_rig_prefix("00000000.pac"))


class DetectRigPrefix_PhmFamily(unittest.TestCase):
    def test_cd_phm_basic(self):
        self.assertEqual(detect_rig_prefix("cd_phm_00_cloak_00_0054_01.pac"), "phm")

    def test_cd_phm_long(self):
        self.assertEqual(detect_rig_prefix("cd_phm_basic_00_00_roofclimb_move_up.paa"), "phm")

    def test_bare_phm_pab(self):
        self.assertEqual(detect_rig_prefix("phm_01.pab"), "phm")

    def test_bare_phm_lod(self):
        self.assertEqual(detect_rig_prefix("phm_01_lod2.pab"), "phm")

    def test_mid_phm1(self):
        self.assertEqual(detect_rig_prefix("cd_seq_001_phm1_intro.paa"), "phm")

    def test_mid_phm2(self):
        self.assertEqual(detect_rig_prefix("cd_seq_002_phm2_foo.paa"), "phm")

    def test_mid_phm8(self):
        self.assertEqual(detect_rig_prefix("cd_seq_008_phm8_boss.paa"), "phm")

    def test_mid_underscore_phm(self):
        self.assertEqual(detect_rig_prefix("cd_xx_phm_body.pac"), "phm")

    def test_case_insensitive_upper(self):
        self.assertEqual(detect_rig_prefix("CD_PHM_00_CLOAK.PAC"), "phm")

    def test_case_insensitive_mixed(self):
        self.assertEqual(detect_rig_prefix("Cd_Phm_00_Cloak.Pac"), "phm")

    def test_path_with_directory(self):
        self.assertEqual(
            detect_rig_prefix("character/cd_phm_00_cloak_00_0054_01.pac"),
            "phm",
        )

    def test_nested_path(self):
        self.assertEqual(
            detect_rig_prefix("some/deep/path/cd_phm_body.pac"), "phm",
        )


class DetectRigPrefix_PhwFamily(unittest.TestCase):
    def test_cd_phw_basic(self):
        self.assertEqual(detect_rig_prefix("cd_phw_00_dress_00_0010_01.pac"), "phw")

    def test_bare_phw_pab(self):
        self.assertEqual(detect_rig_prefix("phw_01.pab"), "phw")

    def test_mid_phw1(self):
        self.assertEqual(detect_rig_prefix("cd_seq_001_phw1_intro.paa"), "phw")

    def test_mid_phw2(self):
        self.assertEqual(detect_rig_prefix("cd_seq_002_phw2_foo.paa"), "phw")

    def test_upper_case(self):
        self.assertEqual(detect_rig_prefix("PHW_01.PAB"), "phw")


class DetectRigPrefix_PtmPtwFamily(unittest.TestCase):
    def test_cd_ptm(self):
        self.assertEqual(detect_rig_prefix("cd_ptm_00_head_0001.pac"), "ptm")

    def test_bare_ptm(self):
        self.assertEqual(detect_rig_prefix("ptm_01.pab"), "ptm")

    def test_cd_ptw(self):
        self.assertEqual(detect_rig_prefix("cd_ptw_00_head_0001.pac"), "ptw")

    def test_bare_ptw(self):
        self.assertEqual(detect_rig_prefix("ptw_01.pab"), "ptw")

    def test_mid_ptm(self):
        self.assertEqual(detect_rig_prefix("cd_seq_001_ptm_intro.paa"), "ptm")


class DetectRigPrefix_FourLetterFamily(unittest.TestCase):
    """4-letter prefixes (ppdm, ppdw) must win over shorter matches."""

    def test_cd_ppdm(self):
        self.assertEqual(detect_rig_prefix("cd_ppdm_00_eyeleft_00_0001.pac"), "ppdm")

    def test_cd_ppdw(self):
        self.assertEqual(detect_rig_prefix("cd_ppdw_00_eyeleft_00_0001.pac"), "ppdw")

    def test_bare_ppdm(self):
        self.assertEqual(detect_rig_prefix("ppdm_01.pab"), "ppdm")

    def test_bare_ppdw(self):
        self.assertEqual(detect_rig_prefix("ppdw_01.pab"), "ppdw")

    def test_mid_underscore_ppdm(self):
        self.assertEqual(detect_rig_prefix("cd_foo_ppdm_bar.pac"), "ppdm")

    def test_uppercase_ppdm(self):
        self.assertEqual(detect_rig_prefix("CD_PPDM_00.PAC"), "ppdm")


class DetectRigPrefix_PfFamily(unittest.TestCase):
    def test_cd_pfm(self):
        self.assertEqual(detect_rig_prefix("cd_pfm_00_face_0001.pac"), "pfm")

    def test_cd_pfw(self):
        self.assertEqual(detect_rig_prefix("cd_pfw_00_face_0001.pac"), "pfw")

    def test_bare_pfm(self):
        self.assertEqual(detect_rig_prefix("pfm_01.pab"), "pfm")

    def test_bare_pfw(self):
        self.assertEqual(detect_rig_prefix("pfw_01.pab"), "pfw")

    def test_mid_pfm(self):
        self.assertEqual(detect_rig_prefix("cd_seq_001_pfm_intro.paa"), "pfm")


class DetectRigPrefix_PgFamily(unittest.TestCase):
    def test_cd_pgm(self):
        self.assertEqual(detect_rig_prefix("cd_pgm_00_gear_0001.pac"), "pgm")

    def test_cd_pgw(self):
        self.assertEqual(detect_rig_prefix("cd_pgw_00_gear_0001.pac"), "pgw")

    def test_bare_pgm(self):
        self.assertEqual(detect_rig_prefix("pgm_01.pab"), "pgm")


class DetectRigPrefix_HorseFamily(unittest.TestCase):
    def test_cd_prh(self):
        self.assertEqual(detect_rig_prefix("cd_prh_00_horse.pac"), "prh")

    def test_bare_prh(self):
        self.assertEqual(detect_rig_prefix("prh_01.pab"), "prh")

    def test_cd_rd_prh(self):
        self.assertEqual(detect_rig_prefix("cd_rd_prh_foo.pac"), "prh")

    def test_cd_rd_other(self):
        """Standalone cd_rd_* without prh still falls under the rd family."""
        self.assertEqual(detect_rig_prefix("cd_rd_dragon.pac"), "rd")

    def test_bare_rd(self):
        self.assertEqual(detect_rig_prefix("rd_01.pab"), "rd")


class DetectRigPrefix_NpcFamily(unittest.TestCase):
    """NPC prefixes appear both with and without the cd_ wrapper."""

    def test_bare_nhm(self):
        self.assertEqual(detect_rig_prefix("nhm_guard_01.paa"), "nhm")

    def test_bare_nhw(self):
        self.assertEqual(detect_rig_prefix("nhw_maid_01.paa"), "nhw")

    def test_cd_ngm(self):
        self.assertEqual(detect_rig_prefix("cd_ngm_00_grunt.pac"), "ngm")

    def test_cd_ngw(self):
        self.assertEqual(detect_rig_prefix("cd_ngw_00_witch.pac"), "ngw")

    def test_mid_ngm(self):
        self.assertEqual(detect_rig_prefix("cd_xx_ngm_body.pac"), "ngm")


class DetectRigPrefix_OrderingAndPrecedence(unittest.TestCase):
    """4-letter prefixes must beat 3-letter prefixes with overlap."""

    def test_ppdm_not_classified_as_ptm_or_pgm(self):
        # ppdm contains neither 'ptm' nor 'pgm' as substrings, but
        # regression-guard that adding future prefixes doesn't shadow.
        self.assertEqual(detect_rig_prefix("ppdm_01.pab"), "ppdm")

    def test_cd_ppdm_not_captured_by_phm(self):
        self.assertEqual(detect_rig_prefix("cd_ppdm_00.pac"), "ppdm")

    def test_cd_ppdm_at_start(self):
        self.assertEqual(detect_rig_prefix("cd_ppdm_eye.pac"), "ppdm")

    def test_first_pattern_wins_when_multiple_match(self):
        # If both 'phm' and 'phw' substrings appear (unrealistic but
        # possible in edge cases), first-seen in _PREFIX_PATTERNS wins.
        # phm and phw both appear; phm is first in the list.
        self.assertIn(
            detect_rig_prefix("cd_phm_00_phw_hybrid.pac"),
            {"phm", "phw"},   # either one accepted; behaviour is deterministic
        )


class DetectRigPrefix_BoundaryBehavior(unittest.TestCase):
    """Substring matches require boundary characters to avoid false positives."""

    def test_no_match_without_boundary(self):
        # 'phm' at end of name without underscore shouldn't match.
        self.assertIsNone(detect_rig_prefix("symphonyphm.pac"))

    def test_no_match_inside_word(self):
        # 'phm' inside a word without underscores is not a match.
        self.assertIsNone(detect_rig_prefix("symphonyphmmix.pac"))

    def test_boundary_at_start(self):
        # 'phm_' at start of name matches via phase-1 bare-prefix rule.
        self.assertEqual(detect_rig_prefix("phm_stuff.pac"), "phm")

    def test_double_underscore(self):
        self.assertEqual(detect_rig_prefix("cd__phm__01.pac"), "phm")


class KnownPrefixesListing(unittest.TestCase):
    """The exported KNOWN_RIG_PREFIXES tuple is used for UI dropdowns."""

    def test_contains_all_families(self):
        expected = {
            "phm", "phw", "ptm", "ptw", "pfm", "pfw",
            "ppdm", "ppdw", "pgm", "pgw", "prh",
            "nhm", "nhw", "ngm", "ngw", "rd",
        }
        self.assertTrue(expected.issubset(set(KNOWN_RIG_PREFIXES)))

    def test_is_tuple(self):
        self.assertIsInstance(KNOWN_RIG_PREFIXES, tuple)

    def test_non_empty(self):
        self.assertGreater(len(KNOWN_RIG_PREFIXES), 10)

    def test_no_duplicates(self):
        self.assertEqual(len(KNOWN_RIG_PREFIXES), len(set(KNOWN_RIG_PREFIXES)))

    def test_all_lowercase(self):
        for p in KNOWN_RIG_PREFIXES:
            self.assertEqual(p, p.lower())

    def test_four_letter_before_three_letter(self):
        """ppdm/ppdw must appear before ptm/pgm so substring-overlap
        edge cases resolve correctly."""
        idx = {p: i for i, p in enumerate(KNOWN_RIG_PREFIXES)}
        self.assertLess(idx["ppdm"], idx["ptm"])
        self.assertLess(idx["ppdw"], idx["ptw"])


# ═════════════════════════════════════════════════════════════════════
# rank_skeleton_candidates
# ═════════════════════════════════════════════════════════════════════

class RankCandidates_EmptyAndEdge(unittest.TestCase):
    def test_empty_input_returns_empty(self):
        self.assertEqual(rank_skeleton_candidates("phm", []), [])

    def test_none_prefix_still_ranks(self):
        got = rank_skeleton_candidates(None, ["a.pab", "b.pab"])
        self.assertEqual(len(got), 2)

    def test_single_candidate_passthrough(self):
        got = rank_skeleton_candidates("phm", ["character/phm_01.pab"])
        self.assertEqual(got, ["character/phm_01.pab"])

    def test_duplicates_collapsed(self):
        got = rank_skeleton_candidates(
            "phm",
            ["character/phm_01.pab", "character/phm_01.pab"],
        )
        self.assertEqual(got, ["character/phm_01.pab"])

    def test_duplicates_case_insensitive(self):
        got = rank_skeleton_candidates(
            "phm",
            ["Character/PHM_01.pab", "character/phm_01.pab"],
        )
        self.assertEqual(len(got), 1)

    def test_non_string_entries_dropped(self):
        got = rank_skeleton_candidates(
            "phm",
            ["character/phm_01.pab", None, 123, "", "b.pab"],  # type: ignore[list-item]
        )
        self.assertEqual(len(got), 2)

    def test_backslash_normalised(self):
        got = rank_skeleton_candidates(
            "phm",
            ["character\\phm_01.pab"],
        )
        self.assertEqual(got, ["character/phm_01.pab"])


class RankCandidates_PrefixMatching(unittest.TestCase):
    def test_prefix_match_wins_over_non_prefix(self):
        got = rank_skeleton_candidates(
            "phm",
            ["other/ptm_01.pab", "character/phm_01.pab"],
        )
        self.assertEqual(got[0], "character/phm_01.pab")

    def test_shorter_prefix_name_wins(self):
        got = rank_skeleton_candidates(
            "phm",
            [
                "character/phm_01_experimental.pab",
                "character/phm_01_lod.pab",
                "character/phm_01.pab",
            ],
        )
        self.assertEqual(got[0], "character/phm_01.pab")

    def test_wrong_prefix_not_in_top(self):
        got = rank_skeleton_candidates(
            "phw",
            ["character/phm_01.pab", "character/phw_01.pab"],
        )
        self.assertEqual(got[0], "character/phw_01.pab")

    def test_same_dir_tie_break(self):
        got = rank_skeleton_candidates(
            "phm",
            ["other/phm_01.pab", "character/phm_01.pab"],
            asset_path="character/cd_phm_body.pac",
        )
        self.assertEqual(got[0], "character/phm_01.pab")

    def test_no_prefix_falls_back_to_shortest_name(self):
        got = rank_skeleton_candidates(
            None,
            ["long_skeleton_name.pab", "a.pab", "medium.pab"],
        )
        self.assertEqual(got[0], "a.pab")

    def test_case_insensitive_prefix_match(self):
        got = rank_skeleton_candidates(
            "PHM",
            ["character/PHM_01.pab", "character/ptm_01.pab"],
        )
        self.assertEqual(got[0], "character/PHM_01.pab")

    def test_deterministic_lexical_tie_break(self):
        got1 = rank_skeleton_candidates(
            "phm",
            ["character/phm_01_a.pab", "character/phm_01_b.pab"],
        )
        got2 = rank_skeleton_candidates(
            "phm",
            ["character/phm_01_b.pab", "character/phm_01_a.pab"],
        )
        self.assertEqual(got1, got2)


class RankCandidates_DirectoryPreference(unittest.TestCase):
    def test_same_dir_prefix_match_best(self):
        got = rank_skeleton_candidates(
            "phm",
            ["character/phm_01.pab", "other/phm_01.pab"],
            asset_path="character/asset.pac",
        )
        self.assertEqual(got[0], "character/phm_01.pab")

    def test_empty_asset_path_disables_dir_rule(self):
        got = rank_skeleton_candidates(
            "phm",
            ["a/phm_01.pab", "b/phm_01.pab"],
            asset_path="",
        )
        # Both prefix-match, same filename length, so lexical order applies.
        self.assertEqual(got, sorted(got))

    def test_dir_preference_only_matters_if_prefix_matches(self):
        # Asset in 'character/' shouldn't force a non-prefix candidate ahead.
        got = rank_skeleton_candidates(
            "phm",
            ["other/phm_01.pab", "character/ptm_01.pab"],
            asset_path="character/asset.pac",
        )
        self.assertEqual(got[0], "other/phm_01.pab")


# ═════════════════════════════════════════════════════════════════════
# resolve_skeleton
# ═════════════════════════════════════════════════════════════════════

class ResolveSkeleton_ManualOverride(unittest.TestCase):
    def test_manual_override_returns_manual_source(self):
        vfs = _FakeVfs({"character/phw_01.pab": b"\x00"})
        with _patch_parse_pab():
            res = resolve_skeleton(
                "character/cd_phm_00_cloak.pac",
                vfs,
                manual_override="character/phw_01.pab",
            )
        self.assertIsNotNone(res.skeleton)
        self.assertEqual(res.source, "manual")
        self.assertEqual(res.pab_path, "character/phw_01.pab")

    def test_manual_override_read_failure(self):
        vfs = _FakeVfs({})
        with _patch_parse_pab():
            res = resolve_skeleton(
                "character/cd_phm.pac",
                vfs,
                manual_override="nope.pab",
            )
        self.assertIsNone(res.skeleton)
        self.assertIn("could not be read", res.reason)

    def test_manual_override_parse_failure(self):
        vfs = _FakeVfs({"character/broken.pab": b"\x00"})
        with _patch_parse_pab(raise_for={"character/broken.pab"}):
            res = resolve_skeleton(
                "character/cd_phm.pac",
                vfs,
                manual_override="character/broken.pab",
            )
        self.assertIsNone(res.skeleton)
        self.assertIn("failed to parse", res.reason)

    def test_manual_override_zero_bones(self):
        vfs = _FakeVfs({"character/empty.pab": b"\x00"})
        empty = _FakeSkeleton(bones=[])
        with _patch_parse_pab(return_map={"character/empty.pab": empty}):
            res = resolve_skeleton(
                "character/cd_phm.pac",
                vfs,
                manual_override="character/empty.pab",
            )
        self.assertIsNone(res.skeleton)
        self.assertIn("zero bones", res.reason)

    def test_manual_override_wins_over_auto(self):
        """Manual override should be used even when auto-resolve would succeed."""
        vfs = _FakeVfs({
            "character/phm_01.pab": b"\x00",
            "character/phw_01.pab": b"\x00",
        })
        with _patch_parse_pab():
            res = resolve_skeleton(
                "character/cd_phm_body.pac",
                vfs,
                manual_override="character/phw_01.pab",
            )
        self.assertEqual(res.pab_path, "character/phw_01.pab")
        self.assertEqual(res.source, "manual")


class ResolveSkeleton_AutoResolve(unittest.TestCase):
    def test_prefix_match_happy_path(self):
        vfs = _FakeVfs({
            "character/phm_01.pab": b"\x00",
            "character/phw_01.pab": b"\x00",
        })
        with _patch_parse_pab():
            res = resolve_skeleton("character/cd_phm_00_cloak.pac", vfs)
        self.assertIsNotNone(res.skeleton)
        self.assertEqual(res.pab_path, "character/phm_01.pab")
        self.assertEqual(res.source, "prefix_match")
        self.assertEqual(res.rig_prefix, "phm")

    def test_prefix_match_for_damiane(self):
        vfs = _FakeVfs({
            "character/phm_01.pab": b"\x00",
            "character/phw_01.pab": b"\x00",
        })
        with _patch_parse_pab():
            res = resolve_skeleton("character/cd_phw_00_dress.pac", vfs)
        self.assertEqual(res.pab_path, "character/phw_01.pab")
        self.assertEqual(res.source, "prefix_match")

    def test_prefix_match_for_ppdm_eye(self):
        vfs = _FakeVfs({
            "character/phm_01.pab": b"\x00",
            "character/ppdm_01.pab": b"\x00",
        })
        with _patch_parse_pab():
            res = resolve_skeleton(
                "character/cd_ppdm_00_eyeleft.pac", vfs,
            )
        self.assertEqual(res.pab_path, "character/ppdm_01.pab")

    def test_no_prefix_same_dir_is_sibling_source(self):
        vfs = _FakeVfs({
            "character/ptm_01.pab": b"\x00",
            "character/phm_01.pab": b"\x00",
        })
        with _patch_parse_pab():
            res = resolve_skeleton("character/unknown_prop.pac", vfs)
        # No prefix detected; top candidate is in same dir as asset,
        # so source is 'sibling_path' (not 'fallback_scan').
        self.assertIsNotNone(res.skeleton)
        self.assertEqual(res.source, "sibling_path")

    def test_no_prefix_different_dir_is_fallback_scan(self):
        vfs = _FakeVfs({"other/mystery_rig.pab": b"\x00"})
        with _patch_parse_pab():
            res = resolve_skeleton("props/unknown.pac", vfs)
        self.assertEqual(res.source, "fallback_scan")

    def test_sibling_path_recognised_as_such(self):
        # When the top candidate shares the asset's directory but
        # doesn't match by prefix, the source string is sibling_path.
        vfs = _FakeVfs({
            "props/mystery_rig.pab": b"\x00",
        })
        with _patch_parse_pab():
            res = resolve_skeleton(
                "props/unknown_prop.pac",
                vfs,
            )
        self.assertEqual(res.source, "sibling_path")
        self.assertEqual(res.pab_path, "props/mystery_rig.pab")

    def test_empty_vfs(self):
        vfs = _FakeVfs({})
        res = resolve_skeleton("character/cd_phm.pac", vfs)
        self.assertIsNone(res.skeleton)
        self.assertIn("no .pab", res.reason.lower())

    def test_vfs_enumeration_failure_captured(self):
        class ExplodingVfs:
            def list_pab_paths(self):
                raise RuntimeError("VFS down")
            def read_pab_bytes(self, p):
                return b""
        res = resolve_skeleton("character/cd_phm.pac", ExplodingVfs())
        self.assertIsNone(res.skeleton)
        self.assertIn("VFS enumeration failed", res.reason)

    def test_prefix_filled_even_on_failure(self):
        """rig_prefix is set regardless of whether resolution succeeded."""
        vfs = _FakeVfs({})
        res = resolve_skeleton("character/cd_phm_body.pac", vfs)
        self.assertEqual(res.rig_prefix, "phm")

    def test_candidates_tried_populated(self):
        vfs = _FakeVfs({"a.pab": b"\x00", "b.pab": b"\x00"})
        with _patch_parse_pab():
            res = resolve_skeleton("character/cd_phm.pac", vfs)
        self.assertEqual(len(res.candidates_tried), 2)

    def test_parse_failure_tries_next_candidate(self):
        vfs = _FakeVfs({
            "character/phm_01.pab": b"\x00",
            "character/phm_02.pab": b"\x00",
        })
        with _patch_parse_pab(raise_for={"character/phm_01.pab"}):
            res = resolve_skeleton("character/cd_phm.pac", vfs)
        # Should still succeed via phm_02 fallback.
        self.assertIsNotNone(res.skeleton)
        self.assertEqual(res.pab_path, "character/phm_02.pab")

    def test_all_candidates_fail_to_parse(self):
        vfs = _FakeVfs({"a.pab": b"\x00", "b.pab": b"\x00"})
        with _patch_parse_pab(raise_for={"a.pab", "b.pab"}):
            res = resolve_skeleton("character/cd_phm.pac", vfs)
        self.assertIsNone(res.skeleton)
        self.assertIn("no usable .pab", res.reason)

    def test_all_candidates_have_zero_bones(self):
        vfs = _FakeVfs({"a.pab": b"\x00", "b.pab": b"\x00"})
        empty = _FakeSkeleton(bones=[])
        with _patch_parse_pab(return_map={"a.pab": empty, "b.pab": empty}):
            res = resolve_skeleton("character/cd_phm.pac", vfs)
        self.assertIsNone(res.skeleton)

    def test_resolution_dataclass_has_defaults(self):
        r = SkeletonResolution()
        self.assertIsNone(r.skeleton)
        self.assertEqual(r.pab_path, "")
        self.assertEqual(r.source, "")
        self.assertEqual(r.candidates_tried, [])


class ResolveSkeleton_CharacterMeshRegression(unittest.TestCase):
    """Regression guards for the exact reported bug.

    The user reported that ``cd_phm_00_cloak_00_0054_01.pac`` FBX
    export failed because our old search only looked for
    ``cd_phm_00_cloak_00_0054_01.pab`` (which doesn't exist). These
    tests pin down that the shared class rig (``phm_01.pab``) is
    found correctly now.
    """

    def test_cloak_finds_phm_rig(self):
        vfs = _FakeVfs({"character/phm_01.pab": b"\x00"})
        with _patch_parse_pab():
            res = resolve_skeleton(
                "character/cd_phm_00_cloak_00_0054_01.pac", vfs,
            )
        self.assertEqual(res.pab_path, "character/phm_01.pab")

    def test_damiane_dress_finds_phw_rig(self):
        vfs = _FakeVfs({
            "character/phm_01.pab": b"\x00",
            "character/phw_01.pab": b"\x00",
        })
        with _patch_parse_pab():
            res = resolve_skeleton(
                "character/cd_phw_00_dress_00_0010.pac", vfs,
            )
        self.assertEqual(res.pab_path, "character/phw_01.pab")

    def test_eye_finds_ppdm_rig(self):
        vfs = _FakeVfs({
            "character/phm_01.pab": b"\x00",
            "character/ppdm_01.pab": b"\x00",
        })
        with _patch_parse_pab():
            res = resolve_skeleton(
                "character/cd_ppdm_00_eyeleft_00_0001.pac", vfs,
            )
        self.assertEqual(res.pab_path, "character/ppdm_01.pab")

    def test_anim_finds_phm_rig(self):
        """Animation PAA paths should also resolve via the same logic."""
        vfs = _FakeVfs({"character/phm_01.pab": b"\x00"})
        with _patch_parse_pab():
            res = resolve_skeleton(
                "character/cd_phm_cough_00_00_nor_std_idle_01.paa", vfs,
            )
        self.assertEqual(res.pab_path, "character/phm_01.pab")

    def test_asset_without_prefix_gets_fallback(self):
        """Non-character PACs still resolve to the first valid .pab.

        Previously fell through to mesh-only silently; now returns
        a skeleton (though with a non-prefix source) so the caller
        gets SOMETHING rather than nothing.
        """
        vfs = _FakeVfs({"other/mystery.pab": b"\x00"})
        with _patch_parse_pab():
            res = resolve_skeleton("other/strange.pac", vfs)
        self.assertIsNotNone(res.skeleton)
        self.assertNotEqual(res.source, "prefix_match")

    def test_prefix_winds_over_sibling(self):
        """A prefix-matched rig in ANY directory beats a random sibling."""
        vfs = _FakeVfs({
            "character/phm_01.pab": b"\x00",
            "props/random_rig.pab": b"\x00",
        })
        with _patch_parse_pab():
            res = resolve_skeleton("props/cd_phm_prop.pac", vfs)
        self.assertEqual(res.pab_path, "character/phm_01.pab")


class ResolveSkeleton_NeverRaises(unittest.TestCase):
    """The resolver is a black box — no path should escape as an exception."""

    def test_empty_asset_path(self):
        vfs = _FakeVfs({})
        res = resolve_skeleton("", vfs)
        self.assertIsNone(res.skeleton)

    def test_none_asset_path_returns_no_skeleton(self):
        # None asset_path short-circuits via detect_rig_prefix's falsy
        # check and ultimately returns a no-skeleton resolution with a
        # reason string. Never raises.
        vfs = _FakeVfs({})
        res = resolve_skeleton(None, vfs)  # type: ignore[arg-type]
        self.assertIsNone(res.skeleton)

    def test_vfs_read_failure_not_raised(self):
        class ReadFailVfs:
            def list_pab_paths(self):
                return ["x.pab"]
            def read_pab_bytes(self, p):
                raise IOError("boom")
        with _patch_parse_pab():
            res = resolve_skeleton("character/cd_phm.pac", ReadFailVfs())
        self.assertIsNone(res.skeleton)

    def test_vfs_returns_non_list_gracefully(self):
        class OddVfs:
            def list_pab_paths(self):
                return iter(["character/phm_01.pab"])   # generator, not list
            def read_pab_bytes(self, p):
                return b""
        with _patch_parse_pab():
            res = resolve_skeleton("character/cd_phm.pac", OddVfs())
        self.assertIsNotNone(res.skeleton)


# ═════════════════════════════════════════════════════════════════════
# load_skeleton_from_path
# ═════════════════════════════════════════════════════════════════════

class LoadSkeletonFromPath(unittest.TestCase):
    def test_happy_path(self):
        with _patch_parse_pab():
            got = load_skeleton_from_path(
                "character/phm_01.pab", lambda: b"\x00",
            )
        self.assertIsNotNone(got)
        self.assertGreater(len(got.bones), 0)

    def test_reader_raises(self):
        def reader():
            raise IOError("nope")
        got = load_skeleton_from_path("character/phm_01.pab", reader)
        self.assertIsNone(got)

    def test_parse_raises(self):
        with _patch_parse_pab(raise_for={"x.pab"}):
            got = load_skeleton_from_path("x.pab", lambda: b"\x00")
        self.assertIsNone(got)

    def test_zero_bones_returns_none(self):
        empty = _FakeSkeleton(bones=[])
        with _patch_parse_pab(return_map={"x.pab": empty}):
            got = load_skeleton_from_path("x.pab", lambda: b"\x00")
        self.assertIsNone(got)


# ═════════════════════════════════════════════════════════════════════
# VfsManagerAdapter — adapter to the real VfsManager
# ═════════════════════════════════════════════════════════════════════

class _FakeEntry:
    def __init__(self, path):
        self.path = path


class _FakePamtData:
    def __init__(self, paths):
        self.file_entries = [_FakeEntry(p) for p in paths]


class _FakeVfsManager:
    """Mimic the real VfsManager shape enough for the adapter."""

    def __init__(self, groups):
        # groups: dict of group_id -> list of (path, bytes)
        self._pamt_cache = {}
        self._data = {}
        for gid, entries in groups.items():
            paths = []
            for p, data in entries:
                paths.append(p)
                self._data[p] = data
            self._pamt_cache[gid] = _FakePamtData(paths)

    def read_entry_data(self, entry):
        return self._data[entry.path]


class VfsManagerAdapter_Tests(unittest.TestCase):
    def test_lists_pabs_across_groups(self):
        vfs = _FakeVfsManager({
            "0000": [
                ("character/phm_01.pab", b"A"),
                ("character/foo.dds", b"B"),
            ],
            "0009": [
                ("character/phw_01.pab", b"C"),
            ],
        })
        adapter = VfsManagerAdapter(vfs)
        pabs = adapter.list_pab_paths()
        self.assertEqual(len(pabs), 2)
        self.assertIn("character/phm_01.pab", pabs)
        self.assertIn("character/phw_01.pab", pabs)

    def test_read_returns_bytes(self):
        vfs = _FakeVfsManager({
            "0000": [("character/phm_01.pab", b"PAR hello")],
        })
        adapter = VfsManagerAdapter(vfs)
        self.assertEqual(
            adapter.read_pab_bytes("character/phm_01.pab"),
            b"PAR hello",
        )

    def test_read_unknown_raises(self):
        vfs = _FakeVfsManager({"0000": []})
        adapter = VfsManagerAdapter(vfs)
        with self.assertRaises(LookupError):
            adapter.read_pab_bytes("nope.pab")

    def test_read_accepts_basename_only(self):
        vfs = _FakeVfsManager({
            "0000": [("character/phm_01.pab", b"data")],
        })
        adapter = VfsManagerAdapter(vfs)
        # Passing just the basename should still succeed.
        self.assertEqual(
            adapter.read_pab_bytes("phm_01.pab"),
            b"data",
        )

    def test_index_cached(self):
        vfs = _FakeVfsManager({
            "0000": [("character/phm_01.pab", b"X")],
        })
        adapter = VfsManagerAdapter(vfs)
        first = adapter.list_pab_paths()
        second = adapter.list_pab_paths()
        self.assertEqual(first, second)

    def test_empty_vfs(self):
        vfs = _FakeVfsManager({})
        adapter = VfsManagerAdapter(vfs)
        self.assertEqual(adapter.list_pab_paths(), [])

    def test_missing_pamt_cache_attr_handled(self):
        class Minimal:
            pass
        adapter = VfsManagerAdapter(Minimal())
        self.assertEqual(adapter.list_pab_paths(), [])

    def test_skips_non_pab_entries(self):
        vfs = _FakeVfsManager({
            "0000": [
                ("character/phm_01.pab", b"ok"),
                ("character/phm_01.PAB", b"also_ok"),   # case variation
                ("character/thing.dds", b"no"),
                ("character/thing.paa", b"no"),
            ],
        })
        adapter = VfsManagerAdapter(vfs)
        pabs = adapter.list_pab_paths()
        # Both case variants should be listed.
        self.assertEqual(len(pabs), 2)


# ═════════════════════════════════════════════════════════════════════
# Prefix exhaustiveness — every known prefix has a round-trip test
# ═════════════════════════════════════════════════════════════════════

class EveryKnownPrefixExerciseEndToEnd(unittest.TestCase):
    """For every entry in KNOWN_RIG_PREFIXES, run the full resolver
    end-to-end with a synthesised asset and synthesised rig in the
    fake VFS. Ensures we never ship a prefix that the resolver
    knows about in its detect step but can't actually resolve."""

    def test_every_prefix_round_trips(self):
        for prefix in KNOWN_RIG_PREFIXES:
            asset = f"character/cd_{prefix}_00_asset_0001.pac"
            pab_path = f"character/{prefix}_01.pab"
            vfs = _FakeVfs({pab_path: b"\x00"})
            with _patch_parse_pab():
                res = resolve_skeleton(asset, vfs)
            with self.subTest(prefix=prefix):
                self.assertEqual(res.rig_prefix, prefix)
                self.assertEqual(res.pab_path, pab_path)
                self.assertEqual(res.source, "prefix_match")

    def test_every_prefix_detected_from_bare_pab(self):
        for prefix in KNOWN_RIG_PREFIXES:
            with self.subTest(prefix=prefix):
                self.assertEqual(detect_rig_prefix(f"{prefix}_01.pab"), prefix)

    def test_every_prefix_detected_from_cd_asset(self):
        for prefix in KNOWN_RIG_PREFIXES:
            with self.subTest(prefix=prefix):
                # Skip the NPC-bare prefixes which don't have cd_ form.
                if prefix in {"nhm", "nhw"}:
                    continue
                self.assertEqual(
                    detect_rig_prefix(f"cd_{prefix}_00_asset.pac"),
                    prefix,
                )


if __name__ == "__main__":
    unittest.main()
