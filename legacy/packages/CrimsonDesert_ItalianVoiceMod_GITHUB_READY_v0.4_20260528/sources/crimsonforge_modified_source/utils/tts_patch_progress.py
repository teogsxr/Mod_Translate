"""Persistent resume state for Audio-tab TTS patch batches."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from utils.logger import get_logger

logger = get_logger("utils.tts_patch_progress")

_STATE_VERSION = 1
_VOLATILE_OPTION_KEYS = {"ref_audio_path"}


def _state_path() -> Path:
    return Path.home() / ".crimsonforge" / "tts_patch_progress.json"


def _entry_key(package_group: str, entry_path: str) -> str:
    path = entry_path.replace("\\", "/").lower()
    return f"{package_group}:{path}"


def _game_key(packages_path: str) -> str:
    return os.path.normcase(os.path.abspath(packages_path))


def _json_safe(value: Any):
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def build_patch_signature(
    text: str,
    provider_id: str,
    model_id: str,
    voice_id: str,
    language: str,
    speed: float,
    options: dict | None = None,
) -> str:
    """Hash the user-visible synthesis inputs for resume decisions."""
    stable_options = {
        key: value
        for key, value in (options or {}).items()
        if key not in _VOLATILE_OPTION_KEYS
    }
    payload = {
        "text": text,
        "provider_id": provider_id,
        "model_id": model_id,
        "voice_id": voice_id,
        "language": language,
        "speed": round(float(speed), 6),
        "options": _json_safe(stable_options),
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class TTSPatchProgress:
    """Read and atomically update completed Audio-tab batch patch entries."""

    def __init__(self, packages_path: str):
        self.path = _state_path()
        self._game_key = _game_key(packages_path)
        self._state = self._load()
        games = self._state.setdefault("games", {})
        game = games.setdefault(self._game_key, {})
        self._completed = game.setdefault("completed", {})

    def is_completed(self, package_group: str, entry_path: str, signature: str) -> bool:
        record = self._completed.get(_entry_key(package_group, entry_path))
        if isinstance(record, dict) and record.get("force_regenerate_reason"):
            return False
        return isinstance(record, dict) and (
            record.get("manual") is True or record.get("signature") == signature
        )

    def get_record(self, package_group: str, entry_path: str) -> dict | None:
        record = self._completed.get(_entry_key(package_group, entry_path))
        return record if isinstance(record, dict) else None

    def has_completed_record(self, package_group: str, entry_path: str) -> bool:
        record = self.get_record(package_group, entry_path)
        return bool(record and not record.get("force_regenerate_reason"))

    def is_manually_completed(self, package_group: str, entry_path: str) -> bool:
        record = self.get_record(package_group, entry_path)
        return bool(record and not record.get("force_regenerate_reason") and record.get("manual") is True)

    def mark_completed(
        self,
        package_group: str,
        entry_path: str,
        signature: str,
        *,
        provider_id: str,
        model_id: str,
        language: str,
    ) -> None:
        self._completed[_entry_key(package_group, entry_path)] = {
            "signature": signature,
            "provider_id": provider_id,
            "model_id": model_id,
            "language": language,
            "completed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        self._save()

    def mark_manual_completed(self, package_group: str, entry_path: str, reason: str) -> None:
        """Mark a legacy patch as complete when its original TTS inputs are unknown."""
        self._completed[_entry_key(package_group, entry_path)] = {
            "manual": True,
            "reason": reason,
            "completed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        self._save()

    def mark_manual_completed_many(
        self,
        entries: list[tuple[str, str]],
        reason: str,
    ) -> int:
        """Atomically mark multiple legacy entries complete."""
        completed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        count = 0
        for package_group, entry_path in entries:
            self._completed[_entry_key(package_group, entry_path)] = {
                "manual": True,
                "reason": reason,
                "completed_at": completed_at,
            }
            count += 1
        if count:
            self._save()
        return count

    def _load(self) -> dict:
        if not self.path.is_file():
            return {"version": _STATE_VERSION, "games": {}}
        try:
            with self.path.open("r", encoding="utf-8") as f:
                state = json.load(f)
        except (OSError, json.JSONDecodeError, TypeError) as e:
            logger.warning("Ignoring invalid TTS patch progress file %s: %s", self.path, e)
            return {"version": _STATE_VERSION, "games": {}}
        if not isinstance(state, dict) or state.get("version") != _STATE_VERSION:
            return {"version": _STATE_VERSION, "games": {}}
        if not isinstance(state.get("games"), dict):
            state["games"] = {}
        return state

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=f"{self.path.stem}_",
            suffix=".tmp",
            dir=str(self.path.parent),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self._state, f, indent=2, ensure_ascii=False, sort_keys=True)
            os.replace(tmp_name, self.path)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
