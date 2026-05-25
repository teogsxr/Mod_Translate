"""Regression tests for :mod:`core.placeholder_scanner`.

We cover every issue kind the scanner reports, plus the auto-fix
pipeline's surgical-replacement guarantees.

The scanner is the QA surface that runs AFTER translation, so the
tests are organised around the real failure modes we've seen from
the AI round-trip:

  * ``MISSING`` — source had a token, translation dropped it.
  * ``ALTERED`` — translation has a near-cousin of a source token
    (namespace change, identifier edit).
  * ``LEAKED_SENTINEL`` — a tokenizer sentinel (``⟦CFn⟧``) made
    it to the final translation despite decode tolerance.
  * ``EXTRA_TOKEN`` — the translation contains a placeholder the
    source never had. Never auto-fixed.

For auto-fix we explicitly verify the surgical-edit contract: the
bytes outside the targeted placeholder span must be identical
before and after the fix. That's the whole reason this pipeline
exists — we never re-run the AI, we only repair the one broken
token.
"""

from __future__ import annotations

import os
import sys
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from core.placeholder_scanner import (   # noqa: E402
    IssueKind,
    PlaceholderIssue,
    ScanResult,
    autofix_entry,
    scan_batch,
    scan_entry,
    summarise_by_kind,
)
from core.translation_tokenizer import (   # noqa: E402
    decode_after_translation,
    encode_for_translation,
)


# ── Clean cases (no issues) ───────────────────────────────────────

class CleanInputs(unittest.TestCase):
    """Every scenario here MUST produce zero issues."""

    def test_empty_pair(self):
        res = scan_entry("", "")
        self.assertFalse(res.broken)
        self.assertEqual(res.issues, [])

    def test_no_tokens_either_side(self):
        res = scan_entry("Hello world", "Hola mundo")
        self.assertFalse(res.broken)

    def test_identical_source_and_translation(self):
        src = "Press <br/> to continue %0 times"
        res = scan_entry(src, src)
        self.assertFalse(res.broken)

    def test_hash_label_brace_translated_inner(self):
        # The namespace is preserved; the inner Korean label was
        # translated. Scanner signature-matches on namespace, so
        # this is CLEAN.
        src = "{Staticinfo:Knowledge:Knowledge_Hp#생명}을 회복"
        trl = "Restore {Staticinfo:Knowledge:Knowledge_Hp#HP}"
        res = scan_entry(src, trl)
        self.assertFalse(res.broken, msg=f"issues={res.issues}")

    def test_br_preserved(self):
        src = "line 1<br/>line 2<br/>line 3"
        trl = "linea 1<br/>linea 2<br/>linea 3"
        res = scan_entry(src, trl)
        self.assertFalse(res.broken)

    def test_percent_preserved(self):
        res = scan_entry("Hit %0 for %1 damage", "Golpeas a %0 por %1")
        self.assertFalse(res.broken)

    def test_square_bracket_preserved(self):
        res = scan_entry("[EMPTY] slot", "Ranura [EMPTY]")
        self.assertFalse(res.broken)

    def test_braces_in_different_order(self):
        # Token ORDER doesn't matter; scanner uses Counter semantics.
        src = "{A} and {B}"
        trl = "{B} y {A}"
        res = scan_entry(src, trl)
        self.assertFalse(res.broken)


# ── MISSING ───────────────────────────────────────────────────────

