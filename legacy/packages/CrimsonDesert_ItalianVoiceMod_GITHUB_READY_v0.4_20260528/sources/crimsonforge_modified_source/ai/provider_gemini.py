"""Google Gemini provider (Gemini 3.1 Pro, Flash, etc.).

Uses the google-genai SDK for native access, but also supports
OpenAI-compatible mode via the Gemini OpenAI endpoint.
"""

import time
from google import genai
from google.genai import types

from ai.provider_base import (
    AIProviderBase, ModelInfo, TranslationResult, ConnectionResult,
)
from ai.pricing_registry import calculate_cost
from utils.logger import get_logger

logger = get_logger("ai.gemini")


class GeminiProvider(AIProviderBase):
    name = "Google Gemini"
    provider_id = "gemini"
    requires_api_key = True
    supports_openai_compat = True

    def __init__(self, api_key: str = "",
                 base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai/",
                 timeout: int = 60, max_retries: int = 3):
        super().__init__(api_key, base_url, timeout, max_retries)
        self._client = None

    def _get_client(self) -> genai.Client:
        if self._client is None:
            self._client = genai.Client(api_key=self._api_key)
        return self._client

    def list_models(self) -> list[ModelInfo]:
        try:
            client = self._get_client()
            models = []
            for m in client.models.list():
                model_id = m.name
                if model_id.startswith("models/"):
                    model_id = model_id[7:]
                if "gemini" in model_id.lower():
                    models.append(ModelInfo(
                        model_id=model_id,
                        name=getattr(m, "display_name", model_id),
                        provider=self.provider_id,
                    ))
            models.sort(key=lambda x: x.model_id)
            return models
        except Exception as e:
            logger.error("Failed to list Gemini models: %s", e)
            raise ConnectionError(
                f"Failed to list Gemini models: {e}. "
                f"Check your API key and internet connection."
            ) from e

    def translate(
        self,
        text: str,
        source_lang: str,
        target_lang: str,
        model: str = "",
        system_prompt: str = "",
        context: str = "",
    ) -> TranslationResult:
        if not model:
            model = "gemini-2.5-flash"

        if not system_prompt:
            from ai.default_prompt import get_default_system_prompt
            system_prompt = get_default_system_prompt(source_lang, target_lang)

        user_content = text
        if context:
            user_content = f"Context: {context}\n\nTranslate: {text}"

        start_time = time.time()
        try:
            client = self._get_client()
            response = client.models.generate_content(
                model=model,
                contents=user_content,
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    temperature=0.3,
                    max_output_tokens=4096,
                ),
            )
            latency = (time.time() - start_time) * 1000

            translated = response.text.strip() if response.text else ""

            in_tok = 0
            out_tok = 0
            if response.usage_metadata:
                in_tok = getattr(response.usage_metadata, "prompt_token_count", 0) or 0
                out_tok = getattr(response.usage_metadata, "candidates_token_count", 0) or 0

            return TranslationResult(
                translated_text=translated,
                source_text=text,
                source_lang=source_lang,
                target_lang=target_lang,
                model_used=model,
                provider=self.provider_id,
                input_tokens=in_tok,
                output_tokens=out_tok,
                total_tokens=in_tok + out_tok,
                cost_estimate=calculate_cost(self.provider_id, model, in_tok, out_tok),
                latency_ms=latency,
                success=True,
            )
        except Exception as e:
            latency = (time.time() - start_time) * 1000
            logger.error("Gemini translation failed: %s", e)
            return TranslationResult(
                translated_text="",
                source_text=text,
                source_lang=source_lang,
                target_lang=target_lang,
                model_used=model,
                provider=self.provider_id,
                latency_ms=latency,
                error=str(e),
                success=False,
            )

    def test_connection(self) -> ConnectionResult:
        try:
            models = self.list_models()
            return ConnectionResult(
                connected=True,
                provider=self.provider_id,
                message=f"Connected. {len(models)} models available.",
                models_available=len(models),
            )
        except Exception as e:
            return ConnectionResult(
                connected=False,
                provider=self.provider_id,
                message=f"Connection failed: {e}",
                error=str(e),
            )
