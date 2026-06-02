"""Localization usage categorization for Translate tab filtering.

Builds exact per-key usage tags from original game data files and
known symbolic key families. The goal is to classify loaded paloc
entries by where the game uses them, not by guessing from English text.
"""

from __future__ import annotations

import re
import struct
from collections import defaultdict
from pathlib import Path

from core.vfs_manager import VfsManager


CATEGORY_DIALOGUE = "Dialogue / Subtitle"
CATEGORY_QUEST_GREETING = "Quest Greeting"
CATEGORY_QUEST_MAIN = "Quest Main Dialogue"
CATEGORY_QUEST_CONTENT = "Quest Side Content"
CATEGORY_QUEST_LINES = "Quest Lines"
CATEGORY_AI_FRIENDLY = "AI Friendly"
CATEGORY_AI_AMBIENT = "AI Ambient"
CATEGORY_AI_AMBIENT_GROUP = "AI Ambient (Group)"
CATEGORY_QUESTS = "Quests"
CATEGORY_SKILLS = "Skills"
CATEGORY_KNOWLEDGE = "Knowledge / Codex"
CATEGORY_ITEMS = "Items"
CATEGORY_FACTIONS = "Factions"
CATEGORY_MOUNTS = "Mount / Vehicle"
CATEGORY_DOCUMENTS = "Documents / Books"
CATEGORY_UNCATEGORIZED = "Uncategorized"

CATEGORY_ORDER = [
    CATEGORY_DIALOGUE,
    CATEGORY_QUEST_GREETING,
    CATEGORY_QUEST_MAIN,
    CATEGORY_QUEST_CONTENT,
    CATEGORY_QUEST_LINES,
    CATEGORY_AI_FRIENDLY,
    CATEGORY_AI_AMBIENT,
    CATEGORY_AI_AMBIENT_GROUP,
    CATEGORY_QUESTS,
    CATEGORY_SKILLS,
    CATEGORY_KNOWLEDGE,
    CATEGORY_ITEMS,
    CATEGORY_FACTIONS,
    CATEGORY_MOUNTS,
    CATEGORY_DOCUMENTS,
    CATEGORY_UNCATEGORIZED,
]

# Maps pabgb filename stem (without extension) → category.
# Files not listed here are skipped during the generic scan.
# knowledgeinfo / knowledgegroupinfo are handled by dedicated parsers.
_PABGB_CATEGORY_MAP: dict[str, str] = {
    # ── Quests ──
    "questinfo": CATEGORY_QUESTS,
    "questgroupinfo": CATEGORY_QUESTS,
    "questgaugeinfo": CATEGORY_QUESTS,
    "missioninfo": CATEGORY_QUESTS,
    "wantedinfo": CATEGORY_QUESTS,
    # ── Skills ──
    "skill": CATEGORY_SKILLS,
    "skillgroupinfo": CATEGORY_SKILLS,
    "skilltreeinfo": CATEGORY_SKILLS,
    "skilltreegroupinfo": CATEGORY_SKILLS,
    "buffinfo": CATEGORY_SKILLS,
    "statusinfo": CATEGORY_SKILLS,
    "statusgroupinfo": CATEGORY_SKILLS,
    "conditioninfo": CATEGORY_SKILLS,
    "jobinfo": CATEGORY_SKILLS,
    # ── Items ──
    "iteminfo": CATEGORY_ITEMS,
    "itemgroupinfo": CATEGORY_ITEMS,
    "itemuseinfo": CATEGORY_ITEMS,
    "storeinfo": CATEGORY_ITEMS,
    "crafttoolinfo": CATEGORY_ITEMS,
    "crafttoolgroupinfo": CATEGORY_ITEMS,
    "socketinfo": CATEGORY_ITEMS,
    "socketgroupinfo": CATEGORY_ITEMS,
    "dropsetinfo": CATEGORY_ITEMS,
    "inventory": CATEGORY_ITEMS,
    "equipslotinfo": CATEGORY_ITEMS,
    "equiptypeinfo": CATEGORY_ITEMS,
    "dyecolorgroupinfo": CATEGORY_ITEMS,
    "elementalmaterialinfo": CATEGORY_ITEMS,
    "royalsupply": CATEGORY_ITEMS,
    # ── Factions ──
    "faction": CATEGORY_FACTIONS,
    "factiongroup": CATEGORY_FACTIONS,
    "factionnode": CATEGORY_FACTIONS,
    "allygroupinfo": CATEGORY_FACTIONS,
    "tribeinfo": CATEGORY_FACTIONS,
    # ── Mount / Vehicle ──
    "vehicleinfo": CATEGORY_MOUNTS,
    # ── Knowledge / Codex (generic map/region data) ──
    "regioninfo": CATEGORY_KNOWLEDGE,
    "uimaptextureinfo": CATEGORY_KNOWLEDGE,
    "sublevelinfo": CATEGORY_KNOWLEDGE,
}


