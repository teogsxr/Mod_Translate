"""OpenAI provider (GPT-5.4, etc.).

Uses the official openai SDK. This is the native provider - many other
providers use OpenAI-compatible APIs and share similar code.
"""

import time
from openai import OpenAI

from ai.provider_base import (
    AIProviderBase, ModelInfo, TranslationResult, ConnectionResult,
)
from utils.logger import get_logger

logger = get_logger("ai.openai")


class OpenAIProvider(AIProviderBase):
    name = "OpenAI"
    provider_id = "openai"
    requires_api_key = True
    supports_openai_compat = True

    def __init__(self, api_key: str = "", base_url: str = "https://api.openai.com/v1",
                 timeout: int = 60, max_retries: int = 3):
        super().__init__(api_key, base_url, timeout, max_retries)
        self._client = None

    def _get_client(self) -> OpenAI:
        if self._client is None or self._client.api_key != self._api_key:
            self._client = OpenAI(
                api_key=self._api_key,
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
            logger.error("Failed to list OpenAI models: %s", e)
            raise ConnectionError(
                f"Failed to list OpenAI models: {e}. "
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
            model = "gpt-4o"

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

            return TranslationResult(
                translated_text=translated,
                source_text=text,
                source_lang=source_lang,
                target_lang=target_lang,
                model_used=model,
                provider=self.provider_id,
                input_tokens=usage.prompt_tokens if usage else 0,
                output_tokens=usage.completion_tokens if usage else 0,
                total_tokens=usage.total_tokens if usage else 0,
                latency_ms=latency,
                success=True,
            )
        except Exception as e:
            latency = (time.time() - start_time) * 1000
            logger.error("OpenAI translation failed: %s", e)
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