class MissingToken(unittest.TestCase):

    def test_single_br_dropped(self):
        src = "line 1<br/>line 2"
        trl = "linea 1 linea 2"
        res = scan_entry(src, trl)
        self.assertTrue(res.broken)
        self.assertEqual(len(res.issues), 1)
        self.assertEqual(res.issues[0].kind, IssueKind.MISSING)
        self.assertEqual(res.issues[0].source_token, "<br/>")
        self.assertTrue(res.issues[0].auto_fixable)

    def test_multiple_missing_same_token(self):
        src = "A<br/>B<br/>C"
        trl = "A B C"
        res = scan_entry(src, trl)
        kinds = [i.kind for i in res.issues]
        self.assertEqual(kinds.count(IssueKind.MISSING), 2)

    def test_hash_label_dropped(self):
        src = "Hit {Actor:Hero#영웅} for damage"
        trl = "Hit for damage"
        res = scan_entry(src, trl)
        self.assertEqual(len(res.issues), 1)
        self.assertEqual(res.issues[0].kind, IssueKind.MISSING)

    def test_percent_dropped(self):
        res = scan_entry("Deal %0 damage", "Deal damage")
        self.assertEqual(len(res.issues), 1)
        self.assertEqual(res.issues[0].kind, IssueKind.MISSING)
        self.assertEqual(res.issues[0].source_token, "%0")

    def test_square_bracket_dropped(self):
        res = scan_entry("slot [EMPTY]", "slot")
        self.assertEqual(len(res.issues), 1)
        self.assertEqual(res.issues[0].kind, IssueKind.MISSING)

    def test_missing_issue_has_source_span(self):
        src = "hello <br/> world"
        trl = "hello world"
        res = scan_entry(src, trl)
        iss = res.issues[0]
        s, e = iss.source_span
        self.assertEqual(src[s:e], "<br/>")


# ── ALTERED ───────────────────────────────────────────────────────

class AlteredToken(unittest.TestCase):

    def test_plain_brace_namespace_change(self):
        # {Key:Key_Run} → {Key:Key_Running} — the identifier was
        # mutated. Scanner reports ALTERED because a plain-brace
        # token is present in the translation that doesn't match
        # any source signature but shares the same family prefix.
        src = "press {Key:Key_Run} to run"
        trl = "press {Key:Key_Running} to run"
        res = scan_entry(src, trl)
        altered = [i for i in res.issues if i.kind == IssueKind.ALTERED]
        self.assertEqual(len(altered), 1)
        self.assertEqual(altered[0].source_token, "{Key:Key_Run}")
        self.assertEqual(altered[0].translated_fragment, "{Key:Key_Running}")
        self.assertTrue(altered[0].auto_fixable)

    def test_angle_tag_altered(self):
        src = "use <b>BOLD</b> please"
        trl = "use <b>BOLD</b2> please"   # closing tag mutated
        res = scan_entry(src, trl)
        altered = [i for i in res.issues if i.kind == IssueKind.ALTERED]
        self.assertGreaterEqual(len(altered), 1)

    def test_altered_span_points_into_translation(self):
        src = "press {Key:Key_Run} to run"
        trl = "press {Key:Key_Sprint} to sprint"
        res = scan_entry(src, trl)
        altered = [i for i in res.issues if i.kind == IssueKind.ALTERED]
        self.assertEqual(len(altered), 1)
        s, e = altered[0].translated_span
        self.assertEqual(trl[s:e], "{Key:Key_Sprint}")


# ── LEAKED_SENTINEL ───────────────────────────────────────────────

class LeakedSentinel(unittest.TestCase):

    def test_bare_leaked(self):
        res = scan_entry("source", "translated \u27E6CF99\u27E7 stuff")
        leaked = [i for i in res.issues if i.kind == IssueKind.LEAKED_SENTINEL]
        self.assertEqual(len(leaked), 1)
        self.assertTrue(leaked[0].auto_fixable)

    def test_close_tag_also_caught(self):
        res = scan_entry("", "hello \u27E6/CF0\u27E7 bye")
        leaked = [i for i in res.issues if i.kind == IssueKind.LEAKED_SENTINEL]
        self.assertEqual(len(leaked), 1)

    def test_whitespace_noise_variant(self):
        res = scan_entry("", "x \u27E6 CF 12 \u27E7 y")
        leaked = [i for i in res.issues if i.kind == IssueKind.LEAKED_SENTINEL]
        self.assertEqual(len(leaked), 1)


# ── EXTRA_TOKEN ───────────────────────────────────────────────────

