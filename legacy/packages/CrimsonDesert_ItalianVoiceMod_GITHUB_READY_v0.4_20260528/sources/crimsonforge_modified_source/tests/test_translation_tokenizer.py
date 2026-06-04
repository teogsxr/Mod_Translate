"""Regression tests for :mod:`core.translation_tokenizer`.

We verify every placeholder family found in the Korean-paloc
census survives the encode→simulate-AI→decode round-trip:

  * ``<br/>``         line break — 36,084 instances
  * ``[EMPTY]``       sentinel    — 2,163
  * ``%0`` … ``%9``   positional  — 244
  * ``%%``            literal %   — 4
  * ``#27``           doc number  — 27
  * ``{emoji:...}``   namespaced  — 5,424
  * ``{Param1}``      bare ident  — 91
  * ``{X:Y#Korean}``  hash-label  — 1,717 (the big one —
                                    namespace locked, label
                                    translated)

The "simulated AI" pass just translates the non-sentinel prose
(Korean → fake English). We then check that every protected
token is byte-identical after decode AND that hash-label tokens
have their inner label swapped to whatever the AI returned.
"""

from __future__ import annotations

import os
import sys
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from core.translation_tokenizer import (   # noqa: E402
    PROMPT_INSTRUCTION,
    count_sentinels_per_entry,
    decode_after_translation,
    encode_for_translation,
)


def _simulate_ai(text: str, replacements: dict[str, str] | None = None) -> str:
    """Stand-in for a real LLM call. Replaces substrings via
    the ``replacements`` mapping but leaves everything else
    (including sentinels) untouched.

    Sentinels in every test must survive this function unchanged.
    If a test wants to validate the "AI mangled a sentinel"
    recovery path, it mutates the text manually.
    """
    if not replacements:
        return text
    out = text
    for src, dst in replacements.items():
        out = out.replace(src, dst)
    return out


class EncodeAngleTag(unittest.TestCase):
    def test_br_is_encoded(self):
        encoded, table = encode_for_translation("hello<br/>world")
        self.assertEqual(len(table), 1)
        self.assertIn("\u27E6CF0\u27E7", encoded)
        self.assertNotIn("<br/>", encoded)

    def test_multiple_br_round_trip(self):
        src = "line1<br/>line2<br/>line3<br/>end"
        encoded, table = encode_for_translation(src)
        self.assertEqual(len(table), 3)
        decoded = decode_after_translation(_simulate_ai(encoded), table)
        self.assertEqual(decoded, src)

    def test_open_tag_round_trip(self):
        src = "use <b>BOLD</b> please"
        encoded, table = encode_for_translation(src)
        self.assertEqual(len(table), 2)   # <b> and </b>
        decoded = decode_after_translation(_simulate_ai(encoded), table)
        self.assertEqual(decoded, src)

    def test_self_closing_with_attrs(self):
        src = '<img src="foo.png"/> text'
        encoded, table = encode_for_translation(src)
        decoded = decode_after_translation(_simulate_ai(encoded), table)
        self.assertEqual(decoded, src)


class EncodeSquareBracket(unittest.TestCase):
    def test_empty_sentinel_round_trip(self):
        src = "[EMPTY]"
        encoded, table = encode_for_translation(src)
        self.assertEqual(len(table), 1)
        self.assertNotIn("[EMPTY]", encoded)
        decoded = decode_after_translation(_simulate_ai(encoded), table)
        self.assertEqual(decoded, src)

    def test_embedded_sentinel(self):
        src = "before [EMPTY] after"
        encoded, table = encode_for_translation(src)
        self.assertEqual(len(table), 1)
        decoded = decode_after_translation(_simulate_ai(encoded), table)
        self.assertEqual(decoded, src)


class EncodePercent(unittest.TestCase):
    def test_positional_args_round_trip(self):
        src = "%0#%1# %2# 소모"
        encoded, table = encode_for_translation(src)
        # 3 positional args locked
        count = count_sentinels_per_entry(table)
        self.assertEqual(count["total"], 3)
        decoded = decode_after_translation(_simulate_ai(encoded), table)
        self.assertEqual(decoded, src)

    def test_double_percent(self):
        src = "margin: %0 ~ %1%%"
        encoded, table = encode_for_translation(src)
        self.assertEqual(count_sentinels_per_entry(table)["total"], 3)  # %0, %1, %%
        decoded = decode_after_translation(_simulate_ai(encoded), table)
        self.assertEqual(decoded, src)

    def test_format_specifiers(self):
        src = "Hello %s, you have %d points"
        encoded, table = encode_for_translation(src)
        self.assertEqual(count_sentinels_per_entry(table)["total"], 2)
        decoded = decode_after_translation(_simulate_ai(encoded), table)
        self.assertEqual(decoded, src)

    def test_dollar_arg_notation(self):
        src = "Hello %1$s, %2$d items"
        encoded, table = encode_for_translation(src)
        self.assertEqual(count_sentinels_per_entry(table)["total"], 2)
        decoded = decode_after_translation(_simulate_ai(encoded), table)
        self.assertEqual(decoded, src)


