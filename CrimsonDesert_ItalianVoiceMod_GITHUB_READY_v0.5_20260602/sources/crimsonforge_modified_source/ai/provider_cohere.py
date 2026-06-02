"""Cohere provider (Command A, Command R+).

Uses the official cohere SDK (not OpenAI-compatible).
"""

import time
import cohere

from ai.provider_base import (
    AIProviderBase, ModelInfo, TranslationResult, ConnectionResult,
)
from utils.logger import get_logger

logger = get_logger("ai.cohere")


class CohereProvider(AIProviderBase):
    name = "Cohere"
    provider_id = "cohere"
    requires_api_key = True
    supports_openai_compat = False

    def __init__(self, api_key: str = "", base_url: str = "https://api.cohere.com/v2",
                 timeout: int = 60, max_retries: int = 3):
        super().__init__(api_key, base_url, timeout, max_retries)
        self._client = None

    def _get_client(self) -> cohere.ClientV2:
        if self._client is None:
            self._client = cohere.ClientV2(
                api_key=self._api_key,
                timeout=self._timeout,
            )
        return self._client

    def list_models(self) -> list[ModelInfo]:
        try:
            client = self._get_client()
            response = client.models.list()
            models = []
            for m in response.models:
                if hasattr(m, "name") and m.name:
                    models.append(ModelInfo(
                        model_id=m.name,
                        name=m.name,
                        provider=self.provider_id,
                    ))
            models.sort(key=lambda x: x.model_id)
            return models
        except Exception as e:
            logger.error("Failed to list Cohere models: %s", e)
            raise ConnectionError(
                f"Failed to list Cohere models: {e}. "
                f"Check your API key."
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
            model = "command-a-03-2025"

        if not system_prompt:
            from ai.default_prompt import get_default_system_prompt
            system_prompt = get_default_system_prompt(source_lang, target_lang)

        messages = []
        if context:
            messages.append({"role": "user", "content": f"Context: {context}"})
            messages.append({"role": "assistant", "content": "Understood."})
        messages.append({"role": "user", "content": text})

        start_time = time.time()
        try:
            client = self._get_client()
            response = client.chat(
                model=model,
                messages=messages,
                system=system_prompt,
                temperature=0.3,
            )
            latency = (time.time() - start_time) * 1000

            translated = ""
            if response.message and response.message.content:
                for block in response.message.content:
                    if hasattr(block, "text"):
                        translated += block.text
            translated = translated.strip()

            input_tokens = 0
            output_tokens = 0
            if response.usage:
                if hasattr(response.usage, "billed_units"):
                    input_tokens = getattr(response.usage.billed_units, "input_tokens", 0) or 0
                    output_tokens = getattr(response.usage.billed_units, "output_tokens", 0) or 0

            return TranslationResult(
                translated_text=translated,
                source_text=text,
                source_lang=source_lang,
                target_lang=target_lang,
                model_used=model,
                provider=self.provider_id,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=input_tokens + output_tokens,
                latency_ms=latency,
                success=True,
            )
        except Exception as e:
            latency = (time.time() - start_time) * 1000
            logger.error("Cohere translation failed: %s", e)
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
