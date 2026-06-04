import json
import tempfile
import unittest
from pathlib import Path

from utils.audio_text_overrides import (
    get_audio_text_override,
    load_audio_text_overrides,
    upsert_audio_text_override,
)


class AudioTextOverridesTest(unittest.TestCase):
    def test_returns_requested_language_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "audio_text_overrides.json"
            path.write_text(json.dumps({
                "version": 1,
                "entries": {
                    "0006:sound/missing_line.wem": {
                        "texts": {"it": "Testo italiano"}
                    }
                },
            }), encoding="utf-8")

            text = get_audio_text_override(
                "0006",
                r"sound\missing_line.wem",
                "it",
                path=path,
            )

        self.assertEqual(text, "Testo italiano")

    def test_upsert_persists_transcript_and_target_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "audio_text_overrides.json"
            upsert_audio_text_override(
                "0006",
                "sound/missing_line.wem",
                language_code="it",
                text="Testo locale",
                source_language="en",
                source_transcript="Local text",
                metadata={"translation_model": "translategemma:12b"},
                path=path,
            )
            entries = load_audio_text_overrides(path)

        record = entries["0006:sound/missing_line.wem"]
        self.assertEqual(record["texts"]["it"], "Testo locale")
        self.assertEqual(record["source_transcript"], "Local text")
        self.assertEqual(record["metadata"]["translation_model"], "translategemma:12b")


if __name__ == "__main__":
    unittest.main()
