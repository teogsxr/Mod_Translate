"""Base class for OpenAI-compatible providers.

DeepSeek, Ollama, vLLM, Mistral, and Custom providers all use
the OpenAI SDK with different base URLs. This base class eliminates
duplication while letting each provider customize its behavior.
"""

import time
from openai import OpenAI

from ai.provider_base import (
    AIProviderBase, ModelInfo, TranslationResult, ConnectionResult,
)
from ai.pricing_registry import calculate_cost
from utils.logger import get_logger

logger = get_logger("ai.openai_compat")


class OpenAICompatProvider(AIProviderBase):
    """Base for providers that expose an OpenAI-compatible API."""

    supports_openai_compat = True

    def __init__(self, api_key: str = "", base_url: str = "",
                 timeout: int = 60, max_retries: int = 3):
        super().__init__(api_key, base_url, timeout, max_retries)
        self._client = None

    def _get_client(self) -> OpenAI:
        if self._client is None:
            self._client = OpenAI(
                api_key=self._api_key or "not-needed",
                base_url=self._base_url,
                timeout=self._timeout,
                max_retries=self._max_retries,
            )
        return self._client

    def list_models(self) -> list[ModelInfo]:
        try:
            client = self._get_client()
            response = client.models.list()
            models = []
            for m in response.data:
                models.append(ModelInfo(
                    model_id=m.id,
                    name=m.id,
                    provider=self.provider_id,
                ))
            models.sort(key=lambda x: x.model_id)
            return models
        except Exception as e:
            logger.error("Failed to list %s models: %s", self.name, e)
            raise ConnectionError(
                f"Failed to list {self.name} models: {e}. "
                f"Check the API key and base URL in settings."
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
            model = self._get_default_model()

        if not system_prompt:
            from ai.default_prompt import get_default_system_prompt
            system_prompt = get_default_system_prompt(source_lang, target_lang)

        messages = [{"role": "system", "content": system_prompt}]
        if context:
            messages.append({"role": "user", "content": f"Context: {context}"})
        messages.append({"role": "user", "content": text})

        start_time = time.time()
        try:
            client = self._get_client()
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0.3,
            )
            latency = (time.time() - start_time) * 1000

            translated = response.choices[0].message.content.strip()
            usage = response.usage
            in_tok = usage.prompt_tokens if usage else 0
            out_tok = usage.completion_tokens if usage else 0

            return TranslationResult(
                translated_text=translated,
                source_text=text,
                source_lang=source_lang,
                target_lang=target_lang,
                model_used=model,
                provider=self.provider_id,
                input_tokens=in_tok,
                output_tokens=out_tok,
                total_tokens=usage.total_tokens if usage else 0,
                cost_estimate=calculate_cost(self.provider_id, model, in_tok, out_tok),
                latency_ms=latency,
                success=True,
            )
        except Exception as e:
            latency = (time.time() - start_time) * 1000
            logger.error("%s translation failed: %s", self.name, e)
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

    def _get_default_model(self) -> str:
        """Override in subclass to provide a sensible default model."""
        return ""
