"""Item-name search index for Explorer.

Builds a searchable mapping from in-game item display names to model basenames by
parsing localization data plus ``iteminfo.pabgb``. The resulting aliases let the
Explorer search for item names such as ``Vow of the Dead King`` and resolve the
matching PAC/prefab/model files immediately.
"""

from __future__ import annotations

import os
import re
import struct
from dataclasses import dataclass, field

from core.crypto_engine import hashlittle
from core.vfs_manager import VfsManager


@dataclass
class ItemRecord:
    item_id: int
    internal_name: str
    display_name: str = ""
    description: str = ""
    prefab_hashes: list[int] = field(default_factory=list)
    pac_files: list[str] = field(default_factory=list)


@dataclass
class ItemIndex:
    items: list[ItemRecord]
    pac_to_items: dict[str, list[ItemRecord]]
    # Joined search corpus per model base — display + internal + path tokens.
    # Used for general substring / prefix matching.
    model_base_aliases: dict[str, str]
    # Subset of the above that holds ONLY the user-facing display names per
    # base. Searches that can be satisfied by display-name tokens alone are
    # restricted to this map so e.g. ``canta plate armor`` returns just the
    # chest piece (display 'Canta Plate Armor') without dragging in the
    # cloak / hand / foot variants whose internal name shares the
    # 'PlateArmor' set token but whose display name is different.
    model_display_aliases: dict[str, str] = field(default_factory=dict)


def _find_entry(vfs: VfsManager, group: str, needle: str):
    pamt = vfs.load_pamt(group)
    needle = needle.lower()
    for entry in pamt.file_entries:
        if needle in entry.path.lower():
            return entry
    return None


def parse_localization(vfs: VfsManager, progress_fn=None) -> dict[str, str]:
    """Parse the active localization strings into ``loc_id -> text``.

    Reads ``localizationstring_eng.paloc`` — the active-language slot
    Crimson Desert populates from Steam's selected language. The
    *filename* is always ``_eng`` regardless of the chosen language;
    the *content* is whatever the user sees in-game (English,
    Korean, Arabic, …). Strict 1+1: we accept that content verbatim
    so the search index matches exactly the names the user reads on
    screen. There is no language fallback, no transliteration, no
    silent locale switching — the in-game text IS the source of
    truth.
    """
    if progress_fn:
        progress_fn("Loading active localization...")

    loc_entry = _find_entry(vfs, "0020", "localizationstring_eng")
    if not loc_entry:
        return {}

    data = vfs.read_entry_data(loc_entry)
    loc_dict: dict[str, str] = {}
    pos = 0

    while pos + 8 < len(data):
        slen = struct.unpack_from("<I", data, pos)[0]
        if slen == 0 or slen > 50000 or pos + 4 + slen > len(data):
            pos += 1
            continue

        s_bytes = data[pos + 4:pos + 4 + slen]
        if 6 <= slen <= 20 and all(0x30 <= b <= 0x39 for b in s_bytes):
            loc_id = s_bytes.decode("ascii")
            text_pos = pos + 4 + slen
            if text_pos + 4 < len(data):
                text_len = struct.unpack_from("<I", data, text_pos)[0]
                if 0 < text_len < 50000 and text_pos + 4 + text_len <= len(data):
                    text = data[text_pos + 4:text_pos + 4 + text_len].decode(
                        "utf-8", errors="replace"
                    )
                    loc_dict[loc_id] = text
                    pos = text_pos + 4 + text_len
                    continue

        pos += 1

    return loc_dict


