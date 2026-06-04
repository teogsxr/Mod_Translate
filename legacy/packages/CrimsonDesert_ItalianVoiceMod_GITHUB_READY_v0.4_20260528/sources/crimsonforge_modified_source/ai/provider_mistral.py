"""Mistral AI provider. OpenAI-compatible API."""

from ai.provider_openai_compat import OpenAICompatProvider


class MistralProvider(OpenAICompatProvider):
    name = "Mistral AI"
    provider_id = "mistral"
    requires_api_key = True

    def __init__(self, api_key: str = "", base_url: str = "https://api.mistral.ai/v1",
                 timeout: int = 60, max_retries: int = 3):
        super().__init__(api_key, base_url, timeout, max_retries)

    def _get_default_model(self) -> str:
        return "mistral-large-latest"
