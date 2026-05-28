"""Abstract base class for all AI providers.

Defines the common interface that every AI provider must implement:
list_models, translate, and test_connection.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ModelInfo:
    """Information about an available AI model."""
    model_id: str
    name: str
    provider: str
    context_window: int = 0
    max_output_tokens: int = 0
    supports_streaming: bool = False


@dataclass
class TranslationResult:
    """Result of a single translation request."""
    translated_text: str
    source_text: str
    source_lang: str
    target_lang: str
    model_used: str
    provider: str
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost_estimate: float = 0.0
    latency_ms: float = 0.0
    error: str = ""
    success: bool = True


@dataclass
class ConnectionResult:
    """Result of testing a provider connection."""
    connected: bool
    provider: str
    message: str
    models_available: int = 0
    error: str = ""


class AIProviderBase(ABC):
    """Abstract base class for AI translation providers.

    All providers implement this interface to enable dynamic
    provider switching in the UI.
    """

    name: str = ""
    provider_id: str = ""
    requires_api_key: bool = True
    supports_openai_compat: bool = False

    def __init__(self, api_key: str = "", base_url: str = "",
                 timeout: int = 60, max_retries: int = 3):
        self._api_key = api_key
        self._base_url = base_url
        self._timeout = timeout
        self._max_retries = max_retries

    @abstractmethod
    def list_models(self) -> list[ModelInfo]:
        """Fetch available models from the provider API.

        Models are loaded dynamically, never hardcoded.

        Returns:
            List of available ModelInfo objects.
        """
        ...

    @abstractmethod
    def translate(
        self,
        text: str,
        source_lang: str,
        target_lang: str,
        model: str = "",
        system_prompt: str = "",
        context: str = "",
    ) -> TranslationResult:
        """Translate a single text string.

        Args:
            text: Source text to translate.
            source_lang: Source language name/code.
            target_lang: Target language name/code.
            model: Model ID to use. Empty = provider default.
            system_prompt: System prompt for the AI.
            context: Additional context for better translation.

        Returns:
            TranslationResult with the translated text.
        """
        ...

    @abstractmethod
    def test_connection(self) -> ConnectionResult:
        """Test the connection to the provider.

        Returns:
            ConnectionResult indicating success or failure.
        """
        ...

    @property
    def api_key(self) -> str:
        return self._api_key

    @api_key.setter
    def api_key(self, value: str):
        self._api_key = value

    @property
    def base_url(self) -> str:
        return self._base_url

    @base_url.setter
    def base_url(self, value: str):
        self._base_url = value

    @property
    def timeout(self) -> int:
        return self._timeout

    @property
    def max_retries(self) -> int:
        return self._max_retries

    def configure(self, api_key: str = "", base_url: str = "",
                  timeout: int = 0, max_retries: int = 0) -> None:
        """Update provider configuration."""
        if api_key:
            self._api_key = api_key
        if base_url:
            self._base_url = base_url
        if timeout > 0:
            self._timeout = timeout
        if max_retries > 0:
            self._max_retries = max_retries
