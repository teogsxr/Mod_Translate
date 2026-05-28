"""Ollama local provider. OpenAI-compatible API at localhost."""

from ai.provider_openai_compat import OpenAICompatProvider


class OllamaProvider(OpenAICompatProvider):
    name = "Ollama"
    provider_id = "ollama"
    requires_api_key = False

    def __init__(self, api_key: str = "ollama", base_url: str = "http://localhost:11434/v1",
                 timeout: int = 120, max_retries: int = 1):
        super().__init__(api_key, base_url, timeout, max_retries)

    def _get_default_model(self) -> str:
        return "llama3.2"