class LocalizationUsageIndex:
    """Categorize localization keys using authoritative game sources."""

    _DIGIT_RE = re.compile(rb"\d{6,}")
    _KNOWLEDGE_NAME_RE = re.compile(rb"Knowledge_[A-Za-z0-9_]+\x00")
    _GROUP_MARKER = b"\x01\x01\x00\x73\xe1\xc5\xea"

    def __init__(self, vfs: VfsManager):
        self._vfs = vfs
        self._entry_cache: dict[tuple[str, str], object] = {}
        self._data_cache: dict[str, bytes] = {}

    def build(self, available_keys: set[str]) -> dict[str, list[str]]:
        """Return key -> sorted usage tags for the currently loaded paloc keys."""
        from utils.logger import get_logger
        _log = get_logger("localization_usage_index")

        tags: dict[str, set[str]] = defaultdict(set)
        numeric_keys = {key for key in available_keys if key.isdigit()}
        symbolic_keys = available_keys - numeric_keys

        _log.info(
            "Usage index: %d total keys (%d numeric, %d symbolic)",
            len(available_keys), len(numeric_keys), len(symbolic_keys),
        )

        self._tag_symbolic_keys(symbolic_keys, tags)
        self._tag_numeric_game_data(numeric_keys, tags)

        # Tally results by category for diagnostics.
        category_counts: dict[str, int] = {}
        uncategorized = 0
        result = {}
        for key in available_keys:
            key_tags = tags.get(key)
            if not key_tags:
                result[key] = [CATEGORY_UNCATEGORIZED]
                uncategorized += 1
                continue
            result[key] = sorted(key_tags, key=self._category_sort_key)
            for t in key_tags:
                category_counts[t] = category_counts.get(t, 0) + 1

        categorized = len(available_keys) - uncategorized
        _log.info(
            "Usage tagging done: %d/%d keys categorized. Breakdown: %s. Uncategorized: %d",
            categorized, len(available_keys),
            {k: v for k, v in sorted(category_counts.items(), key=lambda x: -x[1])},
            uncategorized,
        )
        return result

    def category_counts(self, entries) -> dict[str, int]:
        counts = {category: 0 for category in CATEGORY_ORDER}
        for entry in entries:
            tags = entry.usage_tags or [CATEGORY_UNCATEGORIZED]
            for tag in tags:
                counts[tag] = counts.get(tag, 0) + 1
        return counts

    def _category_sort_key(self, category: str) -> tuple[int, str]:
        try:
            return (CATEGORY_ORDER.index(category), category)
        except ValueError:
            return (len(CATEGORY_ORDER), category)

    # ── Symbolic key tagging ─────────────────────────────────────────

    def _tag_symbolic_keys(self, symbolic_keys: set[str], tags: dict[str, set[str]]) -> None:
        for key in symbolic_keys:
            lowered = key.lower()

            # ── Quest Dialogue (voice-acted, linked to .wem audio) ──
            if key.startswith("questdialog_"):
                tags[key].add(CATEGORY_DIALOGUE)
                # Sub-categorize by dialogue type
                if key.startswith("questdialog_hello_"):
                    tags[key].add(CATEGORY_QUEST_GREETING)
                elif key.startswith("questdialog_main_"):
                    tags[key].add(CATEGORY_QUEST_MAIN)
                elif key.startswith("questdialog_contents_"):
                    tags[key].add(CATEGORY_QUEST_CONTENT)
                elif key.startswith("questdialog_quest_"):
                    tags[key].add(CATEGORY_QUEST_LINES)
                elif key.startswith(("questdialog_day2_", "questdialog_pywel_")):
                    tags[key].add(CATEGORY_QUEST_LINES)
                else:
                    tags[key].add(CATEGORY_QUEST_LINES)

            # ── AI Ambient Dialogue (single NPC, voice-acted) ──
            elif key.startswith("aidialogstringinfogroup_"):
                tags[key].add(CATEGORY_DIALOGUE)
                tags[key].add(CATEGORY_AI_AMBIENT_GROUP)

            elif key.startswith("aidialogstringinfo_"):
                tags[key].add(CATEGORY_DIALOGUE)
                if "_friendly_" in lowered:
                    tags[key].add(CATEGORY_AI_FRIENDLY)
                else:
                    tags[key].add(CATEGORY_AI_AMBIENT)

            # ── Other dialogue keys ──
            elif key.startswith(("quest_node_", "onetimequest_")):
                tags[key].add(CATEGORY_DIALOGUE)

            if key.startswith("textdialog_"):
                tags[key].add(CATEGORY_DOCUMENTS)

            if lowered.startswith("epilogue_") and (
                "_subtitlegroup_" in lowered
                or "_player_" in lowered
                or "_npc_" in lowered
                or "_globalgametrack_" in lowered
            ):
                tags[key].add(CATEGORY_DIALOGUE)

            if key.startswith("quest_") and not key.startswith((
                "questdialog_",
                "quest_node_",
            )):
                tags[key].add(CATEGORY_QUESTS)

    # ── Numeric key tagging (auto-discover all .pabgb files) ─────────

    def _tag_numeric_game_data(self, numeric_keys: set[str], tags: dict[str, set[str]]) -> None:
        if not numeric_keys:
            return

        from utils.logger import get_logger
        _log = get_logger("localization_usage_index")

        # Auto-discover all .pabgb files from every cached PAMT whose
        # folder_prefix contains "gamedata".
        file_hits: dict[str, int] = {}
        for _group, pamt in list(self._vfs._pamt_cache.items()):
            for entry in pamt.file_entries:
                path_lower = entry.path.replace("\\", "/").lower()
                if not path_lower.endswith(".pabgb"):
                    continue
                stem = path_lower.split("/")[-1][:-6]  # strip .pabgb

                # Skip knowledge — handled by dedicated parsers below.
                if stem in ("knowledgeinfo", "knowledgegroupinfo"):
                    continue

                category = _PABGB_CATEGORY_MAP.get(stem)
                if category is None:
                    continue

                data = self._read_entry_data(entry)
                if not data:
                    continue

                matched = self._matching_numeric_keys(data, numeric_keys)
                if matched:
                    file_hits[stem] = len(matched)
                for key in matched:
                    tags[key].add(category)

        if file_hits:
            _log.info("Gamedata file hit counts: %s", file_hits)

        # Special structured parsing for knowledge entries.
        self._tag_knowledgeinfo(numeric_keys, tags)
        self._tag_knowledgegroupinfo(numeric_keys, tags)

    # ── Key matching (ASCII + UTF-16 LE + binary uint32) ─────────────

    def _matching_numeric_keys(self, data: bytes, numeric_keys: set[str]) -> set[str]:
        matches = set()
        for raw in self._DIGIT_RE.findall(data):
            key = raw.decode("ascii")
            if key in numeric_keys:
                matches.add(key)
        return matches

    # ── Knowledge special parsers ────────────────────────────────────

    def _tag_knowledgeinfo(self, numeric_keys: set[str], tags: dict[str, set[str]]) -> None:
        data = self._read_data("0008", "gamedata/knowledgeinfo.pabgb")
        if not data:
            return

        records = []
        for match in self._KNOWLEDGE_NAME_RE.finditer(data):
            start = match.start() - 8
            if start < 0:
                continue
            name = match.group()[:-1].decode("ascii")
            name_len = struct.unpack_from("<I", data, start + 4)[0]
            if name_len != len(name):
                continue
            records.append((start, name))

        seen = set()
        unique_records = []
        for start, name in sorted(records):
            if start in seen:
                continue
            seen.add(start)
            unique_records.append((start, name))

        for i, (start, _name) in enumerate(unique_records):
            end = unique_records[i + 1][0] if i + 1 < len(unique_records) else len(data)
            blob = data[start:end]
            marker_pos = blob.find(self._GROUP_MARKER)
            if marker_pos == -1 or marker_pos + 23 > len(blob):
                continue
            group_id = int.from_bytes(blob[marker_pos + 15:marker_pos + 19], "little")
            loc_keys = self._matching_numeric_keys(blob, numeric_keys)
            if not loc_keys:
                continue

            record_tags = self._knowledge_group_tags(group_id)
            for key in loc_keys:
                tags[key].update(record_tags)

    def _tag_knowledgegroupinfo(self, numeric_keys: set[str], tags: dict[str, set[str]]) -> None:
        data = self._read_data("0008", "gamedata/knowledgegroupinfo.pabgb")
        if not data:
            return

        for key in self._matching_numeric_keys(data, numeric_keys):
            tags[key].add(CATEGORY_KNOWLEDGE)

        special_groups = {
            "KnowledgeGroup_Skill": {CATEGORY_SKILLS},
            "KnowledgeGroup_Skill_Temp": {CATEGORY_SKILLS},
            "KnowledgeGroup_Skill_Temp2": {CATEGORY_SKILLS},
            "KnowledgeGroup_Skill_Vehicle_Temp": {CATEGORY_SKILLS, CATEGORY_MOUNTS},
            "KnowledgeGroup_Skill_Faction_Temp": {CATEGORY_SKILLS, CATEGORY_FACTIONS},
            "KnowledgeGroup_Skill_LearnSpot": {CATEGORY_SKILLS},
        }
        for group_name, record_tags in special_groups.items():
            record = self._extract_named_record(data, group_name.encode("ascii"))
            if not record:
                continue
            for key in self._matching_numeric_keys(record, numeric_keys):
                tags[key].update(record_tags)

    def _knowledge_group_tags(self, group_id: int) -> set[str]:
        if group_id in {1, 111, 112, 168}:
            return {CATEGORY_SKILLS}
        if group_id == 113:
            return {CATEGORY_SKILLS, CATEGORY_MOUNTS}
        if group_id == 121:
            return {CATEGORY_SKILLS, CATEGORY_FACTIONS}
        if group_id == 1461:
            return {CATEGORY_SKILLS, CATEGORY_ITEMS}
        return {CATEGORY_KNOWLEDGE}

    def _extract_named_record(self, data: bytes, name: bytes) -> bytes:
        needle = name + b"\x00"
        index = data.find(needle)
        if index == -1:
            return b""
        start = max(0, index - 8)
        next_index = data.find(b"KnowledgeGroup_", index + 1)
        end = next_index - 8 if next_index != -1 else len(data)
        return data[start:end]

    # ── Data reading helpers ─────────────────────────────────────────

    def _read_entry_data(self, entry) -> bytes:
        """Read (+ decrypt/decompress) a PamtFileEntry, with path-keyed cache."""
        cache_key = entry.path
        if cache_key in self._data_cache:
            return self._data_cache[cache_key]
        try:
            data = self._vfs.read_entry_data(entry)
        except Exception:
            data = b""
        self._data_cache[cache_key] = data
        return data

    def _read_data(self, group: str, path: str) -> bytes:
        """Read data by group hint + path (used by knowledge parsers)."""
        entry = self._get_entry(group, path)
        if entry is None:
            return b""
        return self._read_entry_data(entry)

    def _get_entry(self, group: str, path: str):
        cache_key = (group, path)
        if cache_key in self._entry_cache:
            return self._entry_cache[cache_key]

        from utils.logger import get_logger
        _log = get_logger("localization_usage_index")

        path_norm = path.replace("\\", "/").lower()

        # Build search order: specified group first, then already-cached groups
        # (free, no I/O), then any remaining groups from disk.
        search_order: list[str] = [group]
        for g in self._vfs._pamt_cache:
            if g != group:
                search_order.append(g)
        try:
            for g in self._vfs.list_package_groups():
                if g not in search_order:
                    search_order.append(g)
        except Exception:
            pass

        for g in search_order:
            try:
                pamt = self._vfs.get_pamt(g)
                if pamt is None:
                    pamt = self._vfs.load_pamt(g)
            except Exception as exc:
                _log.debug("Cannot load PAMT for group %s: %s", g, exc)
                continue

            for entry in pamt.file_entries:
                entry_norm = entry.path.replace("\\", "/").lower()
                if entry_norm == path_norm or entry_norm.endswith("/" + path_norm):
                    if g != group:
                        _log.info(
                            "Found %s in group %s (hint was %s)", path, g, group
                        )
                    self._entry_cache[cache_key] = entry
                    return entry

        _log.debug("Entry not found in any group: %s", path)
        self._entry_cache[cache_key] = None
        return None