class EncodeHashNum(unittest.TestCase):
    def test_hash_number_in_prose(self):
        src = "에르난드 익명의 낙서 #27"
        encoded, table = encode_for_translation(src)
        self.assertEqual(count_sentinels_per_entry(table)["total"], 1)
        decoded = decode_after_translation(_simulate_ai(encoded), table)
        self.assertEqual(decoded, src)

    def test_hash_number_not_touched_inside_brace(self):
        """The '#' inside a {...#label} token must NOT be captured
        separately by the hash-num regex — the brace regex already
        handled it."""
        src = "{Staticinfo:Knowledge:Knowledge_Hp#생명}"
        encoded, table = encode_for_translation(src)
        # One paired token, not two simple ones.
        count = count_sentinels_per_entry(table)
        self.assertEqual(count["total"], 1)
        self.assertEqual(count["paired"], 1)


class EncodePlainBrace(unittest.TestCase):
    def test_bare_identifier(self):
        src = "참여하기(필요 금액:{minigamefee})"
        encoded, table = encode_for_translation(src)
        self.assertEqual(count_sentinels_per_entry(table)["total"], 1)
        decoded = decode_after_translation(_simulate_ai(encoded), table)
        self.assertEqual(decoded, src)

    def test_namespaced_emoji(self):
        src = "{emoji:cd_icon_ability_wanted_immune} 협박하기"
        encoded, table = encode_for_translation(src)
        self.assertEqual(count_sentinels_per_entry(table)["total"], 1)
        self.assertNotIn("emoji", encoded)
        decoded = decode_after_translation(_simulate_ai(encoded), table)
        self.assertEqual(decoded, src)

    def test_key_binding(self):
        src = "{Key:Key_Run}로 달리기"
        encoded, table = encode_for_translation(src)
        self.assertEqual(count_sentinels_per_entry(table)["total"], 1)
        decoded = decode_after_translation(_simulate_ai(encoded), table)
        self.assertEqual(decoded, src)


class EncodeHashLabelBrace(unittest.TestCase):
    """The big one: ``{Staticinfo:...#생명}`` — namespace MUST
    stay locked, label IS translated."""

    def test_paired_sentinel_structure(self):
        src = "{Staticinfo:Knowledge:Knowledge_Hp#생명}이 감소합니다."
        encoded, table = encode_for_translation(src)
        count = count_sentinels_per_entry(table)
        self.assertEqual(count["paired"], 1)
        # Encoded string keeps the Korean label between paired sentinels.
        self.assertIn("\u27E6CF0\u27E7생명\u27E6/CF0\u27E7", encoded)

    def test_ai_translated_label_survives(self):
        """Simulate AI translating 생명 → Life. The namespace
        must come back intact around the new label."""
        src = "{Staticinfo:Knowledge:Knowledge_Hp#생명}이 감소합니다."
        encoded, table = encode_for_translation(src)
        ai_out = _simulate_ai(encoded, {"생명": "Life", "이 감소합니다.": " decreases."})
        decoded = decode_after_translation(ai_out, table)
        self.assertEqual(
            decoded,
            "{Staticinfo:Knowledge:Knowledge_Hp#Life} decreases.",
        )

    def test_namespace_locked_even_if_ai_would_translate_it(self):
        """Even if the AI tries to translate 'Staticinfo' or
        'Knowledge_Hp', the encoded form prevents it — those
        words are never visible to the AI."""
        src = "{Staticinfo:Knowledge:Knowledge_Hp#생명}"
        encoded, _ = encode_for_translation(src)
        self.assertNotIn("Staticinfo", encoded)
        self.assertNotIn("Knowledge_Hp", encoded)
        # But the Korean label IS visible for translation.
        self.assertIn("생명", encoded)

    def test_multiple_hash_label_braces_independent_labels(self):
        src = (
            "{Staticinfo:Knowledge:Knowledge_Hp#생명}와 "
            "{Staticinfo:Knowledge:Knowledge_Mp#마나}가 회복됩니다."
        )
        encoded, table = encode_for_translation(src)
        count = count_sentinels_per_entry(table)
        self.assertEqual(count["paired"], 2)
        ai_out = _simulate_ai(encoded, {
            "생명": "Life",
            "마나": "Mana",
            "와 ": "and ",
            "가 회복됩니다.": " are restored.",
        })
        decoded = decode_after_translation(ai_out, table)
        self.assertEqual(
            decoded,
            "{Staticinfo:Knowledge:Knowledge_Hp#Life}and "
            "{Staticinfo:Knowledge:Knowledge_Mp#Mana} are restored.",
        )

    def test_ascii_label_inside_hash_brace_also_preserved(self):
        """About 20 tokens in the paloc have {...#ASCII_Label} —
        A.T.A.G. is the canonical example. The ASCII label still
        routes through the translatable slot, so the AI can
        choose to translate or leave it."""
        src = "{staticinfo:Knowledge:Knowledge_All_Terrain_Armored_Gear:Name#A.T.A.G.}"
        encoded, table = encode_for_translation(src)
        count = count_sentinels_per_entry(table)
        self.assertEqual(count["paired"], 1)
        # AI leaves the label alone — decode restores verbatim.
        decoded = decode_after_translation(_simulate_ai(encoded), table)
        self.assertEqual(decoded, src)


