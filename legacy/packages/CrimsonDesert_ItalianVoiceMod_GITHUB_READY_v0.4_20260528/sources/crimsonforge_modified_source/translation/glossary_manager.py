"""Glossary manager for consistent proper noun translation.

Maintains a dictionary of proper nouns (character names, city names,
faction names, item names, etc.) with their fixed translations per
target language. The glossary is injected into every AI prompt to
ensure consistent translations across all 102K+ game strings.

Glossary is auto-extracted from paloc data by detecting short entries
that appear frequently in longer descriptions (proper noun pattern).
Users can edit, categorize, and translate each entry.

Storage: ~/.crimsonforge/glossary/<source_lang>_<target_lang>.json
"""

import json
import os
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional
from enum import Enum

from utils.logger import get_logger

logger = get_logger("translation.glossary")

GLOSSARY_DIR = os.path.join(os.path.expanduser("~"), ".crimsonforge", "glossary")


class GlossaryCategory(Enum):
    CHARACTER = "character"
    LOCATION = "location"
    FACTION = "faction"
    ITEM = "item"
    CREATURE = "creature"
    UI_LABEL = "ui_label"
    SKILL = "skill"
    QUEST = "quest"
    MATERIAL = "material"
    OTHER = "other"
    SKIP = "skip"


CATEGORY_LABELS = {
    GlossaryCategory.CHARACTER: "Character",
    GlossaryCategory.LOCATION: "Location",
    GlossaryCategory.FACTION: "Faction",
    GlossaryCategory.ITEM: "Item",
    GlossaryCategory.CREATURE: "Creature",
    GlossaryCategory.UI_LABEL: "UI Label",
    GlossaryCategory.SKILL: "Skill",
    GlossaryCategory.QUEST: "Quest",
    GlossaryCategory.MATERIAL: "Material",
    GlossaryCategory.OTHER: "Other",
    GlossaryCategory.SKIP: "Skip",
}


