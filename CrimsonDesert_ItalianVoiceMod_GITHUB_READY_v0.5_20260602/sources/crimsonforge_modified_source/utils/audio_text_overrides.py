"""User-maintained TTS text overrides for audio rows without PALOC text."""

from __future__ import annotations

import json
import os
import tempfile
from functools import lru_cache
from pathlib import Path

from utils.logger import get_logger

logger = get_logger("utils.audio_text_overrides")

_STATE_VERSION = 1


def override_path() -> Path:
    return Path.home() / ".crimsonforge" / "audio_text_overrides.json"


def _entry_key(package_group: str, entry_path: str) -> str:
    normalized = (entry_path or "").replace("\\", "/").lower()
    return f"{package_group}:{normalized}"


def load_audio_text_overrides(path: str | Path | None = None) -> dict:
    source = Path(path) if path else override_path()
    if not source.is_file():
        return {}
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError) as e:
        logger.warning("Ignoring invalid audio text override file %s: %s", source, e)
        return {}
    if not isinstance(payload, dict) or payload.get("version") != _STATE_VERSION:
        return {}
    entries = payload.get("entries", {})
    return entries if isinstance(entries, dict) else {}


@lru_cache(maxsize=1)
def _cached_audio_text_overrides() -> dict:
    return load_audio_text_overrides()


def get_audio_text_override(
    package_group: str,
    entry_path: str,
    language_code: str,
    *,
    path: str | Path | None = None,
) -> str:
    entries = load_audio_text_overrides(path) if path else _cached_audio_text_overrides()
    record = entries.get(_entry_key(package_group, entry_path))
    if not isinstance(record, dict):
        return ""
    texts = record.get("texts", {})
    if not isinstance(texts, dict):
        return ""

    lang = (language_code or "").strip().lower()
    if not lang:
        return ""
    direct = texts.get(lang)
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    for key, value in texts.items():
        if isinstance(key, str) and key.lower().startswith(lang):
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def upsert_audio_text_override(
    package_group: str,
    entry_path: str,
    *,
    language_code: str,
    text: str,
    source_language: str = "",
    source_transcript: str = "",
    metadata: dict | None = None,
    path: str | Path | None = None,
) -> None:
    target = Path(path) if path else override_path()
    payload = _load_payload(target)
    entries = payload.setdefault("entries", {})
    record = entries.setdefault(_entry_key(package_group, entry_path), {})
    texts = record.setdefault("texts", {})
    texts[language_code] = text
    if source_language:
        record["source_language"] = source_language
    if source_transcript:
        record["source_transcript"] = source_transcript
    if metadata:
        stored_metadata = record.setdefault("metadata", {})
        stored_metadata.update(metadata)
    _atomic_write_json(target, payload)
    if path is None:
        _cached_audio_text_overrides.cache_clear()


def _load_payload(path: Path) -> dict:
    entries = load_audio_text_overrides(path)
    return {
        "version": _STATE_VERSION,
        "entries": entries,
    }


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f"{path.stem}_",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False, sort_keys=True)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