class ExtraToken(unittest.TestCase):

    def test_br_invented_in_translation(self):
        res = scan_entry("plain text", "hola<br/>mundo")
        extra = [i for i in res.issues if i.kind == IssueKind.EXTRA_TOKEN]
        self.assertEqual(len(extra), 1)
        self.assertFalse(extra[0].auto_fixable)

    def test_extra_brace_with_no_family_kin(self):
        # A {...} shape in translation with NO analogous token in
        # source → EXTRA_TOKEN (not ALTERED because there's no
        # family prefix match at all — source has no brace tokens).
        res = scan_entry("hello world", "hola {MUNDO}")
        extra = [i for i in res.issues if i.kind == IssueKind.EXTRA_TOKEN]
        self.assertEqual(len(extra), 1)
        self.assertFalse(extra[0].auto_fixable)


# ── Auto-fix: correctness + surgical guarantees ──────────────────

class AutoFixMissing(unittest.TestCase):

    def test_missing_br_appended_with_space(self):
        src = "A<br/>B"
        trl = "linea A linea B"
        fixed, n = autofix_entry(src, trl)
        self.assertEqual(n, 1)
        self.assertTrue(fixed.endswith("<br/>"))
        # Prose before the appended token unchanged.
        self.assertTrue(fixed.startswith("linea A linea B"))

    def test_missing_no_duplicate_space(self):
        # Translation already ends with a space — don't add another.
        src = "A<br/>B"
        trl = "linea A "
        fixed, n = autofix_entry(src, trl)
        self.assertEqual(n, 1)
        self.assertNotIn("  ", fixed)   # no double-space

    def test_missing_on_newline_suffix(self):
        src = "line 1<br/>line 2"
        trl = "line 1\n"
        fixed, n = autofix_entry(src, trl)
        self.assertEqual(n, 1)
        self.assertIn("<br/>", fixed)

    def test_missing_when_trl_empty(self):
        src = "{FOO}"
        trl = ""
        fixed, n = autofix_entry(src, trl)
        self.assertEqual(n, 1)
        self.assertEqual(fixed, "{FOO}")


class AutoFixAltered(unittest.TestCase):

    def test_altered_surgical_replace_preserves_prose(self):
        src = "press {Key:Key_Run} to run"
        trl = "presione {Key:Key_Running} para correr"
        fixed, n = autofix_entry(src, trl)
        self.assertEqual(n, 1)
        # Prose before + after the replaced span must be byte-exact.
        self.assertTrue(fixed.startswith("presione "))
        self.assertTrue(fixed.endswith(" para correr"))
        # Replaced substring must equal source's original.
        self.assertIn("{Key:Key_Run}", fixed)
        self.assertNotIn("{Key:Key_Running}", fixed)

    def test_altered_does_not_leak_other_spans(self):
        # Two altered tokens in the same line; replacing right-to-left
        # must keep earlier spans valid.
        src = "{A:X} then {B:Y}"
        trl = "{A:XX} luego {B:YY}"
        fixed, n = autofix_entry(src, trl)
        self.assertEqual(n, 2)
        self.assertIn("{A:X}", fixed)
        self.assertIn("{B:Y}", fixed)
        self.assertNotIn("{A:XX}", fixed)
        self.assertNotIn("{B:YY}", fixed)


class AutoFixLeakedSentinel(unittest.TestCase):

    def test_leaked_stripped(self):
        fixed, n = autofix_entry("", "hello \u27E6CF99\u27E7 bye")
        self.assertEqual(n, 1)
        self.assertNotIn("\u27E6", fixed)
        self.assertNotIn("\u27E7", fixed)

    def test_leaked_collapses_double_space(self):
        fixed, _ = autofix_entry("", "a \u27E6CF3\u27E7 b")
        self.assertEqual(fixed.count("  "), 0)
        self.assertIn("a", fixed)
        self.assertIn("b", fixed)


class AutoFixExtraToken(unittest.TestCase):

    def test_extra_is_not_fixed(self):
        src = "plain"
        trl = "hola<br/>mundo"
        fixed, n = autofix_entry(src, trl)
        self.assertEqual(n, 0)
        self.assertEqual(fixed, trl)   # translation untouched


