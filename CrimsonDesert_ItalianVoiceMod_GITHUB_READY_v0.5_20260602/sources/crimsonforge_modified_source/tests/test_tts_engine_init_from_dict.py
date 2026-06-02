"""Regression tests for ``TTSEngine.initialize_from_config(dict)``.

Bug history (2026-05-07): "Generate + Patch" worked but "Generate All
+ Patch" failed with 401 Unauthorized on ElevenLabs (and would fail
the same way for any provider whose key lives in a nested config
section).

Root cause: ``initialize_from_config`` checked ``hasattr(config,
'get')`` BEFORE ``isinstance(config, dict)``. Plain dicts have a
``.get()`` method, but it doesn't understand dotted-path keys. So
``dict.get("ai_providers.elevenlabs_tts.api_key", "")`` returned ``""``
— provider initialised with an empty key — every request returned
401. The dotted-path branch was unreachable for plain dicts.

Single-mode "Generate + Patch" was unaffected because the engine
was initialised once at startup with the live ``ConfigManager``,
whose ``.get`` does walk dotted paths. Batch mode passed
``self._config.data`` (a plain dict) into a worker so the worker
re-initialised a fresh engine — that's where the bug bit.

These tests pin the contract: ``initialize_from_config`` must
correctly extract nested API keys from BOTH a ConfigManager-like
object AND a plain dict.
"""

from __future__ import annotations

import os
import sys
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from ai.tts_engine import TTSEngine  # noqa: E402


class _FakeConfigManager:
    """Minimal stand-in for ``utils.config.ConfigManager`` — enough
    to verify the hasattr-fallback path still works for objects that
    natively understand dotted keys.
    """

    def __init__(self, data: dict):
        self._data = data

    def get(self, key: str, default=None):
        cur = self._data
        for k in key.split("."):
            if isinstance(cur, dict) and k in cur:
                cur = cur[k]
            else:
                return default
        return cur


class InitializeFromDict(unittest.TestCase):
    """The exact failure mode the user reported on 2026-05-07."""

    def setUp(self):
        self.config_dict = {
            "ai_providers": {
                "elevenlabs_tts": {"api_key": "el_test_secret_KEY_123"},
                "openai":          {"api_key": "sk-openai-test"},
            },
            "tts": {
                "active_provider": "elevenlabs_tts",
                "azure_region":    "westus",
            },
        }

    def test_elevenlabs_key_resolves_from_dict(self):
        eng = TTSEngine()
        eng.initialize_from_config(self.config_dict)
        prov = eng.get_provider("elevenlabs_tts")
        self.assertEqual(
            prov.api_key, "el_test_secret_KEY_123",
            "ElevenLabs key must resolve through dict's dotted path. "
            "Empty key means batch-mode 401 bug has returned.",
        )

    def test_openai_key_resolves_from_dict(self):
        # OpenAI's TTS key is shared via TTS_KEY_SHARING — the
        # same dotted-path lookup must work.
        eng = TTSEngine()
        eng.initialize_from_config(self.config_dict)
        # The OpenAI TTS provider id, whatever the table says.
        from ai.tts_engine import TTSEngine as _TTS  # noqa: F401
        from ai.tts_engine import TTS_PROVIDER_CLASSES, TTS_KEY_SHARING
        for pid in TTS_PROVIDER_CLASSES:
            if TTS_KEY_SHARING.get(pid) == "openai":
                prov = eng.get_provider(pid)
                self.assertEqual(
                    prov.api_key, "sk-openai-test",
                    f"OpenAI TTS provider {pid!r} must inherit the "
                    "shared key via dotted-path lookup."
                )
                break

    def test_active_provider_resolves_from_dict(self):
        eng = TTSEngine()
        eng.initialize_from_config(self.config_dict)
        # _active_provider_id is the dotted-key-driven default.
        self.assertEqual(eng._active_provider_id, "elevenlabs_tts")

    def test_extras_resolve_from_dict(self):
        # Azure's region sits at "tts.azure_region" — a dotted key.
        # The provider stashes it in ``self._extra["region"]``.
        eng = TTSEngine()
        eng.initialize_from_config(self.config_dict)
        prov = eng.get_provider("azure_tts")
        self.assertEqual(prov._extra.get("region"), "westus")


class InitializeFromConfigManager(unittest.TestCase):
    """The legacy single-mode path must still work after the fix."""

    def test_elevenlabs_key_resolves_from_configmanager(self):
        cfg = _FakeConfigManager({
            "ai_providers": {
                "elevenlabs_tts": {"api_key": "el_via_configmanager"},
            },
        })
        eng = TTSEngine()
        eng.initialize_from_config(cfg)
        prov = eng.get_provider("elevenlabs_tts")
        self.assertEqual(prov.api_key, "el_via_configmanager")


class InitializeMissingKeysAreEmpty(unittest.TestCase):
    """Absent keys produce empty strings (not crashes), consistent
    with the fallback semantics callers already rely on."""

    def test_no_ai_providers_section(self):
        eng = TTSEngine()
        eng.initialize_from_config({})  # totally empty
        prov = eng.get_provider("elevenlabs_tts")
        self.assertEqual(prov.api_key, "")

    def test_partial_ai_providers_section(self):
        eng = TTSEngine()
        eng.initialize_from_config({
            "ai_providers": {"openai": {"api_key": "sk-only-openai"}}
        })
        prov = eng.get_provider("elevenlabs_tts")
        self.assertEqual(prov.api_key, "")


if __name__ == "__main__":
    unittest.main()
