"""Application configuration manager.

Loads, saves, and provides access to all application settings.
Settings are stored in a JSON file at ~/.crimsonforge/settings.json.
Nothing is hardcoded - all values come from config or user input.
"""

import json
import os
import copy
from pathlib import Path
from typing import Any

from utils.app_paths import data_path


class ConfigManager:
    """Manages application configuration with JSON persistence.

    Provides nested key access via dot-notation (e.g., 'ai_providers.openai.api_key'),
    observer pattern for settings changes, and atomic file writes.
    """

    CONFIG_DIR_NAME = ".crimsonforge"
    CONFIG_FILE_NAME = "settings.json"

    def __init__(self, config_dir: str = ""):
        if config_dir:
            self._config_dir = Path(config_dir)
        else:
            self._config_dir = Path.home() / self.CONFIG_DIR_NAME

        self._config_path = self._config_dir / self.CONFIG_FILE_NAME
        self._data: dict = {}
        self._observers: list = []
        self._defaults = self._build_defaults()
        self.load()

    def _build_defaults(self) -> dict:
        defaults = self._build_embedded_defaults()
        try:
            defaults_path = data_path("default_settings.json")
            with open(defaults_path, "r", encoding="utf-8") as f:
                file_defaults = json.load(f)
            if isinstance(file_defaults, dict):
                defaults = self._merge_with_defaults(defaults, file_defaults)
        except (OSError, json.JSONDecodeError, TypeError):
            pass
        return defaults

    @staticmethod
    def _build_embedded_defaults() -> dict:
        return {
            "version": "1.0.0",
            "general": {
                "theme": "dark",
                "language": "en",
                "last_game_path": "",
                "last_output_path": "",
                "recent_files": []
            },
            "ui": {
                "search_history": {}
            },
            "ai_providers": {
                "openai": {
                    "enabled": True,
                    "api_key": "",
                    "base_url": "https://api.openai.com/v1",
                    "default_model": "",
                    "timeout_seconds": 60,
                    "max_retries": 3
                },
                "anthropic": {
                    "enabled": True,
                    "api_key": "",
                    "base_url": "https://api.anthropic.com/v1",
                    "default_model": "",
                    "timeout_seconds": 60,
                    "max_retries": 3
                },
                "gemini": {
                    "enabled": True,
                    "api_key": "",
                    "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
                    "default_model": "",
                    "timeout_seconds": 60,
                    "max_retries": 3
                },
                "deepseek": {
                    "enabled": True,
                    "api_key": "",
                    "base_url": "https://api.deepseek.com",
                    "default_model": "",
                    "timeout_seconds": 60,
                    "max_retries": 3
                },
                "ollama": {
                    "enabled": True,
                    "api_key": "ollama",
                    "base_url": "http://localhost:11434/v1",
                    "default_model": "",
                    "timeout_seconds": 120,
                    "max_retries": 1
                },
                "vllm": {
                    "enabled": False,
                    "api_key": "",
                    "base_url": "http://localhost:8000/v1",
                    "default_model": "",
                    "timeout_seconds": 120,
                    "max_retries": 1
                },
                "mistral": {
                    "enabled": True,
                    "api_key": "",
                    "base_url": "https://api.mistral.ai/v1",
                    "default_model": "",
                    "timeout_seconds": 60,
                    "max_retries": 3
                },
                "cohere": {
                    "enabled": True,
                    "api_key": "",
                    "base_url": "https://api.cohere.com/v2",
                    "default_model": "",
                    "timeout_seconds": 60,
                    "max_retries": 3
                },
                "custom": {
                    "enabled": False,
                    "api_key": "",
                    "base_url": "",
                    "default_model": "",
                    "timeout_seconds": 60,
                    "max_retries": 3
                },
                "deepl": {
                    "enabled": False,
                    "api_key": "",
                    "base_url": "https://api.deepl.com/v2",
                    "default_model": "deepl",
                    "timeout_seconds": 60,
                    "max_retries": 3
                }
            },
            "translation": {
                "autosave_enabled": True,
                "autosave_interval_seconds": 30,
                "batch_size": 10,
                "batch_delay_ms": 500,
                "max_concurrent_requests": 3,
                "default_source_lang": "en",
                "default_target_lang": "",
                "system_prompt": "",
                "user_prompt_template": "",
                "projects_dir": ""
            },
            "tts": {
                "elevenlabs_tts_api_key": "",
                "azure_tts_api_key": "",
                "azure_region": "eastus",
                "omnivoice_api_key": "",
                "omnivoice_base_url": "http://127.0.0.1:8880",
                "omnivoice_tts_default_model": "omnivoice",
                "omnivoice_timeout_seconds": 120,
                "omnivoice_num_step": 32,
                "omnivoice_guidance_scale": 3.0,
                "omnivoice_denoise": True,
                "omnivoice_duration_seconds": 0.0,
                "omnivoice_t_shift": 0.1,
                "omnivoice_position_temperature": 5.0,
                "omnivoice_class_temperature": 0.0,
                "omnivoice_clone_mode": "one_shot",
                "omnivoice_profile_name": "",
                "omnivoice_voice_mode": "auto",
                "omnivoice_auto_reference": True,
                "omnivoice_refresh_profile": True
            },
            "repack": {
                "auto_backup": True,
                "verify_after_repack": True,
                "preserve_timestamps": True,
                "backup_dir": ""
            },
            "advanced": {
                "log_level": "INFO",
                "log_file": "",
                "debug_mode": False
            }
        }

    def load(self) -> None:
        """Load settings from disk. Creates defaults if file doesn't exist."""
        if self._config_path.exists():
            try:
                with open(self._config_path, "r", encoding="utf-8-sig") as f:
                    loaded = json.load(f)
                self._data = self._merge_with_defaults(self._defaults, loaded)
            except (json.JSONDecodeError, OSError) as e:
                raise ConfigLoadError(
                    f"Failed to load settings from {self._config_path}: {e}. "
                    f"The file may be corrupted. Delete it to reset to defaults, "
                    f"or fix the JSON syntax error."
                ) from e
        else:
            self._data = copy.deepcopy(self._defaults)
            self.save()

    def save(self) -> None:
        """Atomically save settings to disk."""
        self._config_dir.mkdir(parents=True, exist_ok=True)
        tmp_path = self._config_path.with_suffix(".tmp")
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=4, ensure_ascii=False)
            tmp_path.replace(self._config_path)
        except OSError as e:
            if tmp_path.exists():
                tmp_path.unlink()
            raise ConfigSaveError(
                f"Failed to save settings to {self._config_path}: {e}. "
                f"Check that the directory {self._config_dir} is writable."
            ) from e

    def get(self, key: str, default: Any = None) -> Any:
        """Get a config value using dot notation.

        Example: config.get('ai_providers.openai.api_key')
        """
        keys = key.split(".")
        current = self._data
        for k in keys:
            if isinstance(current, dict) and k in current:
                current = current[k]
            else:
                return default
        return current

    def set(self, key: str, value: Any) -> None:
        """Set a config value using dot notation and notify observers.

        Example: config.set('ai_providers.openai.api_key', 'sk-xxx')
        """
        keys = key.split(".")
        current = self._data
        for k in keys[:-1]:
            if k not in current or not isinstance(current[k], dict):
                current[k] = {}
            current = current[k]
        old_value = current.get(keys[-1])
        current[keys[-1]] = value
        if old_value != value:
            self._notify_observers(key, old_value, value)

    def get_section(self, section: str) -> dict:
        """Get an entire config section as a dict."""
        result = self.get(section)
        if isinstance(result, dict):
            return copy.deepcopy(result)
        return {}

    def set_section(self, section: str, data: dict) -> None:
        """Replace an entire config section."""
        self.set(section, data)

    def reset_to_defaults(self) -> None:
        """Reset all settings to defaults."""
        self._data = copy.deepcopy(self._defaults)
        self._notify_observers("*", None, None)

    def add_observer(self, callback) -> None:
        """Register a callback for settings changes.

        Callback signature: callback(key: str, old_value, new_value)
        """
        if callback not in self._observers:
            self._observers.append(callback)

    def remove_observer(self, callback) -> None:
        """Unregister a settings change callback."""
        if callback in self._observers:
            self._observers.remove(callback)

    def _notify_observers(self, key: str, old_value: Any, new_value: Any) -> None:
        for observer in self._observers:
            try:
                observer(key, old_value, new_value)
            except Exception:
                pass

    def _merge_with_defaults(self, defaults: dict, loaded: dict) -> dict:
        """Deep merge loaded config with defaults, keeping loaded values
        but adding any new default keys."""
        result = copy.deepcopy(defaults)
        for key, value in loaded.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = self._merge_with_defaults(result[key], value)
            else:
                result[key] = value
        return result

    @property
    def config_dir(self) -> Path:
        return self._config_dir

    @property
    def config_path(self) -> Path:
        return self._config_path

    @property
    def data(self) -> dict:
        return copy.deepcopy(self._data)


class ConfigLoadError(Exception):
    """Raised when settings file cannot be loaded."""
    pass


class ConfigSaveError(Exception):
    """Raised when settings file cannot be saved."""
    pass