class AutoFixHashLabelPreservation(unittest.TestCase):
    """Regression suite for the v1.22.9 user bug.

    Source: ``Defeat the {StaticInfo:Knowledge:Knowledge_LandSpider_BismuthQueen#Queen Bismuth Oreback Crab}``
    Correct translation: ``Defeat the {StaticInfo:...#ملكة سلطعون البزموت}``

    Earlier autofix logic replaced the WHOLE broken token with the
    source token on ALTERED, which deleted the correctly-translated
    Arabic label and reverted the entry to English. The contract
    below codifies the correct behaviour: when both sides are
    ``{ns#label}`` tokens, restore the source namespace but KEEP
    the translator's label.
    """

    USER_SOURCE = (
        "Defeat the "
        "{StaticInfo:Knowledge:Knowledge_LandSpider_BismuthQueen"
        "#Queen Bismuth Oreback Crab}"
    )
    ARABIC_LABEL = "ملكة سلطعون البزموت"
    CORRECT_TRANSLATION = (
        "Defeat the "
        "{StaticInfo:Knowledge:Knowledge_LandSpider_BismuthQueen#"
        + ARABIC_LABEL
        + "}"
    )

    def test_correct_translation_reports_no_issues(self):
        res = scan_entry(self.USER_SOURCE, self.CORRECT_TRANSLATION)
        self.assertFalse(res.broken, msg=f"spurious issues={res.issues}")

    def test_correct_translation_is_untouched_by_autofix(self):
        fixed, n = autofix_entry(self.USER_SOURCE, self.CORRECT_TRANSLATION)
        self.assertEqual(n, 0)
        self.assertEqual(fixed, self.CORRECT_TRANSLATION)

    def test_case_change_in_namespace_preserves_arabic_label(self):
        # staticinfo (lowercase) instead of StaticInfo — AI drift.
        broken = (
            "Defeat the "
            "{staticinfo:Knowledge:Knowledge_LandSpider_BismuthQueen#"
            + self.ARABIC_LABEL
            + "}"
        )
        fixed, n = autofix_entry(self.USER_SOURCE, broken)
        self.assertEqual(n, 1)
        self.assertEqual(fixed, self.CORRECT_TRANSLATION,
                         "namespace restored but Arabic label KEPT")

    def test_namespace_translated_to_target_lang_preserves_label(self):
        # AI translated 'Knowledge' → 'المعرفة' inside the namespace.
        broken = (
            "Defeat the "
            "{StaticInfo:المعرفة:Knowledge_LandSpider_BismuthQueen#"
            + self.ARABIC_LABEL
            + "}"
        )
        fixed, n = autofix_entry(self.USER_SOURCE, broken)
        self.assertEqual(n, 1)
        self.assertEqual(fixed, self.CORRECT_TRANSLATION)

    def test_identifier_mutated_preserves_label(self):
        # AI added an underscore — BismuthQueen → Bismuth_Queen.
        broken = (
            "Defeat the "
            "{StaticInfo:Knowledge:Knowledge_LandSpider_Bismuth_Queen#"
            + self.ARABIC_LABEL
            + "}"
        )
        fixed, n = autofix_entry(self.USER_SOURCE, broken)
        self.assertEqual(n, 1)
        self.assertEqual(fixed, self.CORRECT_TRANSLATION)

    def test_space_before_hash_preserves_label(self):
        # AI added a stray space before the hash.
        broken = (
            "Defeat the "
            "{StaticInfo:Knowledge:Knowledge_LandSpider_BismuthQueen #"
            + self.ARABIC_LABEL
            + "}"
        )
        fixed, n = autofix_entry(self.USER_SOURCE, broken)
        self.assertEqual(n, 1)
        self.assertEqual(fixed, self.CORRECT_TRANSLATION)

    def test_korean_label_also_preserved(self):
        # Mirror scenario for a Korean translator.
        src = "Hit {Staticinfo:Knowledge:Knowledge_Hp#Life}"
        broken = "Hit {staticinfo:Knowledge:Knowledge_Hp#생명}"
        fixed, n = autofix_entry(src, broken)
        self.assertEqual(n, 1)
        self.assertEqual(
            fixed, "Hit {Staticinfo:Knowledge:Knowledge_Hp#생명}",
        )

    def test_spanish_label_also_preserved(self):
        src = "Press {Key:Key_Run#Run} to run"
        broken = "Presione {Key_Run#Correr} para correr"
        fixed, n = autofix_entry(src, broken)
        self.assertEqual(n, 1)
        # The namespace is restored but 'Correr' is kept.
        self.assertIn("{Key:Key_Run#Correr}", fixed)
        # Spanish prose outside the token is untouched.
        self.assertTrue(fixed.startswith("Presione "))
        self.assertTrue(fixed.endswith(" para correr"))

    def test_plain_brace_no_hash_still_replaces_whole_token(self):
        # Tokens without a '#' (no translatable label inside) fall
        # back to whole-token replacement — there's nothing to
        # preserve, the whole identifier IS the lookup key.
        src = "press {Key:Key_Run} to run"
        broken = "presione {Key:Key_Sprint} para correr"
        fixed, _ = autofix_entry(src, broken)
        self.assertIn("{Key:Key_Run}", fixed)
        self.assertNotIn("{Key:Key_Sprint}", fixed)

    def test_label_with_punctuation_preserved(self):
        # Arabic labels commonly contain punctuation + spaces.
        src = "Check {Info:Name#Hero_Bismuth}"
        broken = "Check {info:Name#هرو، ذو الرأسين: ملك!}"
        fixed, _ = autofix_entry(src, broken)
        self.assertIn("{Info:Name#هرو، ذو الرأسين: ملك!}", fixed)

    def test_autofix_idempotent_after_hash_label_repair(self):
        # After fixing once, a second pass must be a no-op.
        broken = (
            "Defeat the "
            "{staticinfo:Knowledge:Knowledge_LandSpider_BismuthQueen#"
            + self.ARABIC_LABEL
            + "}"
        )
        once, _ = autofix_entry(self.USER_SOURCE, broken)
        twice, n = autofix_entry(self.USER_SOURCE, once)
        self.assertEqual(n, 0)
        self.assertEqual(once, twice)


