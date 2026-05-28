"""Dynamic model loading from provider APIs.

Fetches available models from each provider and caches them.
Models are never hardcoded - always loaded from the actual API.
"""

from typing import Optional
from ai.provider_base import ModelInfo, AIProviderBase
from ai.provider_registry import ProviderRegistry
from utils.logger import get_logger

logger = get_logger("ai.model_loader")


class ModelLoader:
    """Loads and caches models from AI providers."""

    def __init__(self, registry: ProviderRegistry):
        self._registry = registry
        self._cache: dict[str, list[ModelInfo]] = {}

    def load_models(self, provider_id: str, force_refresh: bool = False) -> list[ModelInfo]:
        """Load models from a provider, using cache if available.

        Args:
            provider_id: Provider ID to load models from.
            force_refresh: If True, bypass cache and fetch fresh list.

        Returns:
            List of ModelInfo objects.
        """
        if not force_refresh and provider_id in self._cache:
            return self._cache[provider_id]

        provider = self._registry.get_provider(provider_id)
        try:
            models = provider.list_models()
            self._cache[provider_id] = models
            logger.info(
                "Loaded %d models from %s",
                len(models), provider_id,
            )
            return models
        except Exception as e:
            logger.error("Failed to load models from %s: %s", provider_id, e)
            if provider_id in self._cache:
                return self._cache[provider_id]
            raise

    def get_model(self, provider_id: str, model_id: str) -> Optional[ModelInfo]:
        """Get a specific model by provider and model ID."""
        models = self.load_models(provider_id)
        for m in models:
            if m.model_id == model_id:
                return m
        return None

    def clear_cache(self, provider_id: str = "") -> None:
        """Clear the model cache for a provider, or all caches."""
        if provider_id:
            self._cache.pop(provider_id, None)
        else:
            self._cache.clear()

    def load_all_enabled(self) -> dict[str, list[ModelInfo]]:
        """Load models from all enabled providers."""
        results = {}
        for provider_id in self._registry.list_enabled_provider_ids():
            try:
                results[provider_id] = self.load_models(provider_id)
            except Exception as e:
                logger.warning("Skipping %s: %s", provider_id, e)
                results[provider_id] = []
        return results
