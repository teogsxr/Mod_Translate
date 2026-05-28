import unittest

from tools.fill_missing_audio_text_overrides import build_translate_prompt


class FillMissingAudioTextOverridesTest(unittest.TestCase):
    def test_translategemma_prompt_keeps_two_blank_lines_before_text(self):
        prompt = build_translate_prompt(
            "Hello there.",
            "English",
            "en",
            "Italian",
            "it",
        )

        self.assertIn("English (en) to Italian (it)", prompt)
        self.assertTrue(prompt.endswith("Italian:\n\nHello there."))


if __name__ == "__main__":
    unittest.main()
