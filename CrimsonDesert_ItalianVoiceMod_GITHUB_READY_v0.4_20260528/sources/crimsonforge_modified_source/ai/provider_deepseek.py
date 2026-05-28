"""DeepSeek provider (V3.2, R1). OpenAI-compatible API."""

from ai.provider_openai_compat import OpenAICompatProvider


class DeepSeekProvider(OpenAICompatProvider):
    name = "DeepSeek"
    provider_id = "deepseek"
    requires_api_key = True

    def __init__(self, api_key: str = "", base_url: str = "https://api.deepseek.com",
                 timeout: int = 60, max_retries: int = 3):
        super().__init__(api_key, base_url, timeout, max_retries)

    def _get_default_model(self) -> str:
        return "deepseek-chat"
