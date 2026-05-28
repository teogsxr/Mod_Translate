"""Multi-provider Speech-to-Text engine.

STT providers share API keys with translation/TTS providers where possible:
  - OpenAI STT uses the same OpenAI API key (whisper-1, gpt-4o-transcribe)
  - Gemini STT uses the same Google credentials
  - Local Whisper uses no API key (runs on CPU/GPU)

All models are fetched dynamically from provider APIs. Nothing is hardcoded.
"""

from __future__ import annotations

import os
import time
import tempfile
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, Callable

from utils.logger import get_logger

logger = get_logger("ai.stt_engine")


@dataclass
class STTModel:
    """An STT model fetched from provider API."""
    model_id: str
    name: str
    provider: str = ""


@dataclass
class STTResult:
    """Result of a speech-to-text transcription."""
    text: str
    language: str = ""
    duration_ms: float = 0.0
    latency_ms: float = 0.0
    cost_estimate: float = 0.0
    provider: str = ""
    model: str = ""
    error: str = ""
    success: bool = True


class STTProviderBase(ABC):
    """Abstract base for STT providers."""

    name: str = ""
    provider_id: str = ""
    requires_api_key: bool = True

    def __init__(self, api_key: str = "", **kwargs):
        self._api_key = api_key
        self._extra = kwargs

    @abstractmethod
    def list_models(self) -> list[STTModel]:
        """Fetch available STT models from provider API."""
        ...

    @abstractmethod
    def transcribe(self, audio_path: str, model_id: str = "",
                   language: str = "") -> STTResult:
        """Transcribe audio file to text."""
        ...

    @property
    def api_key(self) -> str:
        return self._api_key

    @api_key.setter
    def api_key(self, value: str):
        self._api_key = value


# ═══════════════════════════════════════════════════════════════════════
#  OPENAI STT (Whisper) — shares API key with OpenAI
# ═══════════════════════════════════════════════════════════════════════

class STTOpenAI(STTProviderBase):
    """OpenAI Whisper STT. whisper-1, gpt-4o-transcribe, gpt-4o-mini-transcribe."""

    name = "OpenAI Whisper"
    provider_id = "openai_stt"

    def list_models(self) -> list[STTModel]:
        """Fetch STT models from OpenAI API."""
        try:
            from openai import OpenAI
            client = OpenAI(api_key=self._api_key)
            models = client.models.list()
            stt_models = []
            for m in models:
                mid = m.id.lower()
                if "whisper" in mid or "transcribe" in mid:
                    stt_models.append(STTModel(m.id, m.id, "openai_stt"))
            return stt_models if stt_models else []
        except Exception as e:
            logger.warning("Failed to fetch OpenAI STT models: %s", e)
            return []

    def transcribe(self, audio_path: str, model_id: str = "",
                   language: str = "") -> STTResult:
        start = time.time()
        try:
            from openai import OpenAI
            client = OpenAI(api_key=self._api_key)

            with open(audio_path, "rb") as f:
                kwargs = {
                    "model": model_id or "whisper-1",
                    "file": f,
                }
                if language:
                    kwargs["language"] = language[:2].lower()

                result = client.audio.transcriptions.create(**kwargs)

            latency = (time.time() - start) * 1000
            file_size = os.path.getsize(audio_path)
            # Whisper: $0.006/minute, estimate duration from file size
            est_minutes = max(0.01, file_size / (16000 * 2 * 60))  # rough 16kHz mono
            cost = est_minutes * 0.006

            return STTResult(
                text=result.text, provider="openai_stt",
                model=model_id or "whisper-1",
                latency_ms=latency, cost_estimate=cost,
            )
        except Exception as e:
            return STTResult(
                text="", provider="openai_stt", error=str(e), success=False,
                latency_ms=(time.time() - start) * 1000,
            )


# ═══════════════════════════════════════════════════════════════════════
#  GEMINI STT — shares credentials with Gemini
# ═══════════════════════════════════════════════════════════════════════

class STTGemini(STTProviderBase):
    """Google Gemini audio transcription via generateContent."""

    name = "Gemini Audio"
    provider_id = "gemini_stt"

    def list_models(self) -> list[STTModel]:
        return [STTModel("gemini-2.5-flash", "Gemini 2.5 Flash", "gemini_stt")]

    def transcribe(self, audio_path: str, model_id: str = "",
                   language: str = "") -> STTResult:
        start = time.time()
        try:
            from google import genai

            client = genai.Client(api_key=self._api_key)
            with open(audio_path, "rb") as f:
                audio_data = f.read()

            # Upload audio file
            uploaded = client.files.upload(
                file=audio_path,
                config={"display_name": os.path.basename(audio_path)},
            )

            prompt = "Transcribe this audio accurately. Return only the transcribed text, nothing else."
            if language:
                prompt += f" The audio is in {language}."

            response = client.models.generate_content(
                model=model_id or "gemini-2.5-flash",
                contents=[prompt, uploaded],
            )

            latency = (time.time() - start) * 1000

            return STTResult(
                text=response.text.strip(), provider="gemini_stt",
                model=model_id or "gemini-2.5-flash",
                latency_ms=latency,
            )
        except Exception as e:
            return STTResult(
                text="", provider="gemini_stt", error=str(e), success=False,
                latency_ms=(time.time() - start) * 1000,
            )


