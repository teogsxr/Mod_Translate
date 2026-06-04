"""CrimsonForge version and changelog registry.

Single source of truth for the application version. Every module that needs
the version string imports it from here. The CHANGELOG list is rendered in the
About tab so users (and developers) always see what changed.

VERSION BUMPING RULES
---------------------
- Bump PATCH (1.x.Y) for bug fixes, small tweaks, and safe improvements.
- Bump MINOR (1.X.0) for new features, new tabs, new AI providers, or
  significant workflow changes.
- Bump MAJOR (X.0.0) for breaking changes to project files, settings
  format, or game-patch pipeline.
- Always add a new entry at the TOP of CHANGELOG when changing code.
"""

__all__ = ["APP_VERSION", "APP_NAME", "CHANGELOG"]

APP_NAME = "CrimsonForge"
APP_VERSION = "1.26.0"

# Each entry: (version, date, list_of_changes)
# Newest first. `date` is YYYY-MM-DD.
CHANGELOG: list[tuple[str, str, list[str]]] = [
    (
        "1.26.0", "2026-05-12", [
            "[Mesh export] Strict skin write-back via v2 cfmeta sidecar. FBX → PAC round-trips now preserve the user's edited bone weights byte-exact: schema 2 adds a top-level `skeleton_bones` (PAB-index-ordered names) plus per-submesh `pab_to_slot` (PAB index → raw vertex-byte slot). The rebuilder consumes both; without them it refuses to invent a mapping — no silent donor-weight fallback. New public helper `build_skin_writeback_sidecar(original_data, vfs, pac_path)` in `core.mesh_importer` builds the sidecar from a donor PAC for plugin / CLI callers that don't go through the Explorer's FBX export flow.",
            "[Mesh export] Palette-table skin resolution replaces the K-NN / centroid heuristic. `derive_skin_slot_to_pab_geometric` in `core.mesh_parser` decodes the PAC's per-mesh palette table (longest valid-hash run @ 4-byte stride) and remaps `bone_indices` from raw-slot space to PAB-index space. Idempotency guard prevents double application. Logged accounting: assigned / slot-out-of-range / hash-not-in-PAB.",
            "[Skeleton] `bone_count` is read as uint16 LE @ 0x14 (was uint8). Fixes Damiane's spike-explosion — skeletons >255 bones were silently truncated, missing bones got identity-stub padding, and verts weighted to them collapsed to origin. phw_01.pab now parses 448 bones (was 192), phm_01.pab 434 (was 178).",
            "[Skeleton] Bone name length is read from byte[3] of the leading 4-byte hash field instead of 'scan until next printable-ASCII boundary'. The old heuristic drifted catastrophically after bone ~56 once a `parent_index` byte landed in the printable range. 226+/246 PAB files now parse 100%.",
            "[Skeleton] Per-bone SRT (`scale` / `rotation` quat / `position`) is now populated alongside the bind matrix — required by the FBX animation Lcl Rotation composer. `bone_hashes` list added to `Skeleton` (24-bit hashes in PAB index order, consumed by `derive_skin_slot_to_pab_geometric`).",
            "[Skeleton] New `core/skeleton_resolver.py` replaces the two duplicated resolvers (correct one in the Explorer + broken sibling-basename one added in v1.22.3 to the mesh export path). Both mesh and animation FBX paths now route through one implementation. Picker uses palette-table coverage — PAC bytes are passed in so candidates are scored by real palette signal, not path prefix. Fixes accessory PACs (foot/hand/cloak) where the donor's neighbourhood holds many unrelated rigs.",
            "[Character export] Right-click PAC → Export Complete Character. New strict pipeline (`core/character_complete_exporter.py`): clicked PAC is treated as the body PAC, every `character/*.app_xml` is scanned for a `<Nude><Prefab>` match. Hit → all PACs referenced by every `<Prefab>` are merged into ONE FBX skinned to the shared rig. Multi-hit shows an appearance picker; zero-hit refuses with a clear error naming the body-PAC stem.",
            "[Character export] New `core/character_appearance_resolver.py` decodes `.app_xml`, walks `<Nude>/<Head>/<Hair>/<Armor>` `<Prefab Name=\"X\">` children, reads each `character/X.prefab` binary, and extracts every PAC reference inside (verified 22/22 on damian). Strict path remap `\"character/\" + basename` (verified 22/22). Reverse lookup `find_app_xmls_for_body_pac` is cached per-VFS-instance.",
            "[Character export] New `core/character_animation_resolver.py` replaces substring `_discover_paa_candidates`. Token-based match against `actionchart/*.paa_metabin` (the engine's per-character action chart). Damian: 2,228 strict matches vs 2,323 substring matches — the 95-file delta is exactly the compound-word false positives (`damiandinner.paa` no longer surfaces for 'damian').",
            "[Texture] New `core/pac_xml_texture_resolver.py` decodes the `.pac_xml` companion deterministically. Strict join key `_subMeshName == lower(submesh.name)`. Maps `_baseColorTexture` / `_overlayColorTexture` / `_normalTexture` / `_materialTexture` / `_heightTexture` slots. Rules verified against 14,193 real Materials in 2,000 sample files. Strips the `texture/` segment when it's the second path component (verified 191/191). Engine sentinel `texture/nonetexture0x*.dds` treated as null — no silent fallback DDS pick.",
            "[Editors] New `.paseq` / `.paseqc` / `.pastage` editor (`ui/dialogs/paseq_editor_dialog.py`) backed by `core/paseq_parser.py`. Decodes the `[len:u32-LE][content:N bytes]` reflection-string layout (verified against `cd_seq_quest_marnidragon_boss_0010.paseq`'s 6 known strings). Field-kind tagging: `audio_event` (Wwise bgm/sfx/vce), `animation` (.paa/.paao), `mesh_path` (.pam/.pamlod/.prefab/.dds), `object_path` (`character/`, `leveldata/`, …), `unknown`. Fixed-length edits by default; 'Allow size changes' opt-in rewrites the length prefix and shifts subsequent bytes. The same parser also handles `.prefab` / `.pami` / `.pae` / `.binarygimmick` / `.binaryproperty`.",
            "[Editors] New `.pabgh` editor (`ui/dialogs/pabgh_editor_dialog.py`). Edits the `[count:u16][row_hash/id][offset:u32]…` row table (5-byte vs 8-byte row flavour auto-detected by file size). View + reorder + edit + add + remove. Save to sidecar or patch back to game.",
            "[Preview] Native DDS decode via `core.dds_reader.decode_dds_to_rgba` bypasses Qt's DDS loader (which fails on Pearl Abyss's BC variants). `validate_dds_payload_size` runs first so a truncated DDS surfaces as a clear error instead of crashing the preview. PAB preview decodes the skeleton and shows bone count + hierarchy summary. PAA preview decodes the animation header (variant, track count, frame range, link target if any).",
            "[Search] Display-name-only alias map added to `ItemIndex.model_display_aliases`. Searches that can be satisfied by display-name tokens alone are restricted to this map — `canta plate armor` returns the chest piece (display 'Canta Plate Armor') without dragging in the cloak / hand / foot variants that share the `PlateArmor` set-token but differ in display name.",
            "[Performance] C PaChecksum extension is now Stable-ABI / abi3. Single `_pa_checksum.cp311-abi3-win_amd64.pyd` loads on every CPython 3.11+ — Blender 4.2 (3.11), 5.x (3.13), future (3.14+) all get the C path. Previously the cp<N>-tag locked the .pyd to one Python and Blender silently fell back to pure-Python — the 247-second PAZ-checksum bottleneck on the in-place repack flow.",
            "[Blender addon] New `blender_addon/` ships a standalone Blender 4.2+ extension that reuses CrimsonForge's bundled `core/` for byte-exact parity. Operators: Browse Game (live VFS search → import PAC + sibling PAB + textures), Export to Game (in-place PAZ patch with backup), Import Complete Character (one click → entire character from .app_xml), Import Animation (PAA bake onto a CF-built armature). Armature builder sets `EditBone.matrix` from PAB bind so rest == bind — PAA quaternions apply directly as `pose_bone.rotation_quaternion` with no basis-change gymnastics.",
            "[Tests] New regression suites: `test_character_appearance_resolver`, `test_character_animation_resolver`, `test_pac_xml_texture_resolver`, `test_pac_skin_writeback`, updated `test_skeleton_resolver`. Full suite still green.",
        ],
    ),
    (
        "1.25.0", "2026-05-07", [
            "[Mesh import] OBJ → PAC import is now intent-aware. When the OBJ carries explicit submesh names that match the original PAC, the OBJ is treated as authoritative — submeshes the user removed get emitted as empty placeholders (zero verts / zero faces) so the descriptor count stays valid for the rebuilder while the game and preview render only the submeshes the user kept. Fully unnamed OBJs still take the legacy positional path. Verified end-to-end on helmet 0363 with a 2-of-7 OBJ.",
            "[Mesh import] PAC vertex layout fully reverse-engineered against the shipping shader DXIL. Tangents now MikkTSpace-recomputed per-vertex; normals written into the engine's actual bit layout (bits 10-29 + sign bit 30); bone-skinning decode reads 8-bit weights from bytes 28-35 with 6×10-bit indices in bytes 20-27 + the 4-bone-vs-6-bone gate from byte 39's low 6 bits — fixes white-with-sparks lighting and dropped-influence skinning on multi-bone meshes.",
            "[FBX export] Skin weights resolve via the per-mesh palette table embedded in section 0 of every PAC (longest run of valid PAB hashes at 4-byte stride). Replaces the v1.25.2-1.25.4 heuristic K-NN / centroid passes that cross-contaminated weights on layered armor.",
            "[FBX export] UVs no longer drop on character export. Per-bone visual length (`LimbNode.Size`) is now the world-space distance to the longest-reach child bone, so finger leaves draw small and legs draw big in Blender / Maya / Unreal — matches anatomy without breaking the round-trip back to PAC.",
            "[FBX export] 'Export Full Character FBX (Mesh + Bones + Animation)' menu unhidden in the Explorer — v1.25 makes the skin mapping reliable enough to ship.",
            "[Repack] Path-resolution fix: `find_file_entry` now picks the LONGEST matching path, not the first. Shipping PAMTs carry both shortcut aliases AND the real nested entry for the same basename; the previous resolver patched the shortcut and the game ignored it, making patches invisible. New `Preview Resolution` button on the Repack tab shows which canonical path each selected file will hit before any disk write.",
            "[Audio] Generate All + Patch no longer fails with 401 Unauthorized while the single Generate + Patch works with the same key. `TTSEngine.initialize_from_config` was checking `hasattr(config, 'get')` before `isinstance(config, dict)`, and dict's `.get` doesn't walk dotted paths — so batch mode (which receives `self._config.data`) initialised every provider with an empty key. Fixed for ElevenLabs and every other nested-key provider.",
            "[Audio] BNK preview failures (Crimson Desert ships some Wwise SoundBanks in an encrypted variant the bundled vgmstream can't decode) are now logged as WARNING with the BNK name + size instead of generic ERROR — the audio pipeline isn't broken, only those specific BNK files. Single-WEM previews keep working.",
            "[Performance] Cold-load against a 1.5M-entry game install drops from ~30s to ~11.5s. Skipped the never-consumed VFS trie inside `load_pamt`, deferred the item-search index to a background worker (no more 7.7s UI freeze), inlined the `_ArchiveRow` path-split via `rfind` + slice instead of `os.path.basename` (4.7× speedup on row construction), and cached the texture-service combined PAMT index so click latency drops from ~230ms to ~0.3ms after the first click.",
            "[Performance] Async mesh preview — heavy parse + GPU prep runs on a `FunctionWorker` so the UI never freezes on a click; tiny meshes still take a sync fast path. 'Loading mesh…' status appears immediately, full preview lands when the worker finishes.",
            "[Performance] Font Builder no longer re-parses all 34 PAMTs from disk on init — reuses the cached VfsManager (~10s saved on shipping installs).",
            "[UX] Splash screen visible from the first frame so the user never sees the OS shell shadow + blank white client area on cold launch / fast reopen. Loading screen now adds a 10-second post-load grace window with a per-second countdown so background indexes finish warming before the first click. Bottom-left status bar correctly flips to 'Game loaded: N package groups, M localization files' when loading completes.",
            "[Explorer] Ctrl+C copies the selected file's name (basename + extension) to the clipboard. Multi-select copies one filename per line. Scoped to the archive view so it doesn't shadow Ctrl+C in the preview pane / editor / search box.",
            "[Build] `PyOpenGL_accelerate>=3.1.10` added to `requirements.txt` (a wheel exists for Python 3.14 / Windows). The startup log line 'No OpenGL_accelerate module loaded' is gone after a fresh `pip install -r requirements.txt`.",
            "[Item Catalog] CSV export no longer crashes with `dict contains fields not in fieldnames: 'display_name', 'icon_paths'` — fieldnames are now derived from `asdict(items[0])` so future schema changes don't need a manual sync.",
            "[Tests] +12 regression tests covering the OBJ-deletion intent path, the path-resolution longest-match rule, and the TTS dict-config dotted-path lookup. Full suite: 1206 passed.",
        ],
    ),
    (
        "1.24.2", "2026-05-06", [
            "[Hotfix] Explorer search `ext:.dds canta` (and any other field-filter combined with extra terms) now works. The bare-extension shortcut was consuming the whole search string, so `ext:.dds canta` ended up filtering on the literal extension `.dds canta` which no row could match. Shortcut now fires only when the query is the bare extension with no further terms; richer queries route through the parsed evaluator that correctly applies the ext filter AND every other clause.",
            "[Hotfix] Explorer search keystroke latency on the 1.4 M-row file list is back under one frame for complex queries. The first cut of the enterprise parser tokenised every row's path on every keystroke, which made boolean / field / wildcard queries feel sluggish on a fully-loaded archive. The complex-query path now uses C-fast substring + fnmatch checks per clause and never tokenises the path corpus inside the per-row loop. Simple single-token queries still take the v1.24.0 Tier A / Tier B fast path.",
            "[Hotfix] Catalog Browser no longer points at non-existent .pac files for items whose iteminfo prefab hash resolves to an `_index01_n.prefab` / `_index02_n.prefab` / `_n.prefab` descriptor. Previously `core.item_index.build_item_index` and `core.item_catalog.parse_iteminfo_records` synthesised a `.pac` filename by stripping a fixed list of suffixes (`_l _r _u _s _t _index01 _index02 _index03`) from the resolved prefab name and appending `.pac`, with no check that the resulting file actually shipped — so the Demenissian Soldier's Cloth Barding catalog row showed `cd_r0002_00_horse_ub_0019_index01_n.pac` (a fabricated name) instead of the real `cd_r0002_00_horse_ub_0019.pac` + `cd_r0002_00_horse_ub_0019_sub01.pac` files the engine loads. Both modules now strip compound `_index??_n` and bare `_n` suffixes (longest-first), then verify each candidate stem against the live PAMT and only keep stems that exist as a real `.pac` entry. Bumps the catalog parser version so cached pickles rebuild silently on next launch.",
        ],
    ),
    (
        "1.24.1", "2026-05-06", [
            "[Fix] PAC reimport now writes replacement mesh normals during same-topology in-place rebuilds, preventing stale donor custom normals from causing dark, flipped, or sticker-like lighting patches on imported OBJ/FBX meshes.",
            "[Fix] PAC rebuild can clear donor shading records and packed-normal high flags when requested, so replacement meshes can keep their own normal data cleanly.",
            "[Feature] Explorer search now supports quoted phrases, OR/NOT clauses, wildcards, field filters such as `ext:`, `name:`, `path:`, `type:`, `size:`, and optional `content:` byte search while keeping the simple search path fast.",
        ],
    ),
    (
        "1.24.0", "2026-05-04", [
            "[Feature] Catalog Browser collapses the iteminfo / multichange leveling-variant clones. Pearl Abyss's tables describe each item once as a base record (`variant_level=None`) and once per upgrade level — a single Canta Plate Cloak ships in the catalog as a base plus +1 through +30, all sharing the same PAC, icon and shader and differing only in a `(+N)` suffix on the display name. Showing all 19,692 raw rows in the grid was pure noise; the dialog now folds them down to 4,320 unique items by keeping every record whose `variant_level is None` plus any rare orphan whose base doesn't ship as a record. Result: the canta search returns 4 distinct items (Canta Plate Armor, Canta Plate Cloak, Cantars Leather Armor, Eccanta Plate Armor) instead of 50, and `Rhett's Longsword` shows once instead of 30 times.",
            "[Feature] Catalog Browser thumbnail decoder uses an O(1) DDS-entry index built once at model construction. The previous implementation re-scanned every loaded PAMT for every cell that asked for an icon — 7 k icons × 1.4 M PAMT entries = an effective 10-billion-comparison ceiling that left grid cells stuck on the placeholder for many seconds while the GUI thread spun. Now `CatalogModel` builds a `dict[lowercased_path, PamtFileEntry]` covering every `.dds` entry across every group (~120 k entries, ~0.5 s one-time cost) and the worker resolves each icon with a single dict lookup. Inflight decodes are tracked per icon path so the same DDS is never queued twice. Two distinct placeholder pixmaps replace the single 'no icon' tile — a darker 'loading…' tile while a decode is in flight, the regular 'no icon' tile only when the catalog has no icon path for that item — so users can tell at a glance whether to wait or move on.",
            "[Feature] Catalog Browser inherits inventory icons across leveling variants by walking the catalog records twice during `build_item_catalog`: pass 1 resolves icons by direct PAC-stem match against `itemicon_prefab_<base>.dds`, pass 2 borrows the base item's icon list for any record without its own pac_files but with a `variant_base_name` pointing to a record that does. Bumps the catalog's icon coverage from ~17% (mesh-bearing items only) to ~80% (mesh-bearing items plus every leveled variant) — every Rhett's Longsword (+1) through (+30) now ships the same blade icon as the base item.",
            "[Feature] Single click on a catalog item live-scopes the Explorer file list to that item's files (PAC + paired DDS textures + sidecar XMLs + prefabs + mesh sidecars + inventory icon — same set the workbench-scope code path consumes). Double click does the same scope action AND closes the dialog as a commit-and-leave shortcut. The Explorer search box is cleared automatically before the scope is applied so a leftover search query no longer intersects the path filter and produces an empty file list — users can now click freely between items without having to manually delete the search bar's text.",
            "[Performance] Explorer search bar is back to v1.23.x speed for the 1.4 M-row file list. The two-tier search introduced in this version made every row tokenize its corpus on every keystroke, which was correctness-correct (no more 'Eccanta' false positives for a `canta` query) but cost-incorrect when applied uniformly to a 1.4 M-row hot loop. The filter now keeps Tier B as a plain `in` substring match (the same C-fast scan the pre-1.24.0 filter used) and limits Tier A's token matching to rows that actually carry a display alias — typically well under 1% of the corpus. Display tokens are lazily cached on the row so subsequent keystrokes reuse the work. Net effect: the keystroke-to-result latency on a fully-loaded archive is back under one frame, while `canta plate armor` still resolves to just Canta Plate Armor (Tier A still suppresses Tier B when display tokens match).",
            "[Performance] Catalog Browser opens instantly thanks to a background pre-build of the catalog on Explorer game-data activation. The Explorer tab now kicks off `build_item_catalog_cached` in a `FunctionWorker` thread immediately after the game's PAMTs finish loading, holds the resulting `ItemCatalogData` on the tab, and flips the catalog button's enabled state when the build resolves. Clicking the button then constructs the dialog with the pre-built catalog as a constructor argument — no GUI-thread blocking, no white-screen wait, no progress bar. First launch after a parser-version bump still pays the ~20 s cold rebuild cost but it happens in the background while the user uses the file list.",
            "[Fix] Encrypted-XML sidecar previews (`.pac_xml`, `.app_xml`, `.prefabdata_xml`, `.pami`, `.spline`, `.spline2d`, `.mi`) now render cleanly. Pearl Abyss ships these files as UTF-8 with a leading BOM (`EF BB BF`); the previous text reader used `encoding='utf-8'` which leaves the BOM as a literal `\\ufeff` character at the start of the string, and QPlainTextEdit renders that as an invisible / boxy glyph that pushed the actual XML off the visible area on some Qt builds — making users think the preview was empty or unreadable. The text reader now sniffs encoding by walking `utf-8-sig` (silently strips the UTF-8 BOM) -> `utf-16` -> `utf-8` -> `cp1252` and keeps the first successful decode, so any BOM-prefixed XML, UTF-16 game text, or Latin-1 fallback file renders correctly. Affects every text-category preview, not only mesh sidecars.",
            "[Feature] New Item Catalog Browser dialog opens from a button next to the Explorer search bar. Shows all 19,692 items the iteminfo / multichange game-data tables describe in a categorised image grid, organised through the same `top_category` -> `category` -> `subcategory` -> `subsubcategory` chain the Item Catalog tab already uses (Equipment -> Weapon -> Melee -> Sword/Axe/Mace/Spear/Hammer/Dagger/Rapier/Fist; Equipment -> Weapon -> Ranged -> Bow/Crossbow; Equipment -> Weapon -> Firearm -> Pistol/Musket/Shotgun/Cannon; Equipment -> Weapon -> Polearm -> Spear/Pike/Halberd; Equipment -> Weapon -> Special -> Torch/Rod/Scythe/Flail/Fan/Lantern/Bola/Bomb/Flag/ElementalThrower/BlowPipe; Equipment -> Armor -> Head/Body/Hands/Feet/Back/Face; Equipment -> Mount & Pet Gear; Material; Tool; Document & Quest; Special; Misc). Search bar at the top reuses the same two-tier token matcher as the Explorer file list — `canta plate armor` returns just the chest piece, not the cloak / hand / foot variants of the same set. Single click highlights an item and shows its PAC paths, icon path, and prefab hashes in the side info pane; double click (or the Open in Explorer button) closes the dialog and scopes the Explorer file list to every file connected to the picked item — PAC, paired DDS textures, sidecar XMLs, prefabs, mesh sidecars, item icon — all surfaced in one filtered view.",
            "[Feature] Catalog Browser thumbnails decode lazily off the GUI thread via `QThreadPool` and cache through `QPixmapCache` (96 MB pool). DDS decoding goes through the same `core.dds_reader.decode_dds_to_rgba` pipeline that powers the Explorer image preview, so DX10 R8 / R10G10B10A2 / R16F / R32F textures render alongside the legacy DXT1/3/5 / BC4-7 ones — the Canta Plate Armor inventory icon (`itemicon_prefab_cd_m0001_00_so_phm_ub_22170.dds`, R10G10B10A2_UNORM) shows correctly without falling through the BC1 fallback. In-flight decodes are tracked per icon path so the same DDS is never queued twice when the user scrolls back over a row.",
            "[Feature] `ItemCatalogRecord` gains two enrichment fields populated during `build_item_catalog`: `display_name` (resolved from the `loc_key` against `localizationstring_eng.paloc` so 19,598 of 19,692 records carry their English name without callers having to re-load the localisation table) and `icon_paths` (every `ui/itemicon_prefab_<base>.dds` that resolves to a record's PAC files, discovered by walking every package group's PAMT once at build time). The catalog cache fingerprint includes a parser-version string mixed in via `build_cache.fingerprint_strings`, so the new fields cause a one-time silent rebuild on next launch — old pickles are dropped without prompting the user.",
            "[Fix] Encrypted-XML sidecar previews (`.pac_xml`, `.app_xml`, `.prefabdata_xml`, `.pami`, `.spline`, `.spline2d`, `.mi`) now render cleanly. Pearl Abyss ships these files as UTF-8 with a leading BOM (`EF BB BF`); the previous text reader used `encoding='utf-8'` which leaves the BOM as a literal `\\ufeff` character at the start of the string, and QPlainTextEdit renders that as an invisible / boxy glyph that pushed the actual XML off the visible area on some Qt builds — making users think the preview was empty or unreadable. The text reader now sniffs encoding by walking `utf-8-sig` (silently strips the UTF-8 BOM) -> `utf-16` -> `utf-8` -> `cp1252` and keeps the first successful decode, so any BOM-prefixed XML, UTF-16 game text, or Latin-1 fallback file renders correctly. Affects every text-category preview, not only mesh sidecars.",
            "[Feature] Explorer search bar now uses two-tier token matching instead of plain substring `in`. Both the query and each row's display name + path + alias are split into lowercase tokens (split on whitespace, `_`, `-`, `/`, `\\`, `.`, `:`, `|`, plus CamelCase boundaries) and a row matches when every query token is a prefix of at least one corpus token. Tier A scans display names only; Tier B scans the full corpus (display + internal name + path). When any row qualifies for Tier A, only Tier A results are shown — so `canta plate armor` returns just the chest piece (`Canta Plate Armor`), not the cloak / hand / foot / helm variants of the same set whose internal name shares the `PlateArmor` CamelCase token chain. Tier B kicks in only when the query has no display-name match (queries by raw path, model ID, or stem suffix). Solves four real user complaints in one pass: (1) `canta` no longer surfaces `Eccanta Plate Armor` because the prefix isn't at the start of the `Eccanta` token; (2) `canta plate armor` returns only the actual armor piece, not its cloak / hand / helm siblings whose internal names share the `PlateArmor` CamelCase chain; (3) the natural-language phrase with spaces now matches the internal name thanks to CamelCase splitting; (4) typing the lowercase coded stem `cd_m0001_00_so_phm_ub_22170` still works because the same tokenizer splits underscores. New `utils/text_search.py` module is the single source of truth so future code (Item Catalog tab, Audio search, Translate search) can adopt the same matcher.",
            "[Fix] Search aliases built by `core.item_index.build_item_index` now preserve the original CamelCase spelling of the iteminfo `internal_name` alongside its lowercased form. Previously only `item.internal_name.lower()` was added to `model_base_aliases`, which collapsed `Canta_PlateArmor_Armor` into the single token `canta_platearmor_armor` once the new tokenizer split underscores — destroying the boundary between `plate` and `armor` and breaking natural-language search for two-word item types ('plate armor', 'great sword', 'war hammer'). Both forms are now stored so the tokenizer can recover the word boundary from the CamelCase variant.",
            "[Fix] Item Catalog disk cache now invalidates automatically when the parser logic changes, not just when the source PAMTs change. A new `_PARSER_VERSION` constant in `core.item_catalog` is mixed into the cache fingerprint via `build_cache.fingerprint_strings`, so the May 3 2026 0x0E -> 0x0F delimiter fix (and any future parser-only fix) silently rebuilds the cached catalog on next launch. Without this, users carrying a stale `~/.crimsonforge/cache/item_catalog.pkl` from a pre-fix run would keep seeing every canta-class item with `display_name = ''` until they manually deleted the cache file.",
            "[Fix] Item / weapon search by display name now works again on game installs that received the May 3 2026 patch. Pearl Abyss changed the iteminfo prefab-block delimiter byte from 0x0E to 0x0F, which silently broke `core.item_index.build_item_index` and `core.item_catalog.build_item_catalog` — both walked the iteminfo bytes looking for 0x0E and skipped every item, so the resolver's `model_base_aliases` map ended up empty and every display-name lookup ('White Wind Rapier', 'Mace of Ambition', 'Sword of Greed', etc.) returned zero hits in the Explorer search bar. The parser now accepts either delimiter, so search works on patched and pre-patch game installs without users having to roll back.",
            "[Fix] DDS preview no longer fails with 'Cannot load image' on DX10 uncompressed textures. `core.dds_reader.decode_dds_to_rgba` previously had decoder branches only for the legacy DXT1/3/5 + BC4-7 + Luminance / uncompressed-BGRA8 formats, so any file with `info.format` starting with `DX10 (DXGI=…)` and a non-block-compressed payload fell through to a `Unsupported DDS format for preview` error. Added vectorised numpy decoders for: R8_UNORM / R8_UINT (DXGI 61, 62 — region maps, single-channel masks), R10G10B10A2_UNORM / UINT (24, 25 — climate texture), R16G16B16A16_FLOAT (10 — reflection probes, e.g. `referencearealightprefiltered.dds`), R32G32B32A32_FLOAT (2), R16_FLOAT (54), R16_UNORM (55), R32_FLOAT (41), R32_UINT (43), R8G8B8A8_UNORM/sRGB/UINT/SNORM (28-31), B8G8R8A8 family (87-91). HDR formats are tone-mapped via auto-normalised gamma 1/2.2 so reflection probes with peak values > 1 still render with visible mid-tones.",
            "[Fix] DDS preview also auto-decompresses self-compressed type-1 DDS files (LZ4-compressed mip 0 with the on-disk size packed into the header reserved area). When a DDS file's body is shorter than the header declares, `decode_dds_to_rgba` now runs `_decompress_type1_dds_per_mip_sizes` followed by `_decompress_type1_dds_first_mip_lz4_tail` before validating, so previews work even when the VFS layer returned the raw payload because the PAMT compression flag was missing. Affected files include `leveldata/global_regionmap.dds`, `leveldata/global_extraregionmap.dds`, and a wide range of single-mip DX10 textures.",
            "[Fix] Mesh parsing — the `.pam` static-mesh parser now handles inline LZ4-compressed geometry sections. Pre-patch parser assumed bytes at offset `geom_off` were raw vertex / index data; the May game patch shipped a class of `.pam` files that store decompressed-size at header `0x40` and on-disk LZ4-compressed-size at `0x44`. When `0x44` is non-zero, the geometry block is now decompressed in place via `lz4.block.decompress` before the submesh table walk, restoring previewability for every affected static prop. Thanks to @Rothfeld.",
            "[Fix] `.pam` stride detection on meshes with more than 65,535 vertices no longer silently corrupts geometry. The combined-buffer probe used `< total_verts` to validate u16 indices; once `total_verts > 65535` every possible u16 value is trivially less than `total_verts` and the probe accepted the first stride candidate (always 6) regardless of correctness. Stride is now derived algebraically from `(geom_decomp - total_idx*2) / total_verts` whenever the decompressed-size field is known. Same fix applied to the PAMLOD sequential-scan path via the new `seq_alg_stride` precomputation. Thanks to @Rothfeld.",
            "[Fix] `.pamlod` parser now decodes the four distinct per-LOD chunk-table layouts seen in shipping files. Format A `[start_offset, decomp_size, lz4_size]` (e.g. `cd_barricade_gaurd_02.pamlod`), Format B `[decomp_size, lz4_size, end_offset]` with anchor entry (e.g. `cd_puzzle_anamorphic_north_01.pamlod`), Format C — like A but with an all-zero placeholder at index 0 (e.g. `cd_spot_tower_10_stairs_01.pamlod`), Format D `[lz4_of_prev, start_offset, decomp_size]` (e.g. `cd_aka_house_module_b_roof_0002.pamlod`). Per-LOD LZ4-compressed chunks are inflated independently before vertex / index decoding. Thanks to @Rothfeld.",
            "[Fix] `.pamlod` LOD ordering — for sequential-scan files where the DDS texture entries appear out of order in the header, lod_groups are now sorted by descending total vertex count so the highest-quality LOD is always indexed as LOD0. For chunk-table files where the table's LOD order differs from vertex-count order, `_match_chunks_to_groups` pairs each chunk to its lod_group by solving `tv*stride + ti*2 == chunk_size` (handles cases where LOD0 has fewer verts than LOD1, e.g. some egg-shaped composite props). Thanks to @Rothfeld.",
            "[Fix] `.pamlod` DDS texture-name regex widened from `[^\\x00]{1,255}\\.dds\\x00` to `[^\\x00]{0,255}dds\\x00`. Some composite objects (cave stalactites, large multi-part props) store just `dds\\0` with no path prefix, which the old regex skipped. Added `voff*6 > geom_size` filter to drop false-positive hits from material-name strings sitting 0x10C bytes past the texture name. Thanks to @Rothfeld.",
            "[Fix] `.pam` parser now accepts the `XAR ` magic variant. Files with this magic share the PAR layout but carry no parseable geometry; the parser previously raised `bad magic XAR ` and bubbled up as a hard error. They now return an empty `ParsedMesh` so callers can continue. Thanks to @Rothfeld.",
            "[Fix] `.dds` per-mip decompression respects the actual header size for DX10 files. `_decompress_type1_dds_per_mip_sizes` previously hardcoded a 128-byte header offset, so DX10 files (148-byte header — 128 standard + 20 DX10 extension) had their first mip silently mis-aligned by 20 bytes. Now uses `info.data_offset` for both the body slice start and the header preamble copy.",
            "[Fix] `.dds` per-mip-size table now returns a non-`None` value for DX10 uncompressed formats. Added explicit byte-per-pixel entries for the DXGI IDs the game uses: 28-31 (RGBA8), 87-91 (BGRA8), 24-25 (R10G10B10A2), 10 (R16G16B16A16F), 2 (R32G32B32A32F), 54-55 (R16), 41-43 (R32), 61-62 (R8). Previously every uncompressed DX10 file returned `None`, making the per-mip type-1 decompressor bail out and hand back the raw compressed bytes.",
        ],
    ),
    (
        "1.23.1", "2026-05-01", [
            "[Performance] Lazy-tab open is now ~100 ms on second launch instead of 30-90 s. The Item Catalog, Dialogue Catalog, and Audio tabs now persist their parsed-game-data snapshot to `~/.crimsonforge/cache/<name>.pkl` and re-load it on next launch. The cache is keyed on a fingerprint of the source PAMT files (size + mtime), so a Steam patch silently invalidates and rebuilds — no manual cache-busting needed. First-ever open after a fresh install / patch still pays the build cost; everything afterwards is essentially instant.",
            "[Fix] Item Catalog and Dialogue Catalog lazy-tab race condition fixed. Both tabs used to spawn an inner FunctionWorker thread from inside the lazy-tab's outer worker thread, so the outer worker reported the tab 'ready' (overlay disappeared) while the inner build was still grinding for another 30-90 s. The user saw the loading overlay vanish into a half-empty tab. Both tabs now detect when called from a worker thread and run the build inline, marshalling the result back to the UI thread via a queued `_lazy_init_finished` signal. Manual Refresh-From-Game button still uses the inner-worker async path so it doesn't freeze the UI.",
            "[Performance] Defer the `ai.provider_registry` import (~2 s warm / ~14 s cold on first launch — pulls openai, anthropic, gemini, deepseek, ollama, vllm, mistral, cohere, custom, and deepl SDK modules) until the first AI-using tab actually calls a registry method. `MainWindow` now accepts a `registry_factory` callable, wraps it in a `_LazyRegistryProxy` shim, and resolves the real registry on first attribute access. Users who never open Translate / Settings save the import cost entirely.",
            "[Feature] New `utils/build_cache.py` — small pickle-based on-disk cache for expensive game-data builds. `cache_dir()`, `fingerprint_paths(paths)`, `fingerprint_strings(parts)`, `load_cached(name, fingerprint)`, `save_cached(name, fingerprint, payload)`, `invalidate(name)`, `invalidate_all()`. Atomic write via temp-file + rename so a Ctrl-C mid-write can never leave a half-written cache file. Corrupt-cache reads are silently dropped and trigger a fresh rebuild. Saves `~/.crimsonforge/cache/<safe_name>.pkl`.",
            "[Refactor] `core.dialogue_catalog.build_dialogue_catalog_cached`, `core.item_catalog.build_item_catalog_cached`, and `core.audio_index.build_audio_index_cached` are the new disk-cached entry points. The original `build_*` functions remain pure (uncached) for tests + tooling that want a deterministic always-rebuild path.",
        ],
    ),
    (
        "1.23.0", "2026-05-01", [
            "[Reverse Engineering] Reverse-engineered the PAA animation container end-to-end. The `0xC0` and `0x00` flag variants both decode now: tagged + untagged + link-with-embedded-tracks + link-with-target-resolution. Per-bone keyframe records are 10 bytes (4 fp16 quaternion + u16 frame index, sparse). Bone-major track layout with frame-index drop signalling the boundary between bones. Verified against `cd_damian_*walk*.paa`, `cd_phw_basic_*.paa`, and the wider shipping corpus.",
            "[Reverse Engineering] Cracked the per-bone identity in PAA link-with-embedded-tracks files: each rotation track is preceded by an inter-track gap whose canonical layout stores a 24-bit PAB bone hash at byte offset `gap_size - 9`, padded with one zero byte and followed by a u32 keyframe count. Hash lookup against the matching PAB is exact 23/23 on Damian's walk PAA — zero false positives, no heuristics, fully deterministic.",
            "[Reverse Engineering] Two-record-validated track walker eliminates phantom 1-keyframe tracks. The previous walker accepted any single 10-byte chunk that decoded as a unit quaternion + small frame index, which caused gap-header bytes to register as fake keyframes and split each real track into many fragments. The new walker requires two consecutive records with monotonically increasing frame indices before committing a track start. Reduces decoded track count from 43 (20 phantoms) to exactly 23 on Damian's walk.",
            "[Reverse Engineering] PAB skeleton parser bone-count fix — `bone_count` is u16 LE at file offset `0x14`, not u8. Reading it as u8 silently truncated any skeleton with > 255 bones to its low byte (phw_01.pab claimed 178, actually has 434; Damian's PAB claimed 192, actually has 448). The truncation was the root cause of the spike-shard explosion on FBX import: vertices weighted to bones beyond index 255 found no target bone, got identity-stub influence, and visually scattered across world space.",
            "[Reverse Engineering] PAB per-bone record format pinned: `[3 bytes hash_lo24][1 byte name_len][N bytes name][4 bytes parent_index][64 bytes bind_matrix][64 bytes inv_bind_matrix][64 bytes bind_copy][64 bytes inv_bind_copy][12 bytes scale][16 bytes rotation_quaternion][12 bytes position][1 byte alignment]` for a stride of `305 + name_len`. The previous heuristic name-terminator scan was correct only for the first ~56 bones before drifting catastrophically; the length-prefix byte at offset 3 of the per-bone record fixes parsing for the entire 246+ shipping PAB corpus.",
            "[Reverse Engineering] PAC vertex skin layout corrected — bytes 28-31 are the four u8 bone weights (sum 240-255 due to fixed-point quantisation headroom), bytes 32-35 are the four u8 bone-palette slot indices. The previous parser had these reversed, which decoded most vertices as zero-weight (because the 'weight' byte at the wrong offset was usually 0 except for slot 0) and routed the remaining vertices to wrong bones. Fix recovers correct per-vertex weighting for character meshes — Spine has 170 verts at chest height instead of zero, Thighs each get ~165 verts at hip height instead of zero, etc.",
            "[Reverse Engineering] PAC weight normalisation now divides by the actual u8 weight sum (240-255) instead of by 255. Pearl Abyss leaves headroom in the weight quantisation; dividing by 255 gave each vertex only ~94% of its intended skin influence and Blender's cluster math drifted the missing 6% along each vertex's bind-pose ray towards the mesh origin, producing the spike-shard pattern across the whole character.",
            "[Reverse Engineering] PAPR (Pearl Abyss Physics Proxy) container decoded. The file is ChaCha20-encrypted then LZ4-block compressed; the inner payload is a PAR-headed table of `(P_<bone_name>, <bone_name>)` pairs that map cloth/jiggle physics-proxy bones to their driving skeleton bones. 25,196-byte decompressed payload on the phw_01 rig.",
            "[Reverse Engineering] PABC (Pearl Abyss Bone Cache) container decoded for the per-mesh skinning palette. After ChaCha20 + LZ4 the payload is `[16 byte PAR header][4 byte u32 record_count][N records of 196 bytes each]` with each record laid out as `[1 byte flag][3 byte 24-bit PAB hash][3 × 64-byte fp32 mat4]`. New `core/pabc_skin_palette.py` parses the file and resolves every record's hash to a PAB bone index. 437/437 records resolved on Damian's body PABC.",
            "[Reverse Engineering] FindBoneByName runtime function located inside the shipped `CrimsonDesert.exe` at `0x140677E0` via x64dbg string-reference search on the bone-name table. Adjacent global IK lookup table at `0x145DFF6E8` populated by the engine at character load with `(bone_pointer, runtime_slot_index)` pairs for the 30+ well-known bones (Bip01, Pelvis, Neck, Head, B_Eyeball_L/R, Clavicles, UpperArms, Forearms, Hands, Thighs, Calfs, Feet, Toes, Fingers). Documented for future work on the `SkinMeshLodBoneToOriginalBoneIndexBuffer*` and `SkinMeshLodSkinMatrixIndexBuffer*` GPU buffers (`0x14281C8E0`, `0x14281C962`, `0x14281C9E4`, `0x14281CA66`).",
            "[Feature] New `core/animation_parser.py` PAA decoder — `parse_paa()` returns a fully-populated `ParsedAnimation` with `bind_pose`, `keyframes` (densified per-frame from the sparse PAA encoding), `metadata_tags`, `format_variant`, `is_link`, `link_target`, `embedded_tracks_absolute` flag for the per-frame composition mode. `parse_paa_with_resolution()` follows link-variant references through the VFS up to a configurable hop count so the 19% of the corpus that uses link references resolves to a real animation instead of returning an empty shell.",
            "[Feature] New `core/skeleton_parser.py` PAB decoder — `parse_pab()` returns a `Skeleton` with full `Bone` records (index, name, parent_index, 4x4 bind_matrix, 4x4 inv_bind_matrix, scale, rotation quaternion, position). All 246/246 known shipping PABs parse without drift; identity-stub fallback on any per-bone validation failure prevents garbage data from poisoning downstream consumers.",
            "[Feature] New `core/skeleton_resolver.py` (extended) — single source of truth for mapping a PAC/PAA asset path to its shared rig. Knows all 16 known rig prefixes (phm, phw, ptm, ptw, pfm, pfw, ppdm, ppdw, pgm, pgw, prh, nhm, nhw, ngm, ngw, rd) and falls back to a manual VFS browse when auto-resolve misses. The user's choice is saved per rig class in config so future exports of the same character class skip the dialog.",
            "[Feature] New `core/pabc_parser.py` morph-target file decoder — handles the SECOND use of the PAR `.pabc` container (character-creation slider deformations, separate from the mesh-skinning use). `parse_pabc()` and `serialize_pabc()` round-trip every `.pabc` and `.pabv` in the live game byte-for-byte. Backed by 27 regression tests in `tests/test_pabc_parser.py`.",
            "[Feature] New `core/pabc_skin_palette.py` mesh-skinning PABC decoder — separate from the morph-target parser, this one walks the 437-record bone palette + matrix table and exposes `slot_to_pab(slot)` for downstream consumers. The palette parsing is correct; the per-submesh slot indirection (which submesh-local slot maps to which palette record) is the remaining piece for the upper-body skinning fix and is documented in the module's docstring.",
            "[Feature] New `core/character_asset_resolver.py` — given a search string ('ogre', 'hexe', 'marie') or a character key (`CD_M0001_00_Ogre`), walks the live game archives and returns a categorised `CharacterAssetBundle` listing every mesh, animation, texture, morph file, physics file, effect, sequencer, prefab, XML config, and database row that mentions the character. The Ogre alone has 506 files with 'ogre' in the name plus 19 `.pabgb` tables that reference it by character-key only — the resolver does that hunt in one pass instead of asking the user to find them by hand.",
            "[Feature] New `core/character_bulk_export.py` — given a `CharacterAssetBundle`, dumps every related `.pac` mesh as `.obj` plus every `.dds` texture into a user folder, with a `manifest.json` that records the source VFS path of each file and an `import_blender.py` script that loads everything into a single Blender scene with auto-rigged armatures. Round-trip path back into the game gated by the existing baseline manager so re-import is idempotent.",
            "[Feature] New `core/character_bulk_reimport.py` — closes the round-trip loop opened by the bulk exporter. Reads the `manifest.json`, finds each edited `.obj` in `meshes_obj/`, rebuilds the corresponding `.pac` using the existing mesh-importer pipeline, and patches the rebuilt mesh back into the live archive. Donor vertex data is sourced from the `meshes_pac_original/` snapshot (saved by the bulk export) instead of the live archive, so re-imports stay byte-stable across multiple iterations.",
            "[Feature] New `ui/dialogs/character_hub_dialog.py` — unified per-character workspace. Type a character name, see every related file in the game, act on them with one click. Search bar + header banner with key + counts + size + per-category file lists with double-click open. Bulk Export folder + Bulk Re-import folder buttons drive the resolver / exporter / re-importer pipeline.",
            "[Feature] New `ui/dialogs/pabc_viewer_dialog.py` — viewer for `.pabc` / `.pabv` morph-target files. Shows the parsed header, payload statistics, the per-row fp32 delta grid, and Save-as-CSV / Save-as-JSON export buttons. Designed for community RE work: rows that are 'all zero' are unused morph targets, rows with extreme values are active customisation deltas.",
            "[Feature] FBX exporter (`core/mesh_exporter.export_fbx_with_skeleton`) now emits a single FBX containing mesh + skeleton + animation curves. AnimationStack / AnimationLayer / AnimationCurveNode / AnimationCurve nodes wired to bone Lcl Rotation properties; armature Null parent so Blender's importer recognises the bone tree as one armature object and binds the Action correctly. Z-up scene with `UpAxis=2`, intrinsic XYZ Euler decomposition with sign-aware gimbal-lock handling, quaternion canonicalisation across adjacent keyframes to eliminate 360° interpolation jumps.",
            "[Enhancement] FBX skin clusters use `bone.bind_matrix` (PAB world bind) for `TransformLink`, axis-converted Y-up to Z-up via the `_yup_to_zup_mat4` helper so the skin math doesn't drift between the mesh vertex coords (already converted) and the cluster's bone reference (also converted now). Previously the bone reference was left in Y-up while everything else was Z-up, mixing coord systems and producing the original spike-shard explosion that prompted this whole rewrite cycle.",
            "[Enhancement] FBX bone Models declare `InheritType=1`, `RotationOrder=0`, `Size=1.0`, and direct `Lcl Rotation` (instead of PreRotation) so Blender's importer reads the rest pose verbatim into the pose-bone matrix at frame 0 — no auto-orientation drift between LimbNodes and the cluster's TransformLink.",
            "[Enhancement] `.cfmeta.json` sidecar v2 — preserves `source_vertex_map` plus `filtered_vertices` + `filtered_faces` so the spike-filter (off by default) round-trips correctly: vertices that were removed pre-export are recreated at import time from the saved vertex IDs and face references, keeping the rebuilt mesh byte-stable across multiple edit cycles.",
            "[Fix] Right-click 'Export Full Character FBX (Mesh + Bones + Animation)…' is HIDDEN in this version. The mesh + skeleton + animation pipeline ships verified, but the per-submesh slot-to-bone palette resolution (which lives inside the game's compiled `SkinMesh*` C++ classes) is not yet 100% accurate for the upper body of certain character meshes. Hiding the action prevents users from generating FBXs with subtle skin-shatter artefacts on the torso / arms / head while the remaining piece is being decoded. The underlying parsers and exporters all stay in place — see `core/pabc_skin_palette.py`, `core/animation_parser.py`, `core/skeleton_parser.py`, and `core/mesh_exporter.export_fbx_with_skeleton()`. The 'Export as OBJ' and 'Export as FBX' (mesh-only) right-click actions are unaffected.",
            "[Tooling] 60+ new probe / verification scripts under `tools/` for PAA, PAB, PAC, and PABC reverse-engineering — `probe_paa_full_walk.py`, `probe_paa_bone_hash.py`, `probe_paa_format_reveng.py`, `probe_paa_gap_structure.py`, `probe_paa_hash_offsets.py`, `probe_paa_link_bytes.py`, `probe_paa_vs_bind.py`, `probe_pab_header.py`, `probe_pab_matrix.py`, `probe_pac_palette.py`, `probe_pac_weights.py`, `probe_bind_local_vs_world.py`, `probe_parser_vs_probe.py`, `probe_paa_absolute_test.py`, `verify_walk_animation.py`, `inspect_fbx_curves.py`, `test_damian_full_export.py`, `test_new_paa_parser.py`, plus the bone-name-based runtime IK lookup probes used during the live x64dbg session.",
            "[Tooling] New character-RE tooling under `tools/` for the boss-difficulty reverse-engineering side-quest — `find_difficulty_pabgb.py`, `dump_difficulty_rows.py`, `analyze_ogre_row.py`, `auto_bisect_ogre_scale.py`, `bisect_memory_hits.py`, `cheat_engine.py`, `memory_snapshot.py`, plus `OGRE_DEEP_TRACE.md`, `FLAME_KNIGHT_DEEP_TRACE.md`, `FINAL_REPORT.md`, and `MAP_TOOLTIP_UI_TRACE.md` documenting the trace methodology so future contributors can reproduce the work.",
            "[Thanks] Thank you to @Rothfeld for pull request #12 (\"roundtrip fbx to blender\") — the first community attempt at the FBX export + Blender edit + re-import round-trip. The v1.23.0 mesh / skeleton / animation work in this release builds directly on top of that round-trip pipeline, with the per-bone PAB/PAA/PAC/PABC reverse-engineering filling in the rig + animation curves that PR #12 first wired through.",
        ],
    ),
    (
        "1.22.9", "2026-04-24", [
            "[Feature] Placeholder locking during AI translation — every protected token the game expects back byte-for-byte (`<br/>`, `[EMPTY]`, `%0`..`%9`, `%%`, `%s`, `#27`, `{Key:...}`, `{emoji:...}`, `{Staticinfo:Knowledge:...#Korean}`) is now swapped for opaque Unicode sentinels (`⟦CF0⟧`, `⟦CF1⟧`, ...) before the text reaches the AI, then restored byte-identical after the response comes back. Cross-checked against all 178,864 Korean-paloc entries — 100% round-trip.",
            "[Feature] Hash-label braces keep the namespace locked while letting the AI translate the inner Korean label. `{Staticinfo:Knowledge:Knowledge_Hp#생명}` encodes as a paired sentinel `⟦CF0⟧생명⟦/CF0⟧` — the AI translates the 생명 between the brackets to HP / Vida / Vie / Lebenspunkte, and the decoder splices the AI's label back into the original namespace to produce `{Staticinfo:Knowledge:Knowledge_Hp#HP}`. Pearl Abyss's placeholder resolver still finds the data, and the in-game UI shows the localised label.",
            "[Feature] New 'Scan Placeholders' button next to Stop in the Translation tab. Runs over every translated entry and flags four kinds of damage: MISSING (source token the AI dropped), ALTERED (namespace / identifier mutated by the AI), LEAKED_SENTINEL (`⟦CF...⟧` that escaped decode), and EXTRA_TOKEN (AI invented a placeholder the source never had).",
            "[Feature] New color-coded QA dialog (ui/dialogs/placeholder_scan_dialog.py) — broken entries table with one row per entry, legend strip, issue-kind filter + key/text search, per-row details pane showing Source with tokens bold + Translation with broken spans highlighted by kind. Catppuccin-mocha palette: red for MISSING, peach for ALTERED, yellow for LEAKED_SENTINEL, blue for EXTRA_TOKEN.",
            "[Feature] Auto-fix button on every row plus 'Auto-Fix All' — repairs broken tokens with bounded string edits that NEVER touch translated prose outside the placeholder region. MISSING appends the source token at the end; ALTERED replaces just the broken token with the source's original at its exact span; LEAKED_SENTINEL strips the sentinel and collapses any double space. EXTRA_TOKEN is always left for human review (flagged, never auto-fixed).",
            "[Fix] Auto-fix no longer deletes the translator's Arabic / Korean / Spanish label inside a hash-label brace. Previously, if the AI altered the namespace of `{StaticInfo:Knowledge:Knowledge_LandSpider_BismuthQueen#ملكة سلطعون البزموت}` (e.g. case drift, translated the word 'Knowledge' inside the namespace, added a stray space, mutated the identifier), auto-fix replaced the WHOLE token with the source — losing the correctly-translated label and reverting the entry to English. Now auto-fix splices the source namespace + the original translated label, so users keep their work. Covered by 11 new regression tests against the v1.22.9 user-reported scenario plus Korean / Spanish / punctuation variants.",
            "[Feature] New core/translation_tokenizer.py — encode/decode + paired-sentinel machinery. 7 token families covered: hash-label brace, plain brace, angle tag, square bracket, `%%`, `%N` / `%s`, `#N`. Tolerant decoder recovers sentinels even if the model introduces whitespace or case drift inside the bracket pair.",
            "[Feature] New core/placeholder_scanner.py — scan/autofix/batch-summarise API. `scan_entry` pairs source and translated tokens by signature (`{ns#label}` compares by namespace only, since the label is meant to be translatable), then uses a greedy longest-shared-prefix match to distinguish ALTERED edits from MISSING+EXTRA combinations — so `{Key:Run}` → `{Key:Running}` reports as 1 ALTERED issue, not 2 issues.",
            "[Fix] Mesh double-patch corruption is GONE. Previously, running 'Import OBJ + Patch to Game' twice on the same PAC (even with the same OBJ) could shatter the mesh in-game, and patching the ORIGINAL OBJ back didn't fix it — only Steam's 'Verify Integrity of Game Files' restored the mesh. Root cause: the rebuilder sourced donor vertex data (bone weights, packed normals, material IDs) from the LIVE PAC on disk, so every subsequent patch inherited drift from the previous one. Fixed by snapshotting the pristine PAC bytes on first patch and always rebuilding donor data from the snapshot.",
            "[Feature] New core/mesh_baseline_manager.py — persistent snapshot store at `~/.crimsonforge/mesh_baselines/` with SHA-1 integrity check on every read, case-insensitive + Windows-slash-agnostic VFS-path keys, per-key write lock so concurrent patches can't corrupt each other's snapshot, and atomic filesystem writes so a crash mid-snapshot leaves the previous baseline intact.",
            "[Feature] New right-click 'Build PAC to Folder... (no patch)' action on any .pac / .pam / .pamlod in Explorer. Converts OBJ to the target binary and writes it to a user-chosen folder without touching game archives. Ideal for iterating on mesh edits — fast loop instead of the 10-20 s repack cycle on 870 MB PAZ files, and zero risk of corrupting the live game state.",
            "[Feature] New right-click 'Restore from Baseline (undo all edits)' action — one-click revert without Steam's Verify Integrity. Only appears when a baseline exists for the selected PAC (captured automatically on first import). Patches the pristine bytes back into the live archive with an automatic backup of the current state first.",
            "[Feature] 'Reload Game' button in the status bar. Rebuilds every cached view of the game — PAPGT index, every PAMT, VFS tree, paloc discovery, game version — without closing the app. Before this, editing files outside CrimsonForge or running Steam Verify required a full app restart, which dropped any in-flight translation project, open dialogs, and selections.",
            "[Feature] New core/game_reload_service.py — central coordinator that drives the reload and fans out to every registered tab. Tabs implementing a `reload_from_game(payload)` method get a chance to preserve in-flight work (Translate preserves the open project + autosave queue; Explorer preserves the selected package group + scope + search text). Tabs that haven't implemented the hook are demoted to 'needs re-init' so the next click refreshes them from scratch.",
            "[Feature] Automatic staleness detection — a 4-second mtime+size poll over `meta/0.papgt`, `meta/0.paver`, and every `NNNN/0.pamt` in the packages directory. When disk state drifts from what's in memory, a yellow 'Game files changed on disk' badge lights up next to the Reload Game button. One-shot per drift event so deliberate external edits don't nag the user every 4 seconds.",
            "[Enhancement] VfsManager gained a `reload()` method that drops every cache (PAMT index, PAPGT root, VFS tree, processing-warning dedup) and re-scans the packages directory. Called by the reload service, but safe to call directly from any caller that needs to force a fresh view.",
            "[Enhancement] PAMT cache auto-invalidated after every successful 'Import OBJ + Patch to Game' so the next read within the same session sees updated file-entry offsets without needing an explicit reload.",
            "[Enhancement] System prompt automatically gets a concise sentinel-preservation paragraph appended on every AI call so the model has explicit instructions about `⟦CFn⟧` semantics. Prompt stays short — one paragraph, imperative tone.",
            "[Enhancement] 54 regression tests in tests/test_placeholder_scanner.py covering every issue kind, the de-duplication contract (ALTERED doesn't get double-reported as MISSING+EXTRA), surgical-edit guarantees (prose outside the broken span is byte-exact), idempotence (running auto-fix twice is a no-op on second pass), Unicode / Korean prose preservation, the hash-label label-preservation contract (11 dedicated cases including the exact v1.22.9 user scenario), and integration with the real tokenizer's encode/decode path.",
            "[Enhancement] 28 regression tests in tests/test_translation_tokenizer.py cover every family + multi-family combinations + robustness to AI noise (case change / inner whitespace / unknown sentinels).",
            "[Enhancement] 27 regression tests in tests/test_mesh_baseline_manager.py pin the idempotence contract (snapshot-twice keeps first bytes, bytes don't drift across reads or manager restarts), the integrity guard (SHA-1 mismatch refuses to serve, missing bin or meta returns None), key normalisation (Windows + POSIX slashes share one key, case-insensitive, unicode paths supported), and the concurrency contract (two threads snapshotting the same key produce exactly one winner).",
            "[Enhancement] 25 regression tests in tests/test_game_reload_service.py cover bind + fingerprint lifecycle, registration / unregistration, fan-out correctness (every callback fires once, failing tab doesn't abort others), progress callback tolerance (a handler that raises doesn't crash reload), VfsManager.reload() cache reset + disappearing-packages-dir handling.",
            "[Enhancement] Full test suite now 603 tests passing (was 540 at start of v1.22.9). No regressions.",
        ],
    ),
    (
        "1.22.8", "2026-04-24", [
            "[Feature] New PAC XML editor — right-click any .pac_xml / .app_xml / .prefabdata_xml file in the Explorer and pick 'Edit PAC XML...' / 'Edit App XML...' / 'Edit Prefab Data XML'. A popup opens with every editable attribute + text node laid out as a searchable, filterable table (Path / Tag / Attribute / Value / Kind). Each row can be edited in place or via an editable details pane below the table; Save As writes to disk and Patch to Game re-serialises + re-compresses (LZ4) + re-encrypts (ChaCha20) + writes back into the live PAZ/PAMT/PAPGT chain with automatic backup.",
            "[Feature] New core/pac_xml_parser.py — parse / apply_edits / serialize for the multi-root XML format Pearl Abyss uses (UTF-8 BOM, CRLF line endings, tab indentation). Byte-for-byte identical round-trip verified on 30 of 30 real shipping .pac_xml files against a live Steam install.",
            "[Enhancement] Edit-state cell colouring in the dialog — red for rows with pending unsaved edits, green for rows saved or patched this session, subtle category tint (path / name / id / flag / version) for untouched rows. Re-editing a saved row reverts it to red; reverting drops back to the category tint. Colours are at ~40% opacity (category) or ~45% opacity (state) so text stays legible on dark themes.",
            "[Enhancement] Wide inline editor via custom QStyledItemDelegate — Value cells open with a 500-px-minimum QLineEdit (clear-button, framed) so long texture paths like `character/texture/cd_phw_00_eyecovermaterial_0001_n.dds` fit comfortably without the user having to horizontally scroll a cramped default editor.",
            "[Enhancement] Editable multi-line details pane at the bottom of the dialog, with an Apply-to-Row button + Revert-This-Row button. Primary editing surface for long or multi-line values; routes through the same setData path as the inline editor so red/green state colouring stays consistent.",
            "[Enhancement] core/file_detector.py registers .pac_xml / .app_xml / .prefabdata_xml as editable text files with XML syntax highlighting, so they get syntax-highlighted XML in the preview pane + the standard Edit action in the tree.",
            "[Enhancement] 34 new regression tests in tests/test_pac_xml_parser.py covering BOM handling, multi-root parsing, attribute + text enumeration, single + multi-attribute edits surviving round-trips, category classification, unknown-attr fallback, summary counts, dataclass shape, malformed XML handling, non-UTF-8 rejection.",
            "[Enhancement] Full test suite now 468 tests + 136 subtests = 604 scenarios passing (was 570 in v1.22.7).",
        ],
    ),
    (
        "1.22.7", "2026-04-24", [
            "[Fix] Character catalog + mesh sidecars work again on the post-April-2026 game patch. Pearl Abyss renamed three compound extensions: .app.xml -> .app_xml, .pac.xml -> .pac_xml, .prefabdata.xml -> .prefabdata_xml. 5,579 .app_xml, 12,692 .pac_xml, and 2,591 .prefabdata_xml files now live in the archives with the new names. Our encryption-detection list only recognised the old names, so every one of those files came back as raw ChaCha20 ciphertext — character appearance XML parsed as 'not well-formed', mesh sidecar discovery quietly returned nothing, etc.",
            "[Fix] core/pamt_parser.py encryption list now includes all three new extensions alongside the old ones, so both pre-patch and post-patch installs decrypt correctly.",
            "[Fix] core/asset_catalog.py accepts both .app.xml / .app_xml and both .prefabdata.xml / .prefabdata_xml when scanning the character catalog.",
            "[Fix] core/mesh_sidecar_service.py SIDECAR_KINDS tuple carries both old and new suffixes so Import OBJ + Patch to Game correctly picks up the renamed sidecars.",
            "[Investigation] 'Import OBJ + Patch to Game' 10-20x slowdown root-caused: the same game patch consolidated character PAZ archives from ~50 MB files into ~870 MB files (17x growth). Bob Jenkins Lookup3 is O(file-size) and must touch every byte — 570 ms per PAZ on NVMe is the floor. Benchmarked mmap / readinto / read alternatives; f.read() + pa_checksum is already optimal on Windows because ReadFile pre-fetches sequentially while mmap pays a page-fault per 4 KB. Documented the trade-off in code so future contributors don't reintroduce the regression.",
            "[Enhancement] 24 new regression tests in tests/test_patch_2026_04_extension_rename.py. 6 assert all three new extensions route through decryption; 3 verify the legacy forms still work; 7 guard unrelated extensions against accidental mis-flagging; 4 pin the SIDECAR_KINDS tuple; 4 cross-check _checksum_paz_file against pa_checksum() for boundary cases (empty, tail bytes, non-multiple-of-12).",
            "[Enhancement] Full test suite now 434 tests + 136 subtests = 570 scenarios passing (was 546 in v1.22.6).",
        ],
    ),
    (
        "1.22.6", "2026-04-22", [
            "[Fix] Ship-to-App no longer fails with \"'localizationstring_*.paloc' not in PAMT\" for ANY language. Root cause: core/pamt_parser.py had TWO `find_file_entry` definitions; the later one silently shadowed the earlier, dropping the basename-fallback that every Ship-to-App caller relies on. Consolidated into a single canonical lookup that handles full paths, bare basenames, Windows slashes, and mixed case in one O(n) pass. Works identically for every one of the 17 shipping languages (eng / kor / jpn / rus / tur / spa-es / spa-mx / fre / ger / ita / pol / por-br / zho-tw / zho-cn / tha / vie / ara).",
            "[Fix] DeepL translation no longer fails with \"DeepL SDK not installed, run: pip install deepl\" in the shipped exe. The deepl package wasn't in requirements.txt so it was never bundled by PyInstaller. Added deepl>=1.17.0 to requirements.txt and 'ai.provider_deepl' (plus every other provider module) to the spec's hiddenimports to guarantee all 11 providers ship with every future exe build.",
            "[Enhancement] 21 new regression tests in tests/test_find_file_entry.py pin down the canonical lookup contract: every shipping language's paloc resolves from its bare basename, full path, Windows slashes, and mixed case forms; a module-level guard asserts there is EXACTLY ONE `find_file_entry` definition so the shadowing bug cannot reappear.",
            "[Enhancement] Full test suite now 410 tests + 136 subtests = 546 scenarios passing (was 508 in v1.22.5).",
        ],
    ),
    (
        "1.22.5", "2026-04-22", [
            "[Fix] pa_checksum no longer triggers false-positive virus flags from Windows Defender / some third-party AVs. The previous MinGW-compiled ctypes DLL (core/pa_checksum.dll) matched heuristic patterns AVs associate with malware loaders. Switched to a MSVC-compiled Python C extension (core/_pa_checksum.cp*-win_amd64.pyd) loaded via Python's own import machinery, which inherits Python's AV trust path. No observable performance difference — same Bob Jenkins Lookup3 C core, cross-verified bit-for-bit against the old DLL's output.",
            "[Enhancement] Removed the ctypes fallback branch from core/checksum_engine.py — the dispatcher is now C extension when compiled, pure Python otherwise. Cleaner two-path logic instead of three.",
            "[Enhancement] CrimsonForge.spec simplified — no explicit binaries entry, the .pyd is auto-discovered through a hiddenimport. PyInstaller bundles it into the frozen exe via the Python extension machinery rather than as a loose DLL.",
        ],
    ),
    (
        "1.22.4", "2026-04-22", [
            "[Fix] FBX export of character meshes (cd_phm_*, cd_phw_*, cd_ppdm_*, cd_pgm_*, cd_pfm_*, etc.) now correctly finds the shared class-level skeleton. Previously the mesh export path used a sibling/basename search that was guaranteed to miss for character PACs (they share phm_01.pab / phw_01.pab / ppdm_01.pab class rigs, not per-mesh PABs). Root cause of the reported 'no armature in exported FBX' bug.",
            "[Feature] New core/skeleton_resolver.py — single source of truth for mapping a PAC/PAA asset path to its shared rig. Knows all 16 known rig prefixes (phm, phw, ptm, ptw, pfm, pfw, ppdm, ppdw, pgm, pgw, prh, nhm, nhw, ngm, ngw, rd). Used by both the mesh FBX export and the animation FBX export paths for consistency.",
            "[Feature] Manual 'Browse for .pab...' fallback when auto-resolve misses. The picker opens a filterable list of every PAB visible through the VFS, sorted by prefix-match first. User's choice is saved per rig class in config so future exports of the same character class skip the dialog automatically.",
            "[Feature] New core/crash_handler.py — diagnostic layer for silent early-boot failures. faulthandler.enable() captures native C-level crashes (access violations, DLL load failures). sys.excepthook captures uncaught Python exceptions. Windows ctypes MessageBoxW provides a native dialog fallback when Qt itself is what failed to initialise. Next time the exe exits silently we will have a full traceback in the log instead of guessing.",
            "[Fix] main.py now wraps QApplication(sys.argv) in try/except that surfaces a clear error message when Qt fails to initialise — typically after a hard reboot corrupted the PyInstaller extraction (%TEMP%\\_MEI*) or when VC++ 2015-2022 redistributable is missing.",
            "[Feature] New core/mesh_preflight.py — pre-flight memory check that warns before starting a mesh repack when available RAM is below the projected peak. Uses psutil primary path + Windows GlobalMemoryStatusEx fallback. Prevents the 'whole system drops to 1 FPS' symptom when Forge + Blender + JMM + JMM Creator are all open at once.",
            "[Performance] build_pac() in core/mesh_importer.py replaces a full copy.deepcopy(mesh) with a shallow wrapper copy + fresh submesh list. On a 20k-vertex character mesh the deepcopy walked every vertex/face/uv/normal tuple — hundreds of megabytes of allocation. Shallow copy is O(n_submeshes) instead.",
            "[Test] 508 test scenarios across the v1.22.4 changes: 179 for the skeleton resolver (every prefix, ranking rules, VFS integration, manual override), 33 for crash diagnostics (install/reset/excepthook/message-box fallback), 37 for the pre-flight memory check (estimator, probe chain, decision matrix), 34 for the OBJ sidecar round-trip, 29 for vertex-split propagation + shallow-copy regression, 109 for checksum engine, 87 for file type detection.",
        ],
    ),
    (
        "1.22.3", "2026-04-21", [
            "[Fix] OBJ re-import no longer loses skin weights on UV-seam vertices. When Blender splits a vertex for multiple UV/normal corners, the clone now inherits its source slot's bone indices and bone weights. Root cause of the reported 'model exploded after import' symptom.",
            "[Feature] New `.cfmeta.json` sidecar written next to every exported OBJ carries the original skin data (bone indices + weights per vertex). On re-import, the sidecar populates the source-vertex map so the PAC rebuilder picks the correct donor record for each vertex — survives user edits that move vertices far from any original position. Falls back gracefully to positional matching when the sidecar is absent.",
            "[Fix] Repack tab no longer appears empty after game load. `initialize_from_game()` now restores the last-used Modified Files directory from config and auto-scans it; a clear next-step hint is shown when nothing is configured.",
            "[Fix] Ship-to-App dialog no longer freezes the window during ZIP generation. `build_mesh_manager_package` and `build_mesh_ship_package` now run on a background `FunctionWorker`; progress bar updates live and the dialog surfaces errors instead of locking up.",
            "[Fix] FBX export of a PAC with a missing `.pab` skeleton no longer silently falls back to mesh-only. The exporter now searches every loaded PAMT first by sibling path, then by basename, and surfaces a confirmation dialog (with the exact reason) before producing an armature-less FBX.",
            "[Enhancement] `SubMesh.source_vertex_map` added — per-imported-vertex back-reference to the original slot. Consumed by the PAC full-rebuild path to route donor records correctly; empty when unused.",
        ],
    ),
    (
        "1.22.2", "2026-04-21", [
            "[Fix] Tab switching no longer freezes the window on first click. Materialisation is now three-phase: the loading overlay paints first, widget construction runs on the next UI tick, and game-data init runs on a background thread.",
            "[Fix] Qt currentChanged signal re-entrance during the tab swap is blocked, preventing duplicate materialisation.",
            "[Fix] User's selected tab no longer jumps when a non-focused tab is swapped — currentIndex is preserved across the swap.",
            "[Enhancement] TabInitContainer now supports lazy content installation via set_content().",
            "[Enhancement] 531 tests passing (+7 for the lazy-install path).",
        ],
    ),
    (
        "1.22.1", "2026-04-21", [
            "[Fix] Clicking a tab for the first time no longer locks the window for 5-30 seconds. Tab initialisation (PAMT indexing, paloc cross-reference, catalog builds) now runs in a background thread with a progress overlay.",
            "[Feature] New loading-overlay widget — progress bar + status label + Retry button. Flips to real tab content when init finishes.",
            "[Enhancement] Per-tab init state tracked; in-flight workers are de-duplicated; failures surface a Retry button that re-runs the same task.",
            "[Enhancement] 524 tests passing (+15 for the overlay state machine).",
        ],
    ),
    (
        "1.22.0", "2026-04-20", [
            "[Feature] Bone-mapping editor dialog — review and edit the auto-correlated PAA-track → PAB-bone mapping per rig. Saved per rig to %APPDATA%/CrimsonForge/bone_maps/<rig>.bonemap.json with colour-coded confidence.",
            "[Feature] child_idle PAA variant fully decoded — 112 tracks / 4,711 keyframes recovered (was 1 track in v1.21.1). Root bone uses stride 10; child bones use stride 8 with implicit W.",
            "[Feature] Link-variant VFS resolution verified end-to-end across primary-group, fallback-scan, and unresolvable cases.",
            "[Feature] Disconnected placeholder mesh resolved — joint cubes and limb cylinders enlarged to overlap, combined with split-weight limbs.",
            "[Feature] FBX → PAA writer now covers all three shipping variants (tagged, untagged, v3) with a unified dispatcher.",
            "[Fix] v3 parser scan window and walker bailout corrected for minimal bone blocks.",
            "[Enhancement] All five FBX-animation Known Issues tracked since v1.18.0 are now closed.",
            "[Enhancement] 509 tests passing (+21 across parser, integration, and writer).",
        ],
    ),
    (
        "1.21.1", "2026-04-20", [
            "[Feature] First-cut SRT-float / child_idle PAA variant parser — recovers 113 keyframes from the test sample where the v2 parser returned zero tracks.",
            "[Feature] parse_paa() auto-routes to v3 when v2 returns zero tracks — callers get real track data without knowing which variant a file uses.",
            "[Feature] Limb-prism vertices are now split-weighted — parent-end verts bind to the parent bone, child-end verts to the current bone. Limbs bend smoothly between joints instead of sliding past each other.",
            "[Enhancement] 499 tests passing (+13 for v3 parser and split-weight mesh).",
        ],
    ),
    (
        "1.21.0", "2026-04-19", [
            "[Feature] PAA → PAB bone mapping — auto-correlates from bind-pose angular distance with a JSON override saved per rig. After deep RE, confirmed that PAB bone names and common string hashes do not appear in PAA bytes; the mapping isn't in the file, so the auto-correlate seed + user override is the correct solution.",
            "[Feature] Link-variant PAA resolver — follows embedded %character/... paths across the VFS with a loop-guard. Covers the ~19% of shipping PAAs that point at other files instead of carrying their own animation.",
            "[Feature] parse_paa_with_resolution() — single entry point that follows link-variant references through a passed VFS.",
            "[Feature] FBX → PAA inverse writer — round-trips via the parser with bit-exact frame indices and fp16-precision quaternions.",
            "[Feature] export_animation_fbx() accepts a bone_map so PAA track i can drive PAB bone bone_map[i]; tracks mapped to -1 are excluded.",
            "[Known Issue] child_idle variant and disconnected placeholder mesh remain open this release (both closed in v1.21.1 / v1.22.0).",
            "[Enhancement] 486 tests passing (+35 across bone-mapping, link-resolver, and writer).",
        ],
    ),
    (
        "1.20.3", "2026-04-18", [
            "[Fix] Face-Part Browser 'Open Matching Prefab' now uses a reverse-reference index instead of a basename heuristic. Real corpus showed only 1 in 6 prefabs matched their PAC by basename; the new index scans every prefab once and answers queries in O(1).",
            "[Feature] prefab_reference_index module — case-insensitive PAC → prefab map with basename fallback and duplicate-add idempotency. When multiple prefabs point at the same PAC, the Explorer pops a selection dialog.",
            "[Enhancement] End-to-end flow tests exercise the prefab edit, state-machine browse, and face-parts pipelines against the real temp cache.",
            "[Enhancement] Prefab edit/patch path fuzzed — identity round-trip byte-exact, same-length edits preserve file size, length changes update size by exact delta, random 50-cycle edits always re-parse cleanly.",
            "[Enhancement] 451 tests passing (+20 across reverse-index, E2E flows, and fuzz tests).",
        ],
    ),
    (
        "1.20.2", "2026-04-17", [
            "[Fix] Face-Part Browser walks the VFS via the public list_package_groups + load_pamt API instead of a private cache — covers every shipping package group.",
            "[Fix] Face-part classifier regex expanded from 3 to 9 prefixes (ptm/phm/phw/pfm/pfw/ppdm/ppdw/pgm/pgw) so eye-detail and face-template PACs are no longer dropped from the catalog.",
            "[Feature] 'Show Sub-Parts' button reads the granular sub-parts bundled inside head_sub PACs (e.g. EyeLeft_0001, Tooth_0001, Eyebrow_0004).",
            "[Feature] 'Open Matching Prefab' button routes through the Explorer's existing edit flow in one click.",
            "[Enhancement] 431 tests passing (+3 real-corpus tests).",
        ],
    ),
    (
        "1.20.1", "2026-04-16", [
            "[Feature] New Face-Part Browser — catalogs every face-part PAC across loaded archives (Head, HeadSub, Eye, Brow, Lash, Tooth, Tongue, Nose, Lip, Mouth, Beard, Mustache, Hair, Ear, Face) with variant IDs extracted from the filename.",
            "[Feature] face_parts module — enumerated classifier with longest-prefix disambiguation, variant-ID extractor, and a granular sub-part scanner for head_sub PACs.",
            "[Feature] Category list with part/variant counts + filterable variant table; Copy Archive Path + Export Catalog CSV.",
            "[Feature] Explorer Quick Mods now includes 'Face-Part Browser...'.",
            "[Enhancement] Investigation confirmed the game's face-customisation paradigm is submesh swapping (enumerated variant PACs), not blendshapes or dedicated facial bones.",
            "[Enhancement] 428 tests passing (+14 face-part tests).",
        ],
    ),
    (
        "1.20.0", "2026-04-15", [
            "[Feature] New State-Machine Browser — cross-references every condition expression across 9 state-relevant pabgb tables and surfaces the underlying state tokens (ActionAttributes, Missions, Stages, CharacterKeys, Macros, Levels, Gimmicks).",
            "[Feature] state_machine module — byte-level tokeniser for the condition-expression grammar (FCALL allowlist, argument-identifier extraction, bare-identifier enum pass).",
            "[Feature] Token list sorted by occurrence frequency with category filter, text search, and min-occurrences threshold; CSV export per token.",
            "[Feature] Explorer Quick Mods now includes 'State-Machine Browser...'.",
            "[Feature] Known-enum catalogues exposed (ActionAttributes, CharacterKeys, MacroStates).",
            "[WIP] Face-morph investigation deferred. Hex-dumped head / eye / beard PACs: every 'shape' hit is Havok physics, not vertex deltas. The feature is bone-driven (facial rig bones + a per-character appearance blob) — scanner + blob parser tracked for a future release.",
            "[Enhancement] 414 tests passing (+14 state-machine tests).",
        ],
    ),
    (
        "1.19.1", "2026-04-14", [
            "[Fix] Prefab editor no longer crashes on open — replaced the QTableView call that only exists on QTreeView with the correct performance pattern (fixed header section-size-mode + per-pixel scroll mode).",
            "[Fix] Verified headless against a 76-row cloak prefab; 400 regression tests still pass.",
        ],
    ),
    (
        "1.19.0", "2026-04-14", [
            "[Feature] New .prefab editor — byte-level reverse-engineered parser for Pearl Abyss prefab assets (magic header + two 32-bit hashes, then a linear stream of length-prefixed UTF-8 strings classified by role).",
            "[Feature] Editor dialog with category filter, text search, live edit preview with length delta, per-string byte context, revert / save-as / patch-to-game.",
            "[Feature] Safe-mode 'Same-length edits only' (default ON) preserves binary layout; toggle off for length-changing edits with automatic length-prefix updates.",
            "[Feature] Five string categories colour-coded: File References, Tag/Enum Values, Property Names (read-only), Type Names (read-only), Other.",
            "[Feature] Tag values are paired with the nearest preceding tag-typed property (e.g. '_shrinkTag = Cloak') for clearer context.",
            "[Feature] Right-click .prefab in Explorer → 'Edit Prefab'. Patch-to-Game writes through the repack pipeline with automatic backup.",
            "[Feature] apply_edits() supports atomic multi-string rewrite with length deltas accumulated in order.",
            "[Enhancement] PAA link-variant detection scans offsets 0x14..0x100 for the '%' marker with prefix validation, exposing the detected offset to downstream consumers.",
            "[Enhancement] PAA bind-pose walker gains an offset+0/+4 probe for flag variants that insert a 4-byte hash before the first SRT record.",
            "[Enhancement] 400 tests passing (+16 prefab tests).",
        ],
    ),
    (
        "1.18.0", "2026-04-12", [
            "[Feature] New .pabgb / .pabgh game-data table editor — handles both the simple (5-byte entries) and hashed (8-byte entries) flavours discovered via byte-level inspection.",
            "[Feature] Editor dialog with searchable row list, filterable field table with auto-labels and colour coding, hex-dump pane with per-field highlighting, row comparison, duplicate/delete row, and patch-to-game.",
            "[Feature] .pabgb files now open in the editor automatically — previously only showed a hex preview, which blocked edits to iteminfo / stageinfo / conditioninfo / gimmickgroupinfo.",
            "[WIP] PAA → FBX animation export is under active reverse engineering in this release and is NOT production-ready. Use OBJ export for reliable mesh-only round-trips. Full working FBX animation export is tracked for a future release.",
            "[Enhancement] PAA 10-byte keyframe record format documented: [W:fp16][frame:uint16][xyz:3×fp16] per keyframe, sparse frame indices, per-bone implicit-W bind at the top of each block.",
            "[Enhancement] PAA bone-block separator reversed: '3c 00 3c 00 3c' + uint32 count + 6-byte bind + N × 10-byte records. Parser validates each record against |q|² ∈ [0.90, 1.10].",
            "[Enhancement] FBX export composes bind with PAA rotation (fbx_local_rot(t) = PAB_bind × PAA_rot(t)) so bind-pose angles match the expected values.",
            "[Enhancement] FBX export emits a skinned humanoid placeholder mesh so Blender's Armature modifier attaches on import.",
            "[Fix] PAB skeleton parser no longer emits phantom bones past the real count — phm_01.pab returns 56 real bones instead of 178 garbage-trailed ones that crashed Blender's FBX importer.",
            "[Fix] FBX bone positions multiplied by 100 (cm→m) so Blender doesn't collapse the skeleton into a sub-centimetre cluster at origin.",
            "[Fix] FBX bone count clamped to the PAB skeleton size — extra PAA tracks no longer emit origin-placed placeholder bones.",
            "[Known Issue] PAA tracks do not map 1:1 to PAB bone names — explicit mapping table not yet decoded. (Resolved in v1.21.0.)",
            "[Known Issue] child_idle / SRT-float variant decodes to zero tracks. (Resolved in v1.21.1 / v1.22.0.)",
            "[Known Issue] Link-variant PAAs (~19% of shipping corpus) not followed through the VFS. (Resolved in v1.21.0.)",
            "[Known Issue] Placeholder mesh is disconnected cubes + prisms. (Resolved in v1.21.1 / v1.22.0.)",
            "[Known Issue] FBX → PAA reimport not implemented. (Resolved in v1.21.0.)",
            "[Fix] PyInstaller bundle now includes numpy.",
            "[Fix] UPX compression disabled in PyInstaller spec — was corrupting the splash PNG on Windows 11.",
            "[Fix] Splash screen version text position corrected so the version string lands inside the brand banner.",
            "[Enhancement] 384 tests passing (+8 across PAA parser, placeholder mesh, bone-count clamp, and scale).",
        ],
    ),
    (
        "1.17.0", "2026-04-11", [
            "[Performance] Tabs are now lazily instantiated — only constructed when first clicked, cutting app startup time dramatically",
            "[Performance] Game loading moved to a background thread with a live progress bar so the UI stays fully responsive during initialization",
            "[Performance] PAMT scanning across all package groups now runs in parallel using up to 8 I/O threads via concurrent.futures",
            "[Performance] Explorer 'All Packages' loading moved to a background thread — no more UI freeze when browsing 1.45M+ files",
            "[Performance] All QTreeWidget instances now use setUniformRowHeights for instant height calculation instead of per-row measurement",
            "[Performance] All QTableView instances now use fixed row heights and per-pixel scrolling for smoother scroll performance",
            "[Performance] Dialogue Catalog and Item Catalog tree population wrapped with setUpdatesEnabled(False) to eliminate mass repaints during bulk insertion",
            "[Fix] 'Import WAV + Patch to Game' now correctly invalidates the audio player cache after patching so the new audio plays back immediately instead of the stale original",
            "[Fix] Mod Manager ZIP generation no longer crashes with a KeyError on asset_count — the manifest now includes the missing field",
            "[Fix] Added numpy to requirements.txt — resolves 'No module named numpy' error for 3D mesh preview",
            "[Community] OmniVoice TTS: added advanced parameters UI with individual toggles for Gender, Age, Pitch, Style, and Accent (contributed by imedox)",
            "[Community] OmniVoice TTS: added full PAZ location column, renamed JA to CH language code, made language dropdown searchable, and improved TTS UI styling (contributed by imedox)",
            "[Community] OmniVoice TTS: improved ref text auto-fill behavior and enabled text column resizing (contributed by imedox)",
            "[Community] OmniVoice TTS: updated and expanded supported language list (contributed by imedox)",
        ],
    ),
    (
        "1.16.1", "2026-04-08", [
            "[Fix] Standalone Windows builds now bundle ffmpeg and vgmstream helper tools directly inside the packaged app so audio workflows run on clean machines without first-run downloads",
            "[Fix] Bundled runtime now resolves packaged helper executables before user-space tool installs, making shipped builds more reliable on fresh systems",
            "[Fix] Release packaging now includes the helper tool trees alongside core/pa_checksum.dll and the packaged data directory in a single self-contained executable",
        ],
    ),
    (
        "1.16.0", "2026-04-07", [
            "[Feature] Explorer Navigator added as a dedicated popup workbench with live Characters, Items, and Families views built directly from installed game data",
            "[Feature] Navigator selections now scope the normal Explorer file table to exact related archive paths so preview, export, import, patch, ship, extract, and editor workflows continue to use the same Explorer rows",
            "[Enhancement] Navigator now preloads from the active game session and reuses the already loaded game path and PAMT cache instead of rebuilding a separate cold index every time the popup opens",
            "[Enhancement] Navigator UI/UX upgraded with clearer popup flow, active scope labeling, one-click clear scope, resizable split layouts, and zoomable image panels with Fit and 100% controls",
            "[Fix] Navigator DDS image preview now uses the correct decode path for live UI portraits and item icons, matching Explorer behavior instead of failing to load valid DDS files",
            "[Fix] DDS preview support expanded for additional type-1 compressed layouts, including prefixed-LZ4 and first-mip-LZ4-plus-tail families, so more portrait, impostor, and atlas textures open correctly instead of showing dots or noise",
            "[Fix] Unsupported short DDS payloads now fail cleanly with a real preview limitation message instead of fake dot/noise renders, reducing false corruption reports on edge-case textures",
            "[Fix] PAC preview parser now supports additional descriptor variants such as the Kliff/Macduff head layout, restoring full head mesh parsing instead of partial eyecover-only previews",
        ],
    ),
    (
        "1.15.0", "2026-04-06", [
            "[Fix] OBJ reimport now preserves the real face-level UV and normal index mapping from Blender exports instead of assuming position, UV, and normal indices always match",
            "[Fix] Mesh import now correctly splits reused vertices when one position is referenced with multiple UV or normal combinations, preventing mixed, floating, or scrambled textures after reimport",
            "[Fix] The Blender OBJ texture/material binding issue applies across PAC, PAM, and PAMLOD OBJ reimport workflows because the core importer now rebuilds vertices from the actual vi/ti/ni tuples",
            "[Hotfix] Full PAM topology rebuild now remaps hidden static-mesh donor payload by aligned spatial vertex matching instead of raw vertex index, reducing black shading and material corruption on edited static meshes with added geometry",
            "[Feature] Item Catalog tab added: browse raw live-game item data with deep category, subcategory, and subtype taxonomy, searchable tables, path filters, and detailed record inspection",
            "[Feature] Item Catalog exports added: generate enriched CSV/JSON catalogs from iteminfo, multichange, equip-type, slot, and related raw game tables directly from the installed game packages",
            "[Feature] Dialogue Catalog was rebuilt into an enterprise browser with Story, Speakers, and Families views, ordered conversation transcripts, search, filtering, and speaker-confidence reporting",
            "[Enhancement] Dialogue export pipeline now catalogs broad live-game dialogue coverage from localization families such as intro, epilogue, quest, AI ambient, memory, node, and scene-family keys",
            "[Enhancement] Raw game-data browsing improved with structured table indexing so non-item systems like factions, quests, NPCs, roads, and world tables can be discovered from package data faster",
            "[Enhancement] Live-package UI tracing and RTL investigation tooling was expanded for Arabic, font-swap, and English/number runtime debugging directly against Steam-installed game files",
        ],
    ),
    (
        "1.14.0", "2026-04-05", [
            # Audio / OmniVoice Enterprise TTS
            "[Feature] OmniVoice Local TTS provider added with native integration for localhost servers, live model discovery, voice catalog loading, health/status checks, and optional bearer-token auth",
            "[Feature] OmniVoice one-shot cloning now uses original game voice audio as a reference directly from the selected row, with automatic WEM/BNK decode to WAV for local AI synthesis",
            "[Feature] OmniVoice saved-profile workflow added: save or refresh voice profiles from the Audio tab, then synthesize with clone:<profile> voice mode for repeatable character dubbing",
            "[Feature] Audio tab now exposes advanced OmniVoice controls for inference steps, guidance scale, denoise, fixed duration, t_shift, position temperature, and class temperature",
            "[Feature] Batch Generate and Generate All + Patch added to the Audio tab for large-scale NPC redubbing workflows across selected or filtered voice rows",
            "[Enhancement] Audio generation now normalizes provider output formats like MP3 back to WAV automatically before playback, history storage, and WEM patch conversion",
            "[Enhancement] Audio settings changes now refresh the Audio tab immediately so provider availability, OmniVoice URL/token/model, and provider UI state update as soon as settings are saved",
            "[Enhancement] OmniVoice defaults now auto-suggest clone profile names, use selected-row reference audio, prefer unique NPC profile IDs where possible, and bias adult male/female design voices intelligently",

            # Dialogue Coverage / Audio Linking
            "[Enhancement] Audio text linking now scans all .paloc files instead of only localizationstring*.paloc, allowing wider dialogue coverage from broader game text datasets",
            "[Enhancement] Audio filename parsing and paloc linking now recognize more dialogue key families such as faction, npcvoice, npcdialog, textdialog, memory, and general-style voice keys",
            "[Enhancement] Audio linker now tries safe paloc-key aliases for common naming-variant families, improving linkage when audio and localization keys use slightly different prefixes",
            "[Enhancement] Audio index logging now reports text-link coverage percentage directly for easier enterprise QA of dubbing readiness",

            # Stability / Patch Flow
            "[Fix] Audio patch and TTS patch flows now report RepackEngine error lists correctly instead of reading a non-existent single error field",
            "[Fix] Audio tab no longer had legacy TTS patch handlers overriding the newer enterprise workflows, ensuring OmniVoice, batch operations, and normalized audio handling are actually used at runtime",
        ],
    ),
    (
        "1.13.0", "2026-04-05", [
            # Ship to App / Mod Manager Packaging
            "[Feature] Explorer Ship to App now supports a new Mod Manager ZIP (small) mode that exports rebuilt loose mesh files plus manifest.json, modinfo.json, and README.txt for manager-based installs",
            "[Feature] Translate Ship to App now supports the same small Mod Manager ZIP workflow, exporting loose translated .paloc files and optional loose font files instead of full patched archives",
            "[Enhancement] Ship dialogs now let you choose between Mod Manager ZIP (small) and Standalone ZIP (full patched archives), keeping both distribution workflows available in one place",
            "[Enhancement] Manager ZIP packaging now targets current Crimson Desert loose-file manager workflows with files/ payloads, manifest metadata, game-build tagging, and reusable package metadata",
            "[Enhancement] Explorer mesh manager packages now include paired .pamlod loose files automatically when a .pam edit needs its matching LOD asset",

            # Mesh Preview / PAC Viewing
            "[Fix] OpenGL mesh preview upload now uses full buffer byte sizes for positions, normals, and indices, fixing cut or missing body parts caused by truncated GPU buffers",
            "[Fix] Explorer PAC preview now preserves and uses parsed file normals in both the OpenGL and fallback preview paths instead of rebuilding lighting normals incorrectly",
            "[Fix] PAC preview flattening now uses a safer selective preview path with valid fallback behavior, preventing broken partial renders on edge-case character meshes",
            "[Fix] PreviewPane initialization and mesh preview backend handling were stabilized so Explorer mesh preview starts reliably without renderer setup regressions",

            # PAM / Patch-to-Game Stability
            "[Fix] Full PAM rebuild now updates additional local geometry-size headers and hidden mirrored index-count/bounds blocks required by topology-changing static meshes",
            "[Fix] Import OBJ + Patch to Game for PAM meshes now imports the paired PAMLOD transfer helper correctly instead of skipping the LOD patch with a missing-name error",
            "[Fix] Repack state now refreshes in-memory PAMT entry offsets and sizes after patching so same-session preview reads the rebuilt file instead of stale archive offsets",
        ],
    ),
    (
        "1.12.0", "2026-04-04", [
            # Explorer / Mesh Ship to App
            "[Feature] Explorer Ship to App: selected .pac, .pam, and .pamlod meshes can now be packaged as standalone ZIP installers for end users",
            "[Feature] Mesh Ship builder: edited OBJ files now rebuild mesh binaries, patch PAZ/PAMT/PAPGT fully in memory, and generate install.bat, uninstall.bat, README.txt, and manifest.json",
            "[Feature] Explorer mesh context menu now includes 'Import OBJ + Ship to App' for direct one-asset packaging from the file browser",
            "[Enhancement] Explorer mesh shipping dialog now supports multi-asset packaging with per-asset OBJ assignment, reusable metadata fields, and paired .pamlod auto-generation for edited .pam meshes",
            "[Enhancement] Explorer now remembers the last imported OBJ per mesh during the session so packaging workflows can prefill the edited source path automatically",
            "[Enhancement] Explorer mesh Ship to App now resolves real in-game item names for default mod titles when an item mapping exists, such as weapon and armor names from game data",
            "[Fix] Explorer mesh Ship to App metadata fields are now fully editable so mod name, author, and version can be customized before packaging",
            "[Fix] Mesh distribution packages now always include the full enterprise-safe patched set: PAZ payload, package PAMT index, and meta PAPGT checksum root",

            # Translate / Version Tracking
            "[Feature] Translate tab now tracks text updates by exact game build using meta/0.paver + PAPGT CRC instead of only a coarse session fingerprint",
            "[Feature] Per-entry game history is now stored for baseline, added, changed, and removed text events, enabling version-aware filtering inside the translation table",
            "[Feature] Translate table now supports enterprise version filtering: filter entries by tracked game build and by change type (Added, Changed, Removed, Baseline)",
            "[Feature] Translate status bar now shows the latest text-sync build and update summary so new strings and source-text changes are visible immediately after game updates",
            "[Enhancement] Restore, project load, and source-language load now all sync against fresh live game text using the same version-aware merge pipeline",
            "[Enhancement] Game-update sync popups now show previous build, current build, changed text samples, and sample new/removed keys for faster review triage",
            "[Enhancement] Legacy autosave projects are now migrated into enterprise version tracking automatically: existing entries become the original baseline and current pending entries are grouped into the latest update bucket",
            "[Fix] Translation baselines now preserve the original first-seen text and only extend with newly discovered keys on later updates instead of overwriting the baseline snapshot each time",

            # Stability / Static Mesh / Preview
            "[Fix] DDS preview now safely rejects truncated bogus uncompressed decodes and falls back cleanly instead of crashing when browsing problematic files like 03_cube_sp.dds",
            "[Fix] Full PAM topology rebuild now also synchronizes mirrored header metadata blocks, preventing stale static-mesh counts that could cause in-game crashes after adding geometry",
        ],
    ),
    (
        "1.11.0", "2026-04-04", [
            # ── Explorer / Mesh Editing / Search ──
            "[Feature] Full PAC round-trip editing workflow now supports export, edit, add or delete geometry, re-import, and patch back to game for topology-changing meshes",
            "[Fix] PAC OBJ import now triangulates Blender quads and n-gons automatically instead of rejecting non-triangle exports",
            "[Fix] PAC import can now map renamed Blender objects back onto the original game submesh slots using geometry matching heuristics",
            "[Fix] Exact weapon rebuild path now supports topology-changing PAC edits and partial-submesh deletion while preserving archive integrity and checksum validation",
            "[Fix] Explorer item-name search now indexes live game item data so searching by in-game names like 'Vow of the Dead King' shows the correct related files immediately",
            "[Feature] Search history added across Explorer, Audio, and Translate: latest 10 searches persist across restarts, can be clicked to reuse, and each entry can be removed individually",
            "[Enhancement] Explorer 3D preview now uses the fast hardware-accelerated OpenGL viewer path for much smoother large-mesh rendering",
            "[Fix] OpenGL preview compatibility improved: uniform uploads now use PyOpenGL-safe ctypes buffers, fixing preview failures on rebuilt high-vertex PAC meshes",

            # ── Translate / Settings / Runtime ──
            "[Fix] Translate tab AI Provider dropdown now shows the full provider catalog, not only currently enabled providers, with disabled providers clearly labeled for enterprise visibility and control",
            "[Fix] Translate tab now blocks disabled providers with explicit guidance instead of failing silently, while still reading the latest saved model configuration",
            "[Fix] Settings changes now refresh the Translate tab immediately: provider list, selected model display, translation prompt state, and autosave behavior update as soon as settings are saved",
            "[Fix] Settings tab dark-theme white background bug resolved by giving settings pages, stacked panels, and scroll content explicit themed backgrounds",
            "[Enhancement] Standalone build now bundles the entire data directory for portable runtime configuration, language definitions, and future packaged resources",
            "[Fix] Bundled executable now resolves data resources through a dedicated runtime path layer, ensuring languages.json and default settings load correctly in both source and packaged builds",
            "[Fix] Legacy or partial configs now initialize the full AI provider registry consistently, preventing missing-provider states in enterprise settings and translation workflows",
            "[Fix] Clearing custom translation prompts now properly falls back to the built-in enterprise translation prompt instead of keeping stale simplified prompt state",
        ],
    ),
    (
        "1.10.0", "2026-04-02", [
            # ── Enterprise Audio Tab ──
            "[Feature] Enterprise Audio tab: browse, play, export, import, and TTS-generate 107K+ game voice files",
            "[Feature] Audio index engine: 94.9% of voice files auto-linked to paloc dialogue text in 14 languages",
            "[Feature] Voice language auto-detection: Korean (pkg 0005), English (pkg 0006), Japanese (pkg 0035)",
            "[Feature] Audio category filter: Quest Greeting, Quest Main, AI Friendly, AI Ambient, etc.",
            "[Feature] Click any audio file to see dialogue text in all 14 game languages",
            "[Feature] Search across all languages: find audio by English, Korean, Arabic, or any translated text",
            "[Feature] Auto-load translated text into TTS input based on selected language",
            "[Feature] Generated audio history with click-to-play, save, and clear",
            "[Feature] Audio export as WAV or OGG with WEM auto-decode via vgmstream",
            "[Feature] Audio import + Patch to Game with WAV-to-WEM Vorbis conversion via Wwise",
            "[Feature] Wwise auto-detection from WWISEROOT, Program Files, or PATH",
            "[Feature] ffmpeg auto-installer: downloads and installs on first use (~80MB)",

            # ── TTS (Text-to-Speech) ──
            "[Feature] Multi-provider TTS engine: OpenAI, ElevenLabs, Edge TTS (free), Google Cloud, Azure Speech, Mistral Voxtral",
            "[Feature] All TTS models and voices fetched dynamically from provider APIs (nothing hardcoded)",
            "[Feature] TTS providers share API keys with translation providers (OpenAI, Gemini, Mistral)",
            "[Feature] Edge TTS: free, 400+ voices, no API key needed (default provider)",
            "[Feature] Generate + Patch to Game: TTS generate, convert to WEM, write to archives in one click",
            "[Feature] Only enabled providers shown in Audio tab TTS dropdown",

            # ── DeepL Translation ──
            "[Feature] DeepL translation provider (10th provider): superior quality for European languages",
            "[Feature] DeepL free tier (500K chars/month) and Pro ($25/1M chars) support",
            "[Feature] DeepL formality control, context parameter, and glossary support",

            # ── Settings ──
            "[Feature] New Audio/TTS settings page with ElevenLabs and Azure Speech API keys",
            "[Feature] Per-provider Translation Model + TTS Model dropdowns (proper dropdown, not text box)",
            "[Feature] Load Models button fetches and populates Translation + TTS model lists with auto-select",

            # ── Translation Tab ──
            "[Feature] 7 new dialogue sub-categories: Quest Greeting, Quest Main, Quest Side Content, Quest Lines, AI Friendly, AI Ambient, AI Ambient (Group)",

            # ── Mesh Import/Export Fixes ──
            "[Fix] OBJ importer: vertices kept in sequential order (was scrambled by face-visit order)",
            "[Fix] OBJ importer: all vertices preserved including face-unreferenced ones",
            "[Fix] PAM builder: vertex positions patched in-place by pattern matching (100% pass rate)",
            "[Fix] PAC round-trip: 97% pass rate (28/29 tested files)",
            "[Fix] FBX binary writer: node end_offset now absolute — Blender opens exports correctly",

            # ── Stability ──
            "[Fix] App no longer crashes on modded or corrupt game files — decompression failures caught gracefully",
            "[Fix] Browse and preview works on patched game installs where other mod tools modified PAZ archives",
        ],
    ),
    (
        "1.9.0", "2026-04-01", [
            # ── Audio Tab (initial) ──
            "[Feature] Audio tab: browse, play, and export all game audio files (WEM, BNK, WAV, OGG)",
            "[Feature] Audio player with full transport controls in Audio tab",
            "[Feature] Export audio as WAV or OGG from Explorer and Audio tab context menus",
            "[Feature] Import WAV to replace game audio with one-click Patch to Game",
            "[Feature] WEM/BNK to WAV conversion via vgmstream-cli (auto-installed)",

            # ── TTS (initial) ──
            "[Feature] TTS providers: Edge TTS (free), OpenAI TTS, ElevenLabs, Google Cloud TTS, Azure Speech",
            "[Feature] TTS Generator panel: select provider, voice, language, speed",
            "[Feature] Replace + Patch to Game: generate TTS and write directly to game archives",

            # ── DeepL Translation ──
            "[Feature] DeepL translation provider with free tier (500K chars/month) and Pro support",
            "[Feature] DeepL formality control and context parameter for improved accuracy",

            # ── Stability ──
            "[Fix] Decompression failures on modded game files caught gracefully instead of crashing",
            "[Fix] Extract handles corrupt entries by writing raw data instead of crashing",
        ],
    ),
    (
        "1.8.0", "2026-04-01", [
            # ── Round-Trip Mesh Modding ──
            "[Feature] OBJ Import: load modified OBJ files back into the app for preview and patching",
            "[Feature] PAC Builder: rebuild PAC binary from modified mesh — quantizes positions, builds vertex records and index buffer",
            "[Feature] PAM Builder: rebuild PAM binary from modified mesh — preserves header, submesh table, and geometry layout",
            "[Feature] Import OBJ (replace mesh): right-click any .pac/.pam/.pamlod in Explorer to import a modified OBJ",
            "[Feature] Import OBJ + Patch to Game: one-click import, rebuild, compress, encrypt, and write to game archives",
            "[Feature] Full round-trip pipeline: Export OBJ \u2192 edit in Blender \u2192 Import OBJ \u2192 Patch to Game",
            "[Feature] OBJ export now embeds source_path and source_format comments for re-import identification",
            "[Fix] FBX binary writer: child node end_offset was relative to 0 instead of absolute file position — Blender now opens FBX files correctly",

            # ── PAC Mesh Parser (complete rewrite) ──
            "[Feature] PAC mesh parser fully reverse-engineered from binary analysis — correct geometry for all character meshes",
            "[Feature] PAC section layout auto-detected from section offset table inside section 0 — works for all format variants",
            "[Feature] PAC vertex data: uint16 quantized positions dequantized with per-submesh bounding box",
            "[Feature] PAC index buffer: triangle list format with per-submesh index counts per LOD level",
            "[Feature] PAC multi-LOD support: LOD0 (highest quality) automatically selected for preview and export",
            "[Feature] PAC multi-submesh support: sword blades, guards, handles, accessories parsed as separate objects",
            "[Feature] PAC bone index padding: odd bone counts padded to even byte boundary (fixes facial/head meshes)",
            "[Feature] PAC auto-detect vertex stride from section size — handles 36, 38, 40, 42+ byte strides",
            "[Feature] PAC idx_count validation: stops reading at garbage values to prevent buffer overruns",
            "[Feature] UV coordinates extracted from float16 values in vertex records",

            # ── Explorer Export Fixes ──
            "[Fix] Export context menu now uses right-clicked row instead of selected row — no more exporting wrong file",
            "[Fix] Export output filenames include full path (e.g. character_warrior_body.obj) — no more overwrites",
            "[Fix] Lambda closure in export menu binds entry by value — prevents stale reference issues",

            # ── Format Compatibility ──
            "[Feature] 3-LOD PAC files (cd_pgw_* heads, eyebrows) now parse correctly alongside 4-LOD files",
            "[Feature] Variable section size encoding handled: u64 pairs, consecutive u32s, and mixed layouts",
            "[Feature] Unsupported PAC variants (skinnedmesh_box v4.3) gracefully skip instead of showing errors",
        ],
    ),
    (
        "1.7.0", "2026-03-31", [
            # ── Localization Tracer ──
            "[Feature] Localization Tracer: standalone tool — type any text, instantly see every screen it appears on in-game",
            "[Feature] Tracer shows the full chain for each hit: which UI screen, which element, what CSS styling, what font and color",
            "[Feature] 182 game screens mapped to readable names (Character Select, Skill Tree, World Map, Alert Popup, etc.)",
            "[Feature] Three search modes: search by displayed text, by paloc key ID, or by UI binding name",
            "[Feature] When a string appears on multiple screens, all locations are listed with descriptions",
            "[Feature] All 170 CSS, 153 HTML, and 29 template files decrypted and indexed on startup",

            # ── Game UI System ──
            "[Feature] Full game UI system reverse-engineered: HTML/CSS-based with custom localstring binding to paloc entries",
            "[Feature] Per-language CSS files identified — each language has its own font rules and line-breaking behavior",
            "[Feature] Widget template system mapped: reusable KeyGuide, Modal, ItemTooltip components with text overrides",
            "[Feature] 115 UI text bindings cataloged (Save/Load, Exit, Confirm, Cancel, menu labels, skill names, shop titles, etc.)",
            "[Feature] Runtime template variables documented: keybind display, currency icons, clickable game-term links",

            # ── 3D Mesh ──
            "[Feature] Extract and preview all 12,724 skinned character meshes (.pac) from game archives",
            "[Feature] Extract and preview 50,388 static meshes (.pam) including props, terrain, and breakable objects",
            "[Feature] Extract and preview 32,188 LOD mesh variants (.pamlod) with multiple quality levels",
            "[Feature] Export any mesh to OBJ (Wavefront) or FBX (binary 7.4) from Explorer right-click menu",
            "[Feature] FBX export auto-finds and embeds the matching skeleton with full bone hierarchy",
            "[Feature] Mesh preview shows 3D render, vertex/face counts, submesh list, materials, and textures",
            "[Feature] Breakable and destructible object meshes now extract correctly",

            # ── Textures ──
            "[Feature] Preview all 279,515 DDS textures directly in Explorer — no external tools needed",
            "[Feature] Supports all game texture formats: color, normal maps, roughness, heightmaps, distance fields",
            "[Feature] Grayscale and terrain textures render as preview instead of showing an error",

            # ── Skeleton / Animation / Havok ──
            "[Feature] Extract skeleton data (.pab): bone names, parent hierarchy, bind poses, transforms",
            "[Feature] Extract animation data (.paa): keyframes, bone rotations, frame count, duration",
            "[Feature] Extract Havok data (.hkx): bone names, skeleton hierarchy, content type (skeleton/animation/physics/ragdoll)",
            "[Feature] Preview all skeleton, animation, and Havok files directly in Explorer",

            # ── File Support ──
            "[Feature] 108 game file extensions recognized with category, description, and preview/edit support",
        ],
    ),
    (
        "1.6.0", "2026-03-30", [
            "[Feature] OBJ export with materials, UVs, normals, and multi-submesh support",
            "[Feature] FBX binary 7.4 export compatible with Blender, Maya, 3ds Max, Unity, Unreal Engine",
            "[Feature] Right-click Export as OBJ / Export as FBX on any mesh file in Explorer",
            "[Feature] DDS texture header info: format name, resolution, mipmap count, alpha channel",
            "[Feature] Mesh preview in Explorer with static 3D render and geometry statistics",
            "[Feature] Split export option: save each submesh as a separate OBJ file",
            "[Feature] Custom scale factor for mesh export",
        ],
    ),
    (
        "1.5.0", "2026-03-30", [
            "[Feature] Ship to App: generate ZIP+BAT packages for end-user mod distribution",
            "[Feature] Ship to App: auto-discovers Steam game, copies pre-patched files, one-click install",
            "[Feature] Ship to App: built-in font donor system — select donor font, auto-adds missing glyphs for target language",
            "[Feature] Ship to App: uninstall via Steam Verify Integrity — clean and reliable restoration",
            "[Feature] Paloc parser now extracts 172K+ entries (both numeric and symbolic keys like questdialog_*, textdialog_*)",
            "[Feature] Dialogue and Documents categories now populated from symbolic keys (was empty before)",
            "[Feature] Auto-lock untranslatable entries (empty, PHM_, placeholder) — marked Approved and protected from editing",
            "[Feature] Locked status filter in translation table — view all auto-locked entries",
            "[Feature] Wildcard search: key:quest*, *dragon*, {*} for brace tokens, locked:yes, empty:yes",
            "[Feature] Game version read from meta/0.paver — shows real version (e.g. v1.01.02) in About tab",
            "[Feature] Status bar auto-reflects on app startup — badges populate immediately after restore",
            "[Feature] Always merge with fresh game data on startup — catches new entries, parser improvements, patches",
            "[Feature] Detailed game update popup with new/changed/removed counts and text samples",
            "[Feature] Arrow key navigation in Explorer tab now triggers preview (was mouse-only)",
            "[Enhancement] Comprehensive tooltips on every widget across all tabs (Translate, Explorer, Font, Settings, About)",
            "[Enhancement] Search supports field:value syntax, quoted phrases, glob wildcards, boolean operators",
            "[Enhancement] LZ4/ChaCha20 checksum computed via native DLL (754x faster than pure Python on large PAZ files)",
            "[Enhancement] Font Builder: GSUB/GPOS merge filters out lookups referencing missing glyphs — no more KeyError crashes",
            "[Enhancement] Font Builder: handles CJK fonts with coordinates > 16-bit by clamping bounding boxes",
            "[Enhancement] Ship to App BAT scripts use delayed expansion for paths with (x86) parentheses",
            "[Fix] Usage filter categories (Dialogue, Documents) were empty on Windows — now auto-discovered from all game groups",
            "[Fix] paloc_parser was discarding 55K+ symbolic key entries (questdialog_*, textdialog_*) — now extracted",
            "[Fix] Patch to Game duplicate popup and 1-3 minute freeze — O(n) duplicate apply, single confirmation dialog",
            "[Fix] QComboBox and QTextBrowser text invisible on Windows — explicit color in QSS for item pseudo-elements",
            "[Fix] checksum_file() was bypassing native DLL, falling back to slow pure Python — now routes through pa_checksum()",
        ],
    ),
    (
        "1.4.0", "2026-03-30", [
            "[Feature] Complete UI overhaul: modern Catppuccin-inspired theme with rounded corners, gradient progress bars, smooth hover states",
            "[Feature] New button variants: primary (blue), danger (red), success (green), warning (yellow) with proper hover/press/disabled states",
            "[Feature] Styled tool buttons with checked state for toggles (loop, mute)",
            "[Enhancement] Buttons now have 6px border-radius, 500 font-weight, proper focus rings",
            "[Enhancement] Tab bar redesigned: no borders, bottom-accent style, cleaner spacing",
            "[Enhancement] Table view: removed grid lines, increased row height (30px), cleaner cell padding",
            "[Enhancement] Context menus: rounded corners (8px), proper padding, separators",
            "[Enhancement] Scrollbars: transparent track, rounded handles, pressed state",
            "[Enhancement] Combobox dropdowns: rounded items, hover highlights, proper padding",
            "[Enhancement] Group boxes: 8px radius, blue title color, more padding",
            "[Enhancement] Search input: clear button enabled, better placeholder text",
            "[Enhancement] Slider controls: styled groove, rounded handle with hover-grow effect, filled sub-page",
            "[Enhancement] Progress bar: gradient fill (teal to blue), rounded shape",
            "[Enhancement] Translate tab: danger-styled Clear/Clear All buttons, success-styled Patch to Game, warning-styled Revert",
            "[Enhancement] Translate tab: proper vertical line separators replacing ugly '|' text labels",
            "[Enhancement] Translate tab: Stop button danger-styled, AI Selected primary-styled, Approve All success-styled",
            "[Enhancement] Filter bar: styled labels, fixed-width status combo, count label highlighted in blue",
            "[Enhancement] Light theme fully redesigned to match dark theme quality",
            "[Enhancement] Tooltips: rounded corners (6px), proper padding",
            "[Enhancement] Text browser (About/Changelog): styled with proper borders and selection colors",
        ],
    ),
    (
        "1.3.0", "2026-03-30", [
            "[Feature] Auto-install vgmstream: one-click download and install for Wwise audio playback (no manual setup needed)",
            "[Feature] Enhanced audio player: volume slider with mute toggle, loop playback, format info display",
            "[Feature] Audio player keyboard shortcuts: Space (play/pause), S (stop), M (mute), L (loop)",
            "[Feature] Video player now has full transport controls: play/pause/stop, seek, volume, mute, loop",
            "[Feature] Video controls properly connected to video player (was using separate audio-only player before)",
            "[Feature] Added Bink2 (.bk2/.bik) video format support - common in Crimson Desert cinematics",
            "[Feature] Added CriWare USM (.usm) video format support - used in game cutscenes",
            "[Feature] Added MKV, FLAC, AAC format detection and preview support",
            "[Feature] Magic byte detection for FLAC, Bink2, and CriWare USM formats",
            "[Enhancement] Audio preview shows centered format icon with file type label",
            "[Enhancement] Time display supports hours for long audio/video (H:MM:SS format)",
            "[Enhancement] vgmstream auto-installer shows download progress and retry on failure",
            "[Enhancement] Explorer file type filters updated with all new audio/video formats",
            "[Fix] Fixed preview_pane.py clear() method was incorrectly nested inside _html_escape function",
            "[Fix] Video player audio was not controllable - now properly wired to volume/mute controls",
        ],
    ),
    (
        "1.2.0", "2026-03-30", [
            "[Feature] Auto-load game on first run - no manual 'Load Game' click needed when Steam install is found",
            "[Feature] Game version display in status bar, translate tab stats row, and paloc info label (CRC fingerprint + modification date)",
            "[Feature] Auto-check for new game files, new package groups, and new language entries on every launch",
            "[Feature] Game update detection with notification banner showing what changed since last session",
            "[Feature] Centralized version system with full changelog in About tab",
            "[Enhancement] Enterprise context menu: shows selection count, Revert to Pending option, Clear Selected, Select All",
            "[Enhancement] Keyboard shortcut: Ctrl+A to select all visible rows in translation table",
            "[Enhancement] Keyboard shortcut: Delete key to clear selected translations and revert to Pending",
            "[Enhancement] Paste feedback: status bar shows 'Pasted to N entries' after paste operation",
            "[Enhancement] Batch status operations now emit proper signals for real-time stats updates",
            "[Fix] Entry editor (double-click) save now correctly persists translation text",
            "[Fix] Auto-transition Pending -> Translated when entering text in entry editor",
            "[Fix] Auto-revert to Pending when clearing translation text (both inline and editor dialog)",
            "[Fix] Real-time status combo auto-update as you type in entry editor",
            "[Fix] Paste to multiple selected rows: single copied line now applies to ALL selected rows",
            "[Fix] Stats bar updates immediately after save/paste/status change operations",
        ],
    ),
    (
        "1.1.0", "2026-03-28", [
            "[Feature] Full 'Patch to Game' pipeline: export, compress, encrypt, write PAZ, update PAMT+PAPGT checksum chain",
            "[Feature] Backup manager creates automatic backups before patching game files",
            "[Feature] Duplicate detection: finds identical original text across entries and offers batch-apply",
            "[Feature] Glossary manager for proper nouns - ensures consistent translation of names, places, factions",
            "[Feature] AI glossary injection: glossary terms are injected into every AI translation prompt",
            "[Feature] Baseline manager: immutable reference of original game text, survives game updates",
            "[Feature] Game update merge: detects new/removed/changed strings and preserves translations",
            "[Feature] Import/Export JSON for external editing with merge-by-key support",
            "[Feature] Export to TXT with tab-separated format for spreadsheet compatibility",
            "[Feature] Autosave manager with configurable interval (default 30s)",
            "[Feature] Session state recovery: restores project, UI selections, and scroll position on restart",
            "[Feature] Translation batch processor with pause/resume/stop controls",
            "[Feature] Localization usage index: tags strings by game context (dialogue, quest, UI, skills, etc.)",
            "[Enhancement] Usage category filter in translation table",
            "[Enhancement] Advanced search: field-specific queries (key:, original:, translation:, usage:, status:)",
            "[Enhancement] Ranked search results with weighted scoring across all fields",
            "[Enhancement] Review All / Approve All bulk operations with progress",
            "[Enhancement] Token and cost tracking per translation batch",
        ],
    ),
    (
        "1.0.0", "2026-03-19", [
            "[Feature] Initial release - Crimson Desert Modding Studio",
            "[Feature] Game auto-discovery: scans Steam libraries for Crimson Desert installation",
            "[Feature] VFS (Virtual File System) for reading game package archives",
            "[Feature] PAPGT root index parser with full checksum chain support",
            "[Feature] PAMT metadata parser for file entries within package groups",
            "[Feature] PAZ archive reader with ChaCha20 decryption and LZ4 decompression",
            "[Feature] Paloc localization file parser and builder",
            "[Feature] Explorer tab: browse, unpack, and inspect game resources",
            "[Feature] Repack tab: rebuild modified resources back into game archives",
            "[Feature] Translate tab: AI-powered and manual translation workspace",
            "[Feature] Font Builder tab: custom font generation for game text rendering",
            "[Feature] Settings tab: configure AI providers, models, and preferences",
            "[Feature] Multi-provider AI translation: OpenAI, Anthropic, Google, DeepSeek, local models",
            "[Feature] Translation table with virtual scrolling (100K+ entries)",
            "[Feature] Column sorting, copy/paste, and status management",
            "[Feature] Dark and Light theme support",
            "[Feature] 17 game languages auto-detected from paloc files",
            "[Feature] 70+ world languages available as translation targets",
        ],
    ),
]


