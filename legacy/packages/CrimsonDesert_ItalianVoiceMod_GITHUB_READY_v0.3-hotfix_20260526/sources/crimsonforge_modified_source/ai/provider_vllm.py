"""vLLM self-hosted provider. OpenAI-compatible API."""

from ai.provider_openai_compat import OpenAICompatProvider


class VllmProvider(OpenAICompatProvider):
    name = "vLLM"
    provider_id = "vllm"
    requires_api_key = False

    def __init__(self, api_key: str = "", base_url: str = "http://localhost:8000/v1",
                 timeout: int = 120, max_retries: int = 1):
        super().__init__(api_key or "not-needed", base_url, timeout, max_retries)

    def _get_default_model(self) -> str:
        return ""