def parse_iteminfo(vfs: VfsManager, loc_dict: dict[str, str], progress_fn=None) -> list[ItemRecord]:
    """Parse ``iteminfo.pabgb`` and resolve display names."""
    if progress_fn:
        progress_fn("Loading item database...")

    item_entry = _find_entry(vfs, "0008", "iteminfo.pabgb")
    if not item_entry:
        return []

    data = vfs.read_entry_data(item_entry)

    full_marker = b"\x00\x01\x00\x00\x00\x00\x00\x00\x00\x07\x70\x00\x00\x00"
    items = []
    seen_ids = set()
    idx = 0

    while True:
        pos = data.find(full_marker, idx)
        if pos == -1:
            break
        idx = pos + len(full_marker)
        null_pos = pos

        name_start = null_pos
        while name_start > 0 and 0x21 <= data[name_start - 1] <= 0x7E:
            name_start -= 1
            if null_pos - name_start > 150:
                break

        if null_pos - name_start < 3 or name_start < 8:
            continue

        name = data[name_start:null_pos].decode("ascii", errors="replace")
        if not re.match(r"^[A-Za-z][A-Za-z0-9_]*$", name):
            continue

        name_len = struct.unpack_from("<I", data, name_start - 4)[0]
        item_id = struct.unpack_from("<I", data, name_start - 8)[0]
        if name_len not in (len(name), len(name) + 1):
            continue
        if item_id < 100 or item_id > 100000000 or item_id in seen_ids:
            continue
        seen_ids.add(item_id)

        loc_off = pos + 14 + 4
        loc_id = ""
        if loc_off + 4 < len(data):
            loc_len = struct.unpack_from("<I", data, loc_off)[0]
            if 5 < loc_len < 25 and loc_off + 4 + loc_len <= len(data):
                loc_bytes = data[loc_off + 4:loc_off + 4 + loc_len]
                if all(0x30 <= b <= 0x39 for b in loc_bytes):
                    loc_id = loc_bytes.decode("ascii")

        prefab_hashes: list[int] = []
        search_end = min(len(data), pos + 800)
        # The May 3 2026 game patch changed the iteminfo prefab-block
        # marker from 0x0E to 0x0F. Old delimiter kept as a fallback for
        # users on a pre-patch game install.
        for scan in range(pos + 14, search_end - 15):
            if data[scan] != 0x0F and data[scan] != 0x0E:
                continue
            count1 = struct.unpack_from("<I", data, scan + 3)[0]
            count2 = struct.unpack_from("<I", data, scan + 7)[0]
            if not (0 < count1 <= 5 and 0 < count2 <= 5):
                continue
            for h_idx in range(count2):
                value = struct.unpack_from("<I", data, scan + 11 + h_idx * 4)[0]
                if value != 0:
                    prefab_hashes.append(value)
            if prefab_hashes:
                break

        items.append(
            ItemRecord(
                item_id=item_id,
                internal_name=name,
                display_name=loc_dict.get(loc_id, "") if loc_id else "",
                prefab_hashes=prefab_hashes,
            )
        )

    return items


def build_hash_table(file_entries: list) -> dict[int, str]:
    """Build hash -> model basename lookup from PAC/prefab filenames."""
    hash_to_name = {}
    for entry in file_entries:
        lower = entry.path.lower()
        if not (lower.endswith(".prefab") or lower.endswith(".pac") or lower.endswith(".pact")):
            continue
        base = os.path.splitext(os.path.basename(lower))[0]
        for suffix in ("", "_l", "_r", "_u", "_s", "_t", "_index01", "_index02", "_index03"):
            name = base + suffix
            hash_to_name[hashlittle(name.encode("ascii"), 0xC5EDE)] = name
    return hash_to_name