class AutoFixIdempotence(unittest.TestCase):
    """Running autofix twice on the same input must be a no-op the
    second time (one-shot convergence)."""

    def test_missing_is_idempotent(self):
        src = "A<br/>B"
        trl = "uno dos"
        once, _ = autofix_entry(src, trl)
        twice, n = autofix_entry(src, once)
        self.assertEqual(n, 0)
        self.assertEqual(once, twice)

    def test_altered_is_idempotent(self):
        src = "press {Key:Key_Run} to run"
        trl = "presione {Key:Key_Running} para correr"
        once, _ = autofix_entry(src, trl)
        twice, n = autofix_entry(src, once)
        self.assertEqual(n, 0)
        self.assertEqual(once, twice)


class SurgicalReplacementContract(unittest.TestCase):
    """The raison d'être: auto-fix must NEVER touch translated prose
    outside a broken placeholder region."""

    def test_prose_byte_exact_outside_altered_span(self):
        src = "go {Key:Key_Run}"
        trl = "lots of prose before {Key:Key_Running} and lots after"
        fixed, _ = autofix_entry(src, trl)
        # Grab the prose before and after the EXACT altered span in
        # the original translation and prove they're untouched in
        # the fixed output.
        before = "lots of prose before "
        after = " and lots after"
        self.assertTrue(fixed.startswith(before))
        self.assertTrue(fixed.endswith(after))

    def test_unicode_prose_preserved(self):
        # Korean + English mixed, altered brace in the middle.
        src = "공격 {Key:Key_Run}"
        trl = "공격 시 {Key:Key_Running} 눌러주세요"
        fixed, _ = autofix_entry(src, trl)
        self.assertTrue(fixed.startswith("공격 시 "))
        self.assertTrue(fixed.endswith(" 눌러주세요"))


# ── Batch helpers ─────────────────────────────────────────────────

