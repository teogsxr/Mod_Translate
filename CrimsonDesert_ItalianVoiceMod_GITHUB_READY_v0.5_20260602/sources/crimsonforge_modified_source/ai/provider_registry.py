"""Provider registry - discovers, loads, and manages all AI providers.

Central registry that maps provider IDs to provider instances,
handles configuration from settings, and provides provider enumeration.
"""

from typing import Optional

from ai.provider_base import AIProviderBase
from ai.provider_openai import OpenAIProvider
from ai.provider_anthropic import AnthropicProvider
from ai.provider_gemini import GeminiProvider
from ai.provider_deepseek import DeepSeekProvider
from ai.provider_ollama import OllamaProvider
from ai.provider_vllm import VllmProvider
from ai.provider_mistral import MistralProvider
from ai.provider_cohere import CohereProvider
from ai.provider_custom import CustomProvider
from ai.provider_deepl import DeepLProvider
from utils.logger import get_logger

logger = get_logger("ai.registry")

PROVIDER_CLASSES: dict[str, type] = {
    "openai": OpenAIProvider,
    "anthropic": AnthropicProvider,
    "gemini": GeminiProvider,
    "deepseek": DeepSeekProvider,
    "ollama": OllamaProvider,
    "vllm": VllmProvider,
    "mistral": MistralProvider,
    "cohere": CohereProvider,
    "custom": CustomProvider,
    "deepl": DeepLProvider,
}


class ProviderRegistry:
    """Central registry for all AI providers."""

    def __init__(self):
        self._providers: dict[str, AIProviderBase] = {}
        self._enabled: dict[str, bool] = {}

    def initialize_from_config(self, ai_config: dict) -> None:
        """Initialize all providers from the settings config.

        Args:
            ai_config: The 'ai_providers' section from settings.
        """
        self._providers.clear()
        self._enabled.clear()

        for provider_id in ai_config:
            if provider_id not in PROVIDER_CLASSES:
                logger.warning("Unknown provider in config: %s", provider_id)

        for provider_id, cls in PROVIDER_CLASSES.items():
            provider_settings = ai_config.get(provider_id, {})
            if not isinstance(provider_settings, dict):
                provider_settings = {}

            enabled = provider_settings.get("enabled", False)
            self._enabled[provider_id] = enabled

            provider = cls(
                api_key=provider_settings.get("api_key", ""),
                base_url=provider_settings.get("base_url", ""),
                timeout=provider_settings.get("timeout_seconds", 60),
                max_retries=provider_settings.get("max_retries", 3),
            )
            self._providers[provider_id] = provider
            logger.info(
                "Registered provider: %s (enabled=%s)",
                provider_id, enabled,
            )

    def get_provider(self, provider_id: str) -> AIProviderBase:
        """Get a provider by ID."""
        if provider_id not in self._providers:
            raise ValueError(
                f"Provider '{provider_id}' not found. "
                f"Available providers: {list(self._providers.keys())}. "
                f"Check Settings → AI Providers."
            )
        return self._providers[provider_id]

    def get_enabled_providers(self) -> list[AIProviderBase]:
        """Get all enabled providers."""
        return [
            self._providers[pid]
            for pid, enabled in self._enabled.items()
            if enabled and pid in self._providers
        ]

    def get_all_providers(self) -> dict[str, AIProviderBase]:
        """Get all registered providers (enabled and disabled)."""
        return dict(self._providers)

    def is_enabled(self, provider_id: str) -> bool:
        return self._enabled.get(provider_id, False)

    def set_enabled(self, provider_id: str, enabled: bool) -> None:
        self._enabled[provider_id] = enabled

    def update_provider_config(self, provider_id: str, **kwargs) -> None:
        """Update a provider's configuration."""
        if provider_id in self._providers:
            self._providers[provider_id].configure(**kwargs)

    def list_provider_ids(self) -> list[str]:
        """List all registered provider IDs."""
        return list(self._providers.keys())

    def list_enabled_provider_ids(self) -> list[str]:
        """List enabled provider IDs."""
        return [pid for pid, enabled in self._enabled.items() if enabled]