class MultiFamilyRoundTrip(unittest.TestCase):
    """Realistic shape — every family in one string."""

    def test_the_kitchen_sink(self):
        src = (
            "[EMPTY]<br/>"
            "%0#의 {Staticinfo:Knowledge:Knowledge_Hp#생명}이 %1 감소했습니다. "
            "{emoji:cd_icon_danger} 낙서 #27<br/>"
            "비율: %%5"
        )
        encoded, table = encode_for_translation(src)
        # Count: [EMPTY] + 2×<br/> + 1 hash-label-brace + 1 plain
        # brace + 2 percent-args + 1 hash-num + 1 %%
        count = count_sentinels_per_entry(table)
        self.assertGreaterEqual(count["total"], 8)

        # AI translates the Korean parts, leaves sentinels alone.
        ai_out = _simulate_ai(encoded, {
            "생명": "Life",
            "의 ": " has ",
            " 감소했습니다. ": " reduction. ",
            " 낙서 ": " doodle ",
            "비율: ": "rate: ",
        })
        decoded = decode_after_translation(ai_out, table)

        # Every original protected token is back, byte-for-byte,
        # except the hash-label brace which got its label swapped.
        self.assertIn("[EMPTY]", decoded)
        self.assertEqual(decoded.count("<br/>"), 2)
        self.assertIn("%0", decoded)
        self.assertIn("%1", decoded)
        self.assertIn("%%", decoded)
        self.assertIn("{emoji:cd_icon_danger}", decoded)
        self.assertIn("#27", decoded)
        # Hash-label brace was translated:
        self.assertIn("{Staticinfo:Knowledge:Knowledge_Hp#Life}", decoded)


class EmptyAndEdgeCases(unittest.TestCase):
    def test_empty_string(self):
        encoded, table = encode_for_translation("")
        self.assertEqual(encoded, "")
        self.assertEqual(len(table), 0)
        self.assertEqual(decode_after_translation("", table), "")

    def test_no_placeholders(self):
        src = "순수한 한국어 텍스트입니다"
        encoded, table = encode_for_translation(src)
        self.assertEqual(encoded, src)
        self.assertEqual(len(table), 0)
        self.assertEqual(decode_after_translation(src, table), src)

    def test_empty_braces_not_matched(self):
        # {} is not a placeholder — leave it alone.
        src = "literal {} character"
        encoded, table = encode_for_translation(src)
        # No tokens captured (regex requires non-empty inside).
        self.assertEqual(len(table), 0)
        self.assertEqual(encoded, src)


class RobustnessToAiNoise(unittest.TestCase):
    """The AI sometimes mangles sentinels slightly — whitespace,
    case, stray characters. The decoder's tolerant regex catches
    the common mutations."""

    def test_case_change_recovered(self):
        src = "<br/>hello"
        encoded, table = encode_for_translation(src)
        # Simulate AI lowercasing the tag.
        mangled = encoded.replace("CF0", "cf0")
        decoded = decode_after_translation(mangled, table)
        self.assertIn("<br/>", decoded)

    def test_inner_whitespace_recovered(self):
        src = "[EMPTY]"
        encoded, table = encode_for_translation(src)
        # AI inserts a space before/after the digit.
        mangled = encoded.replace("CF0", "CF 0 ")
        decoded = decode_after_translation(mangled, table)
        self.assertIn("[EMPTY]", decoded)

    def test_unknown_sentinel_dropped_not_crashed(self):
        # The AI hallucinates ⟦CF99⟧ that we never emitted.
        src = "hello<br/>world"
        encoded, table = encode_for_translation(src)
        ai_out = encoded + " \u27E6CF99\u27E7 extra"
        decoded = decode_after_translation(ai_out, table)
        # Our <br/> is back; the bogus sentinel is stripped.
        self.assertIn("<br/>", decoded)
        self.assertNotIn("CF99", decoded)


class PromptInstructionSanity(unittest.TestCase):
    """One tiny canary — the instruction must mention both the
    opening and closing sentinel forms AND emphasise preservation.
    """

    def test_instruction_has_shape_reference(self):
        self.assertIn("\u27E6CF", PROMPT_INSTRUCTION)
        self.assertIn("\u27E6/CF", PROMPT_INSTRUCTION)
        self.assertIn("VERBATIM", PROMPT_INSTRUCTION)


if __name__ == "__main__":
    unittest.main()