@dataclass
class GlossaryEntry:
    """A single glossary term with its translation."""
    term: str
    translation: str = ""
    category: GlossaryCategory = GlossaryCategory.OTHER
    mentions: int = 0
    entry_count: int = 0
    locked: bool = False
    notes: str = ""

    def to_dict(self) -> dict:
        return {
            "term": self.term,
            "translation": self.translation,
            "category": self.category.value,
            "mentions": self.mentions,
            "entry_count": self.entry_count,
            "locked": self.locked,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "GlossaryEntry":
        entry = cls(
            term=data["term"],
            translation=data.get("translation", ""),
            mentions=data.get("mentions", 0),
            entry_count=data.get("entry_count", 0),
            locked=data.get("locked", False),
            notes=data.get("notes", ""),
        )
        try:
            entry.category = GlossaryCategory(data.get("category", "other"))
        except ValueError:
            entry.category = GlossaryCategory.OTHER
        return entry


# Known proper nouns from crimsondesert.app wiki + Fextralife (auto-categorized)
KNOWN_CHARACTERS = {
    "Kliff", "Oongka", "Yann", "Naira", "Duane", "Shane", "Marius",
    "Andrew", "Damiane", "Russo", "Jian", "Ludvig", "Adeline", "Adrina",
    "Tommaso", "Giath", "Tristan", "Gregor", "Gwen", "Myurdin",
    "Ross", "Giles", "Sebastian", "Bilwise", "Rulupee", "Antoni",
    "Jeffrey", "Billy", "Turnali", "Bianca", "Lauren", "Arlan",
    "Erich", "Ibano", "Bremer", "Shakatu", "Willian", "Boris",
    "Hubert", "Blix", "Carl", "Salvatore", "Grover", "Rhett",
    "Tina", "Alden", "Bran", "Delkin", "Renee", "Dahlia", "Bentley",
    "Annabella", "Merton", "Haldwin", "Alfred", "Edmond", "Grimrak",
    "Theoric", "Groks", "Bruna", "Ugmon", "Finley", "Prox", "Luke",
    "Ronald", "Silvan", "Otto", "Aldric", "Fritz", "Serge", "Tranan",
    "Ronnie", "Brice", "Grimnir", "Darroch", "Grania", "Grundir",
    "Octavius", "Alustin", "Martinus", "Matthias", "Muskan",
    "Cassius", "Draven", "Fortain", "Kailok", "Kearush", "Saigord",
    "Priscus", "Hexe Marie", "Walter Lanford", "Gwen Kraber",
    "Barden Middler", "Alan Serkis", "Leon Roberts", "Alistair Grace",
}
KNOWN_LOCATIONS = {
    "Hernand", "Pailune", "Demeniss", "Delesyia", "Pywel", "Abyss",
    "Calphade", "Varnia", "Ellimore", "Vellua", "Florindale", "Ivynook",
    "Scholastone", "Kharonso", "Senia", "Thalwynd", "Arboria",
    "Roothold", "Akapen", "Kweiden", "Ashclaw Keep", "Pailune Castle",
    "Reventine Monastery", "Greymane Camp", "Muckroot Ranch",
    "Bluemont Manor", "Oakenshield Manor", "Goldleaf Guildhouse",
    "Fort Warspike", "Fort Ironclad", "Fort Anvil", "Fort Perwin",
    "Longleaf Forest", "Windswept Plains", "Crimson Dunes",
    "Silver Wolf Mountain", "Steel Mountains", "Bay of Steel",
    "Kingshield Mountains", "Howling Hill", "Marni's Masterium",
}
KNOWN_FACTIONS = {
    "Greymane", "Greymanes", "Black Bear", "Black Bears",
    "Bleed Bandits", "House Celeste", "Reventines",
    "Demenissian Empire", "Bekker Guild", "Sydmon Clan",
    "Boltons", "Odeck Company",
}
KNOWN_BOSSES = {
    "Crimson Nightmare", "Tenebrum", "Reed Devil", "White Horn",
    "Snow Walker", "Titan", "Crowcaller", "Abyss Kutum",
    "Golden Star", "Sir Catfish", "One Armed Ludvig", "Awakened Ludvig",
    "Lava Myurdin", "Gregor the Halberd of Carnage",
    "Marni's Excavatron", "Queen Stoneback Crab",
}
KNOWN_MOUNTS = {
    "Herspia", "Brianto", "Cloudcart", "Blackstar Dragon",
    "Boarhand", "Jindo", "Saluki", "Royler", "Rokade",
    "Camora", "Priden", "Numont", "Pororin Cat",
}
KNOWN_SKILLS = {
    "Spinning Slash", "Forward Slash", "Armed Combat", "Blinding Flash",
    "Shield Bash", "Body Slam", "Charged Shot", "Clothesline",
    "Dropkick", "Evasive Shot", "Evasive Slash", "Flying Kick",
    "Giant Swing", "Grappling", "Lariat", "Leg Sweep", "Marksmanship",
    "Meteor Kick", "Multishot", "Pump Kick", "Quick Swap", "Restrain",
    "Rush", "Scissor Takedown", "Turning Slash", "Smiting Strike",
    "Sword Flurry", "Piercing Light", "Smiting Bolt", "Shield Toss",
    "Shield Sentinel", "Blade Sentinel", "Flurry of Kicks",
    "Evasive Smite", "Skystep", "Flame Rush", "Storm Pillar",
    "Hack and Slash", "Rampage", "Quaking Fury", "Raging Lightning",
    "Leaping Smash", "Scatter Shot", "Explosive Leap", "Flame Quake",
    "Storm Howl", "Lightning Pulse", "Lightning Surge", "Frost Mantle",
    "Storm Veil", "Flame Strike",
}


class GlossaryManager:
    """Manages glossary per source-target language pair."""

    def __init__(self):
        Path(GLOSSARY_DIR).mkdir(parents=True, exist_ok=True)
        self._entries: dict[str, GlossaryEntry] = {}
        self._source_lang = ""
        self._target_lang = ""

    def _glossary_path(self, source_lang: str, target_lang: str) -> str:
        return os.path.join(GLOSSARY_DIR, f"{source_lang}_{target_lang}.json")

    def load(self, source_lang: str, target_lang: str) -> int:
        """Load glossary for a language pair. Returns entry count."""
        self._source_lang = source_lang
        self._target_lang = target_lang
        self._entries = {}
        path = self._glossary_path(source_lang, target_lang)
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for entry_data in data.get("entries", []):
                ge = GlossaryEntry.from_dict(entry_data)
                self._entries[ge.term] = ge
            logger.info("Glossary loaded: %s -> %s (%d entries)", source_lang, target_lang, len(self._entries))
        return len(self._entries)

    def save(self) -> str:
        """Save current glossary to file."""
        path = self._glossary_path(self._source_lang, self._target_lang)
        data = {
            "version": "1.0.0",
            "source_lang": self._source_lang,
            "target_lang": self._target_lang,
            "entry_count": len(self._entries),
            "entries": [e.to_dict() for e in sorted(self._entries.values(), key=lambda x: -x.mentions)],
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        logger.info("Glossary saved: %s (%d entries)", path, len(self._entries))
        return path

    def extract_from_paloc(self, entries: list[tuple[str, str]], min_mentions: int = 2) -> int:
        """Extract proper noun candidates from paloc entries.

        Finds short entries (1-2 words) that appear frequently in longer
        descriptions. Auto-categorizes known names from wiki data.

        Only adds NEW terms — does not overwrite existing glossary entries.

        Uses a single-pass concatenated text search for O(n) performance
        instead of O(n*m) nested loops.

        Args:
            entries: list of (key, value) from paloc parser.
            min_mentions: minimum mentions in descriptions to qualify.

        Returns:
            Number of new terms added.
        """
        short_texts = {}
        for key, value in entries:
            text = value.strip()
            if not text or len(text.split()) > 3:
                continue
            if not text[0].isupper():
                continue
            if text.endswith(".") or text.endswith("?") or text.endswith("!"):
                continue
            if text.startswith("{") or text.startswith("<") or text.startswith("UI_"):
                continue
            if text.isdigit() or len(text) < 2:
                continue
            if text.startswith("%") or text.startswith("+"):
                continue
            short_texts[text] = short_texts.get(text, 0) + 1

        # Build ONE big string of all long descriptions for fast substring counting
        long_texts = [value for _, value in entries if len(value.split()) > 5]
        combined = "\x00".join(long_texts)

        added = 0
        for term, count in short_texts.items():
            if term in self._entries:
                continue

            mentions = combined.count(term)
            if mentions < min_mentions:
                continue

            category = self._auto_categorize(term)
            self._entries[term] = GlossaryEntry(
                term=term,
                category=category,
                mentions=mentions,
                entry_count=count,
            )
            added += 1

        logger.info("Extracted %d new glossary candidates from %d paloc entries", added, len(entries))
        return added

    def _auto_categorize(self, term: str) -> GlossaryCategory:
        """Auto-categorize a term based on wiki data (440+ known entries) and patterns."""
        if term in KNOWN_CHARACTERS:
            return GlossaryCategory.CHARACTER
        if term in KNOWN_LOCATIONS:
            return GlossaryCategory.LOCATION
        if term in KNOWN_FACTIONS:
            return GlossaryCategory.FACTION
        if term in KNOWN_BOSSES:
            return GlossaryCategory.CREATURE
        if term in KNOWN_MOUNTS:
            return GlossaryCategory.CREATURE
        if term in KNOWN_SKILLS:
            return GlossaryCategory.SKILL

        lower = term.lower()
        if any(w in lower for w in ["sword", "helm", "armor", "gloves", "boots", "shield", "bow", "staff", "ring", "necklace", "earring", "cloak", "dagger", "musket", "pistol", "shotgun", "hammer", "mace", "rapier", "axe"]):
            return GlossaryCategory.ITEM
        if any(w in lower for w in ["wolf", "bear", "goblin", "orc", "dragon", "spider", "boar", "crab", "devil", "nightmare", "walker", "titan"]):
            return GlossaryCategory.CREATURE
        if any(w in lower for w in ["fort", "castle", "village", "cave", "ruins", "camp", "mine", "bridge", "tower", "gate", "harbor", "lake", "river", "mountain", "valley", "forest", "desert", "island", "monastery", "manor", "keep", "smithy", "inn", "shop", "church"]):
            return GlossaryCategory.LOCATION
        if any(w in lower for w in ["slash", "strike", "dodge", "block", "parry", "combo", "smite", "rush", "kick", "shot", "leap", "slam", "bash", "surge", "pulse", "howl", "quake", "flurry"]):
            return GlossaryCategory.SKILL
        if any(w in lower for w in ["leather", "iron", "copper", "silver", "gold", "wood", "stone", "ore", "cloth", "herb", "meat", "hide", "bone", "timber", "wheat", "barley", "mushroom", "berry", "honey", "milk", "egg", "salt", "sugar", "gunpowder"]):
            return GlossaryCategory.MATERIAL
        if any(w in lower for w in ["guild", "clan", "empire", "bandits", "company", "house", "order"]):
            return GlossaryCategory.FACTION
        return GlossaryCategory.OTHER

    def get_entry(self, term: str) -> Optional[GlossaryEntry]:
        return self._entries.get(term)

    def set_translation(self, term: str, translation: str) -> None:
        if term in self._entries:
            self._entries[term].translation = translation

    def set_category(self, term: str, category: GlossaryCategory) -> None:
        if term in self._entries:
            self._entries[term].category = category

    def lock_entry(self, term: str) -> None:
        if term in self._entries:
            self._entries[term].locked = True

    def unlock_entry(self, term: str) -> None:
        if term in self._entries:
            self._entries[term].locked = False

    def remove_entry(self, term: str) -> None:
        self._entries.pop(term, None)

    @property
    def entries(self) -> list[GlossaryEntry]:
        return sorted(self._entries.values(), key=lambda x: -x.mentions)

    @property
    def translated_entries(self) -> list[GlossaryEntry]:
        return [e for e in self._entries.values() if e.translation]

    @property
    def untranslated_entries(self) -> list[GlossaryEntry]:
        return [e for e in self._entries.values() if not e.translation and e.category != GlossaryCategory.SKIP]

    @property
    def entry_count(self) -> int:
        return len(self._entries)

    @property
    def translated_count(self) -> int:
        return sum(1 for e in self._entries.values() if e.translation)

    def build_prompt_glossary(self, max_entries: int = 200) -> str:
        """Build a glossary string for injection into AI prompts.

        Returns a formatted glossary of translated terms, sorted by
        mention frequency (most important first).

        Args:
            max_entries: Maximum entries to include in prompt.

        Returns:
            Formatted glossary string for AI system prompt.
        """
        translated = sorted(
            [e for e in self._entries.values() if e.translation and e.category != GlossaryCategory.SKIP],
            key=lambda x: -x.mentions,
        )[:max_entries]

        if not translated:
            return ""

        lines = ["GLOSSARY - Use these EXACT translations for proper nouns:"]
        for entry in translated:
            cat = CATEGORY_LABELS.get(entry.category, "")
            lines.append(f"  {entry.term} = {entry.translation} [{cat}]")
        return "\n".join(lines)

    def lookup(self, text: str) -> dict[str, str]:
        """Find all glossary terms that appear in the given text.

        Returns dict of {term: translation} for terms found in text.
        """
        found = {}
        for term, entry in self._entries.items():
            if entry.translation and term in text:
                found[term] = entry.translation
        return found

    def export_json(self, path: str) -> None:
        """Export glossary to a standalone JSON file."""
        data = {
            "version": "1.0.0",
            "source_lang": self._source_lang,
            "target_lang": self._target_lang,
            "entries": [e.to_dict() for e in self.entries],
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def import_json(self, path: str) -> int:
        """Import glossary from JSON, merging with existing entries.

        Returns number of entries added/updated.
        """
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        count = 0
        for entry_data in data.get("entries", []):
            ge = GlossaryEntry.from_dict(entry_data)
            if ge.term not in self._entries or ge.translation:
                self._entries[ge.term] = ge
                count += 1
        return count
