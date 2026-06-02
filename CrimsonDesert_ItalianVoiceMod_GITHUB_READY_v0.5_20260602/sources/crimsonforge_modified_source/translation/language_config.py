"""Language definitions with script types and codes.

All language data is loaded from data/languages.json - never hardcoded.
"""

import json
import os
from dataclasses import dataclass
from typing import Optional

from utils.app_paths import data_path
from utils.logger import get_logger

logger = get_logger("translation.language_config")


@dataclass
class LanguageInfo:
    """Information about a language."""
    code: str
    name: str
    native_name: str
    script: str


class LanguageConfig:
    """Manages language definitions loaded from config file."""

    def __init__(self, languages_file: str = ""):
        if not languages_file:
            languages_file = str(data_path("languages.json"))

        self._languages: list[LanguageInfo] = []
        self._by_code: dict[str, LanguageInfo] = {}
        self._game_languages: list[str] = []
        self._load(languages_file)

    def _load(self, path: str) -> None:
        if not os.path.exists(path):
            raise FileNotFoundError(
                f"Languages config not found: {path}. "
                f"The data/languages.json file must exist."
            )
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        for entry in data.get("languages", []):
            lang = LanguageInfo(
                code=entry["code"],
                name=entry["name"],
                native_name=entry.get("native_name", entry["name"]),
                script=entry.get("script", "Latin"),
            )
            self._languages.append(lang)
            self._by_code[lang.code] = lang

        self._game_languages = data.get("game_languages", [])
        logger.info("Loaded %d languages (%d game languages)", len(self._languages), len(self._game_languages))

    def get_language(self, code: str) -> Optional[LanguageInfo]:
        return self._by_code.get(code)

    def get_all_languages(self) -> list[LanguageInfo]:
        return list(self._languages)

    def get_game_languages(self) -> list[LanguageInfo]:
        return [self._by_code[code] for code in self._game_languages if code in self._by_code]

    def get_language_names(self) -> list[str]:
        return [lang.name for lang in self._languages]

    def get_code_by_name(self, name: str) -> str:
        for lang in self._languages:
            if lang.name == name:
                return lang.code
        return ""

    def get_name_by_code(self, code: str) -> str:
        lang = self._by_code.get(code)
        return lang.name if lang else code