def build_item_index(vfs: VfsManager, progress_fn=None) -> ItemIndex:
    """Build the full item-name -> model search index from live game data."""
    loc_dict = parse_localization(vfs, progress_fn)
    if progress_fn:
        progress_fn(f"Localization: {len(loc_dict):,} strings")

    items = parse_iteminfo(vfs, loc_dict, progress_fn)
    if progress_fn:
        progress_fn(f"Items: {len(items):,} records")

    pamt_0009 = vfs.load_pamt("0009")
    hash_table = build_hash_table(pamt_0009.file_entries)
    if progress_fn:
        progress_fn(f"Hash table: {len(hash_table):,} entries")

    pac_to_items: dict[str, list[ItemRecord]] = {}
    model_base_aliases: dict[str, str] = {}
    model_display_aliases: dict[str, str] = {}
    items_with_models: list[ItemRecord] = []

    # Set of real .pac stems (basename minus extension) so we can verify
    # that a synthesized name actually exists. Without this check, an
    # iteminfo prefab hash that resolves to a ``.prefab`` descriptor whose
    # bare stem doesn't have a matching ``.pac`` (e.g.
    # ``cd_r0002_00_horse_ub_0019_index01_n.prefab`` → no
    # ``cd_r0002_00_horse_ub_0019_index01_n.pac`` exists, only
    # ``cd_r0002_00_horse_ub_0019.pac`` and the ``_sub01`` variant) gets
    # written into the catalog as a fabricated, unsearchable name.
    real_pac_stems: set[str] = set()
    real_pac_paths: dict[str, str] = {}  # stem -> full path (first-seen wins)
    for entry in pamt_0009.file_entries:
        ep = entry.path.replace("\\", "/").lower()
        if ep.endswith(".pac"):
            stem = os.path.splitext(os.path.basename(ep))[0]
            real_pac_stems.add(stem)
            real_pac_paths.setdefault(stem, ep)

    # Suffix list ordered LONGEST-FIRST. Compound suffixes like
    # ``_index01_n`` must strip before the bare ``_n`` / ``_index01``
    # so we land on the correct base stem. The ``_n`` / ``_index??_n``
    # forms are normal-map / "n-version" prefab variants — they
    # describe the same logical mesh as the bare base.
    SUFFIX_STRIP = (
        "_index01_n", "_index02_n", "_index03_n",
        "_index01", "_index02", "_index03",
        "_l", "_r", "_u", "_s", "_t", "_n",
    )

    def _resolve_pac_stems(name: str) -> list[str]:
        """Return real .pac stems matching ``name`` after suffix-strip.

        Tries the bare base plus ``_sub01`` / ``_sub02`` variants.
        Returns only stems that actually exist in the PAMT.
        """
        b = name
        for sfx in SUFFIX_STRIP:
            if b.endswith(sfx):
                b = b[:-len(sfx)]
                break
        # Try bare, then _sub01 / _sub02 variants.
        candidates = [b, b + "_sub01", b + "_sub02"]
        return [c for c in candidates if c in real_pac_stems]

    for item in items:
        for prefab_hash in item.prefab_hashes:
            resolved = hash_table.get(prefab_hash)
            if not resolved:
                continue

            stems = _resolve_pac_stems(resolved)
            if not stems:
                # No real PAC for this prefab — skip silently rather
                # than fabricate a synthesised name. Items in this
                # situation will still appear in the catalog with their
                # display name and item id; we just don't lie about
                # which mesh file they own.
                continue

            base = stems[0]  # first real stem (typically bare base)
            pac_name = base + ".pac"  # canonical name for alias terms below

            for stem in stems:
                stem_pac = stem + ".pac"
                if stem_pac not in item.pac_files:
                    item.pac_files.append(stem_pac)
                pac_to_items.setdefault(stem_pac, []).append(item)

            # Include the ORIGINAL CamelCase ``internal_name`` alongside its
            # lowercased form. The Explorer search bar uses a CamelCase-aware
            # tokenizer (``utils.text_search``) and needs the case boundary
            # preserved so that ``Canta_PlateArmor_Armor`` splits cleanly into
            # ``canta plate armor armor`` instead of being collapsed to the
            # single token ``canta_platearmor_armor`` once lowercased.
            terms = " ".join(
                token for token in (
                    item.display_name,
                    item.internal_name,
                    item.display_name.lower(),
                    item.internal_name.lower(),
                    base.lower(),
                    pac_name.lower(),
                ) if token
            )
            existing = model_base_aliases.get(base, "")
            merged = f"{existing} {terms}".strip() if existing else terms
            model_base_aliases[base] = merged

            # Display-only alias: lets the Explorer filter restrict matches
            # to rows whose user-facing item name actually contains every
            # query token. Without this, set names like ``Canta_PlateArmor``
            # that share the ``Plate Armor`` token chain across cloak / hand
            # / foot variants would all match a query of 'canta plate armor'
            # because the internal name's CamelCase split injects ``Armor``
            # into every variant. The display name is the cleaner signal of
            # which slot the user actually meant.
            if item.display_name:
                existing_disp = model_display_aliases.get(base, "")
                merged_disp = (
                    f"{existing_disp} {item.display_name}".strip()
                    if existing_disp else item.display_name
                )
                model_display_aliases[base] = merged_disp

        if item.display_name and item.pac_files:
            items_with_models.append(item)

    if progress_fn:
        progress_fn(f"Items with models: {len(items_with_models):,}")

    return ItemIndex(
        items=items_with_models,
        pac_to_items=pac_to_items,
        model_base_aliases=model_base_aliases,
        model_display_aliases=model_display_aliases,
    )
