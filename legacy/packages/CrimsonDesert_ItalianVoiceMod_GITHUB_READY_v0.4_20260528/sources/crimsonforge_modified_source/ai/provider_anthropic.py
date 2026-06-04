"""Anthropic provider (Claude Opus 4.6, Sonnet 4.6, Haiku 4.5).

Uses the official anthropic SDK (not OpenAI-compatible).
"""

import time
import anthropic

from ai.provider_base import (
    AIProviderBase, ModelInfo, TranslationResult, ConnectionResult,
)
from ai.pricing_registry import calculate_cost
from utils.logger import get_logger

logger = get_logger("ai.anthropic")


class AnthropicProvider(AIProviderBase):
    name = "Anthropic Claude"
    provider_id = "anthropic"
    requires_api_key = True
    supports_openai_compat = False

    def __init__(self, api_key: str = "", base_url: str = "https://api.anthropic.com",
                 timeout: int = 60, max_retries: int = 3):
        super().__init__(api_key, base_url, timeout, max_retries)
        self._client = None

    def _get_client(self) -> anthropic.Anthropic:
        if self._client is None or self._client.api_key != self._api_key:
            self._client = anthropic.Anthropic(
                api_key=self._api_key,
                base_url=self._base_url if self._base_url != "https://api.anthropic.com/v1" else anthropic.NOT_GIVEN,
                timeout=self._timeout,
                max_retries=self._max_retries,
            )
        return self._client

    def list_models(self) -> list[ModelInfo]:
        try:
            client = self._get_client()
            response = client.models.list(limit=100)
            models = []
            for m in response.data:
                models.append(ModelInfo(
                    model_id=m.id,
                    name=getattr(m, "display_name", m.id),
                    provider=self.provider_id,
                ))
            models.sort(key=lambda x: x.model_id)
            return models
        except Exception as e:
            logger.error("Failed to list Anthropic models: %s", e)
            raise ConnectionError(
                f"Failed to list Anthropic models: {e}. "
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
            model = "claude-sonnet-4-6-20250514"

        if not system_prompt:
            from ai.default_prompt import get_default_system_prompt
            system_prompt = get_default_system_prompt(source_lang, target_lang)

        messages = []
        if context:
            messages.append({"role": "user", "content": f"Context: {context}"})
            messages.append({"role": "assistant", "content": "Understood, I will use this context."})
        messages.append({"role": "user", "content": text})

        start_time = time.time()
        try:
            client = self._get_client()
            response = client.messages.create(
                model=model,
                max_tokens=4096,
                system=system_prompt,
                messages=messages,
                temperature=0.3,
            )
            latency = (time.time() - start_time) * 1000

            translated = ""
            for block in response.content:
                if block.type == "text":
                    translated += block.text
            translated = translated.strip()

            in_tok = response.usage.input_tokens
            out_tok = response.usage.output_tokens
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
            logger.error("Anthropic translation failed: %s", e)
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
