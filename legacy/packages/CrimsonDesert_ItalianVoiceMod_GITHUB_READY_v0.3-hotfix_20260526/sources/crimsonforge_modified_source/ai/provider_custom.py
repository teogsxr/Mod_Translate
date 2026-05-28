"""Custom OpenAI-compatible endpoint provider.

Supports any server that exposes an OpenAI-compatible API.
"""

from ai.provider_openai_compat import OpenAICompatProvider


class CustomProvider(OpenAICompatProvider):
    name = "Custom (OpenAI Compatible)"
    provider_id = "custom"
    requires_api_key = False

    def __init__(self, api_key: str = "", base_url: str = "",
                 timeout: int = 60, max_retries: int = 3):
        super().__init__(api_key, base_url, timeout, max_retries)

    def _get_default_model(self) -> str:
        return ""