class BatchHelpers(unittest.TestCase):

    def test_scan_batch_returns_one_result_per_pair(self):
        pairs = [
            ("hello <br/> world", "hola mundo"),           # missing
            ("clean", "clean"),                             # clean
            ("{Key:X}", "{Key:Y}"),                        # altered
        ]
        results = scan_batch(pairs)
        self.assertEqual(len(results), 3)
        self.assertTrue(results[0].broken)
        self.assertFalse(results[1].broken)
        self.assertTrue(results[2].broken)

    def test_summarise_by_kind(self):
        pairs = [
            ("<br/>", ""),                              # missing
            ("{Key:X}", "{Key:Y}"),                     # altered
            ("", "stray \u27E6CF9\u27E7 text"),         # leaked
            ("clean", "hola <br/> mundo"),              # extra
            ("both<br/>", "stripped"),                  # missing
        ]
        results = scan_batch(pairs)
        summary = summarise_by_kind(results)
        self.assertGreaterEqual(summary.get("missing", 0), 2)
        self.assertGreaterEqual(summary.get("altered", 0), 1)
        self.assertGreaterEqual(summary.get("leaked_sentinel", 0), 1)
        self.assertGreaterEqual(summary.get("extra_token", 0), 1)

    def test_scan_result_auto_fixable_property(self):
        src = "<br/> and %0"
        trl = "nothing here but \u27E6CF99\u27E7 leaked"
        res = scan_entry(src, trl)
        # 2 missing (br, %0) + 1 leaked + 0 extra (all alterable)
        # All three are auto-fixable.
        self.assertEqual(res.auto_fixable, len(res.issues))


# ── Integration: real tokenizer → manually break → scan/fix ──────

class TokenizerIntegration(unittest.TestCase):
    """Prove the scanner works on strings that came through the
    real encode→decode pipeline plus a simulated AI screw-up."""

    def test_recover_after_missing_br(self):
        src = "line 1<br/>line 2<br/>line 3"
        encoded, table = encode_for_translation(src)
        # Simulate the AI dropping one sentinel.
        broken_encoded = encoded.replace("\u27E6CF1\u27E7", "", 1)
        decoded = decode_after_translation(broken_encoded, table)
        # Decoded should be missing exactly one <br/>.
        self.assertEqual(decoded.count("<br/>"), 1)
        # Scanner catches it.
        res = scan_entry(src, decoded)
        missing = [i for i in res.issues if i.kind == IssueKind.MISSING]
        self.assertEqual(len(missing), 1)
        # Auto-fix appends the missing token.
        fixed, n = autofix_entry(src, decoded)
        self.assertEqual(n, 1)
        self.assertEqual(fixed.count("<br/>"), 2)

    def test_recover_after_hash_label_missing(self):
        src = "hp is {Staticinfo:Knowledge:Knowledge_Hp#생명}"
        # Simulate the translation dropping the whole token.
        trl = "hp is"
        res = scan_entry(src, trl)
        self.assertEqual(len(res.issues), 1)
        self.assertEqual(res.issues[0].kind, IssueKind.MISSING)
        fixed, _ = autofix_entry(src, trl)
        # Reinserted token has the original Korean label because the
        # scanner's autofix pulls from source, not from AI output.
        self.assertIn("{Staticinfo:Knowledge:Knowledge_Hp#생명}", fixed)


# ── Never-raise contract ──────────────────────────────────────────

class NeverRaises(unittest.TestCase):
    """The scanner + autofix are designed to be fail-soft."""

    def test_scan_very_long_inputs(self):
        src = "<br/>" * 1000 + "middle" + "{A}" * 1000
        trl = "nothing"
        # Don't care about the count here — only that it returns.
        res = scan_entry(src, trl)
        self.assertTrue(res.broken)

    def test_autofix_on_clean_pair_returns_unchanged(self):
        fixed, n = autofix_entry("hello", "hola")
        self.assertEqual(n, 0)
        self.assertEqual(fixed, "hola")

    def test_scan_handles_only_translation(self):
        # No source tokens, but translation has one — EXTRA.
        res = scan_entry("", "stray <br/>")
        self.assertEqual(len(res.issues), 1)
        self.assertEqual(res.issues[0].kind, IssueKind.EXTRA_TOKEN)


if __name__ == "__main__":
    unittest.main()
