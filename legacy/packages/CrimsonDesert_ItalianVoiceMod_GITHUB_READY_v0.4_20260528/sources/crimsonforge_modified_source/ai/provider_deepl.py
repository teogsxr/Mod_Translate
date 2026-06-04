"""DeepL translation provider.

Uses the official DeepL Python SDK for high-quality machine translation.
Supports glossary management, formality control, and context parameter.
Free tier: 500K chars/month. Pro: $5.49/mo + $25/1M chars.
"""

import time
from typing import Optional

from ai.provider_base import (
    AIProviderBase, ModelInfo, TranslationResult, ConnectionResult,
)
from utils.logger import get_logger

logger = get_logger("ai.provider_deepl")

# DeepL language codes mapping
DEEPL_LANG_MAP = {
    "english": "EN", "british english": "EN-GB", "american english": "EN-US",
    "german": "DE", "french": "FR", "spanish": "ES",
    "italian": "IT", "dutch": "NL", "polish": "PL",
    "portuguese": "PT-PT", "brazilian portuguese": "PT-BR",
    "russian": "RU", "japanese": "JA", "chinese": "ZH",
    "korean": "KO", "czech": "CS", "danish": "DA",
    "finnish": "FI", "greek": "EL", "hungarian": "HU",
    "indonesian": "ID", "latvian": "LV", "lithuanian": "LT",
    "norwegian": "NB", "romanian": "RO", "slovak": "SK",
    "slovenian": "SL", "swedish": "SV", "turkish": "TR",
    "ukrainian": "UK", "bulgarian": "BG", "estonian": "ET",
    "arabic": "AR",
}

# Languages that support formality
FORMALITY_LANGS = {"DE", "FR", "IT", "ES", "NL", "PL", "PT-PT", "PT-BR", "RU", "JA"}


def _to_deepl_lang(lang_name: str, is_target: bool = False) -> str:
    """Convert language name to DeepL language code."""
    key = lang_name.lower().strip()
    if key in DEEPL_LANG_MAP:
        return DEEPL_LANG_MAP[key]
    # Try direct code
    upper = lang_name.upper().strip()
    if len(upper) <= 5:
        return upper
    return "EN" if not is_target else "EN-US"


class DeepLProvider(AIProviderBase):
    """DeepL translation provider using official SDK."""

    name = "DeepL"
    provider_id = "deepl"
    requires_api_key = True
    supports_openai_compat = False

    def __init__(self, api_key: str = "", **kwargs):
        super().__init__(api_key=api_key, **kwargs)
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                import deepl
                self._client = deepl.Translator(self._api_key)
            except ImportError:
                raise RuntimeError(
                    "DeepL SDK not installed. Run: pip install deepl"
                )
        return self._client

    def list_models(self) -> list[ModelInfo]:
        """DeepL doesn't have selectable models — return fixed entries."""
        return [
            ModelInfo(
                model_id="deepl",
                name="DeepL Translate",
                provider="deepl",
                context_window=0,
                max_output_tokens=0,
            ),
            ModelInfo(
                model_id="deepl-next",
                name="DeepL Next (Enhanced)",
                provider="deepl",
                context_window=0,
                max_output_tokens=0,
            ),
        ]

    def translate(
        self,
        text: str,
        source_lang: str,
        target_lang: str,
        model: str = "",
        system_prompt: str = "",
        context: str = "",
    ) -> TranslationResult:
        """Translate text using DeepL API."""
        start = time.time()

        try:
            client = self._get_client()

            src_code = _to_deepl_lang(source_lang)
            tgt_code = _to_deepl_lang(target_lang, is_target=True)

            kwargs = {
                "text": text,
                "target_lang": tgt_code,
                "source_lang": src_code,
            }

            # Add context if provided (improves translation quality)
            if context:
                kwargs["context"] = context

            # Add formality for supported languages
            if tgt_code.split("-")[0] in FORMALITY_LANGS:
                kwargs["formality"] = "default"

            # Use next model if requested
            if model == "deepl-next":
                kwargs["model_type"] = "quality_optimized"

            result = client.translate_text(**kwargs)

            latency = (time.time() - start) * 1000
            char_count = len(text)

            # DeepL charges per character, not tokens
            # Pro: $25 per 1M characters
            cost = (char_count / 1_000_000) * 25.0

            return TranslationResult(
                translated_text=result.text,
                source_text=text,
                source_lang=source_lang,
                target_lang=target_lang,
                model_used=model or "deepl",
                provider="deepl",
                input_tokens=char_count,
                output_tokens=len(result.text),
                total_tokens=char_count + len(result.text),
                cost_estimate=cost,
                latency_ms=latency,
            )

        except Exception as e:
            latency = (time.time() - start) * 1000
            logger.error("DeepL translation error: %s", e)
            return TranslationResult(
                translated_text="",
                source_text=text,
                source_lang=source_lang,
                target_lang=target_lang,
                model_used=model or "deepl",
                provider="deepl",
                latency_ms=latency,
                error=str(e),
                success=False,
            )

    def test_connection(self) -> ConnectionResult:
        """Test DeepL API connection."""
        try:
            client = self._get_client()
            usage = client.get_usage()

            char_count = usage.character.count if usage.character else 0
            char_limit = usage.character.limit if usage.character else 0

            return ConnectionResult(
                connected=True,
                provider="deepl",
                message=f"Connected. Usage: {char_count:,}/{char_limit:,} characters",
                models_available=2,
            )
        except Exception as e:
            return ConnectionResult(
                connected=False,
                provider="deepl",
                message=f"Connection failed: {e}",
                error=str(e),
            )