def get_changelog_html() -> str:
    """Render the full changelog as styled HTML for the About tab."""
    tag_colors = {
        "Feature": "#a6e3a1",
        "Enhancement": "#89b4fa",
        "Fix": "#f9e2af",
        "Breaking": "#f38ba8",
        "Security": "#cba6f7",
        "Deprecated": "#fab387",
        "Removed": "#eba0ac",
        "Performance": "#94e2d5",
        # Work-in-progress + known-issue tags — call out partial
        # features and unresolved gaps directly in the changelog so
        # users don't assume a feature is production-ready when it
        # still has known blockers.
        "WIP": "#fab387",
        "Known Issue": "#f38ba8",
        "Community": "#94e2d5",
    }

    html_parts = []
    for version, date, changes in CHANGELOG:
        html_parts.append(
            f'<h3 style="margin-top:18px; margin-bottom:4px;">'
            f'v{version} &mdash; {date}</h3>'
        )
        html_parts.append('<ul style="margin-top:2px;">')
        for change in changes:
            # Parse [Tag] prefix for coloring
            display = change
            for tag, color in tag_colors.items():
                prefix = f"[{tag}]"
                if change.startswith(prefix):
                    rest = change[len(prefix):].strip()
                    display = (
                        f'<span style="color:{color}; font-weight:bold;">[{tag}]</span> '
                        f'{rest}'
                    )
                    break
            html_parts.append(f"<li>{display}</li>")
        html_parts.append("</ul>")

    return "\n".join(html_parts)