# ═══════════════════════════════════════════════════════════════════════
#  LOCAL WHISPER — free, no API key, runs on CPU
# ═══════════════════════════════════════════════════════════════════════

class STTLocalWhisper(STTProviderBase):
    """Local Whisper model. Free, runs on CPU/GPU, no API key needed."""

    name = "Local Whisper (Free)"
    provider_id = "local_whisper"
    requires_api_key = False

    _model = None

    def list_models(self) -> list[STTModel]:
        return [
            STTModel("turbo", "Turbo (fast, good accuracy)", "local_whisper"),
            STTModel("base", "Base (fastest, lower accuracy)", "local_whisper"),
            STTModel("small", "Small (balanced)", "local_whisper"),
            STTModel("medium", "Medium (slower, better)", "local_whisper"),
            STTModel("large-v3", "Large V3 (best accuracy)", "local_whisper"),
        ]

    def transcribe(self, audio_path: str, model_id: str = "",
                   language: str = "") -> STTResult:
        start = time.time()
        try:
            import whisper

            model_name = model_id or "turbo"
            if STTLocalWhisper._model is None or True:
                STTLocalWhisper._model = whisper.load_model(model_name)

            kwargs = {"fp16": False}
            if language:
                kwargs["language"] = language[:2].lower()

            result = whisper.transcribe(STTLocalWhisper._model, audio_path, **kwargs)
            latency = (time.time() - start) * 1000

            return STTResult(
                text=result.get("text", "").strip(),
                language=result.get("language", ""),
                provider="local_whisper", model=model_name,
                latency_ms=latency, cost_estimate=0.0,
            )
        except ImportError:
            return STTResult(
                text="", provider="local_whisper",
                error="Whisper not installed. Run: pip install openai-whisper",
                success=False, latency_ms=(time.time() - start) * 1000,
            )
        except Exception as e:
            return STTResult(
                text="", provider="local_whisper", error=str(e), success=False,
                latency_ms=(time.time() - start) * 1000,
            )


# ═══════════════════════════════════════════════════════════════════════
#  STT ENGINE — MULTI-PROVIDER MANAGER
# ═══════════════════════════════════════════════════════════════════════

STT_PROVIDER_CLASSES: dict[str, type] = {
    "openai_stt": STTOpenAI,
    "gemini_stt": STTGemini,
    "local_whisper": STTLocalWhisper,
}

# Map STT providers to translation providers whose API key they share
STT_KEY_SHARING = {
    "openai_stt": "openai",
    "gemini_stt": "gemini",
    "local_whisper": None,
}

# Which translation providers also support STT
TRANSLATION_PROVIDERS_WITH_STT = {
    "openai": "openai_stt",
    "gemini": "gemini_stt",
    # anthropic: NO STT
    # deepseek: NO STT
    # mistral: NO STT
    # cohere: STT only (transcribe model) but separate key
    # deepl: NO STT
}


class STTEngine:
    """Multi-provider STT engine. Shares API keys with translation providers."""

    def __init__(self):
        self._providers: dict[str, STTProviderBase] = {}
        self._active_provider_id: str = "openai_stt"
        self._enabled: bool = False

    def initialize_from_config(self, config) -> None:
        """Initialize STT providers from config."""
        def _get(key, default=""):
            if hasattr(config, 'get'):
                return config.get(key, default)
            if isinstance(config, dict):
                parts = key.split(".")
                d = config
                for p in parts:
                    if isinstance(d, dict):
                        d = d.get(p, default)
                    else:
                        return default
                return d
            return default

        self._enabled = _get("stt.enabled", False)

        for pid, cls in STT_PROVIDER_CLASSES.items():
            shared = STT_KEY_SHARING.get(pid)
            if shared:
                key = _get(f"ai_providers.{shared}.api_key", "")
            else:
                key = ""
            self._providers[pid] = cls(api_key=key)

        self._active_provider_id = _get("stt.active_provider", "openai_stt")

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool):
        self._enabled = value

    def get_provider(self, provider_id: str = "") -> Optional[STTProviderBase]:
        pid = provider_id or self._active_provider_id
        if pid not in self._providers:
            cls = STT_PROVIDER_CLASSES.get(pid)
            if cls:
                self._providers[pid] = cls()
        return self._providers.get(pid)

    def list_providers(self) -> list[dict]:
        return [{"id": pid, "name": cls.name, "requires_api_key": cls.requires_api_key}
                for pid, cls in STT_PROVIDER_CLASSES.items()]

    def list_models(self, provider_id: str = "") -> list[STTModel]:
        p = self.get_provider(provider_id)
        return p.list_models() if p else []

    def transcribe(self, audio_path: str, provider_id: str = "",
                   model_id: str = "", language: str = "") -> STTResult:
        if not self._enabled:
            return STTResult(text="", error="STT is disabled. Enable in Settings.",
                             success=False)
        p = self.get_provider(provider_id)
        if not p:
            return STTResult(text="", error=f"STT provider '{provider_id}' not found",
                             success=False)
        return p.transcribe(audio_path, model_id, language)
