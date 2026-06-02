"""Font builder engine for Crimson Desert modding.

Supports:
1. Extract game font from PAZ (LZ4 decompress, NO decryption)
2. Replace entire TTF with custom font
3. Add script glyphs from a donor font
4. Merge GSUB/GDEF/GPOS tables when a script needs shaping
5. Patch font back to PAZ with full checksum chain

CRITICAL from ReadMetoSeeCorrectWay.md:
- Fonts are LZ4 ONLY, NOT encrypted (no ChaCha20)
- sefont/ directory must NOT contain modified fonts
- Always update PAMT comp/orig sizes after font size change
"""

import io
import os
import copy
import struct
from dataclasses import dataclass, field
from typing import Optional, Callable

from fontTools.ttLib import TTFont
from fontTools.pens.recordingPen import DecomposingRecordingPen
from fontTools.pens.ttGlyphPen import TTGlyphPen

from core.pamt_parser import parse_pamt, PamtData, PamtFileEntry, update_pamt_paz_entry, update_pamt_file_entry, update_pamt_self_crc
from core.papgt_manager import parse_papgt, get_pamt_crc_offset, update_papgt_pamt_crc, update_papgt_self_crc
from core.paz_write_utils import build_space_map, write_entry_payload
from core.checksum_engine import pa_checksum, checksum_file
from core.compression_engine import decompress, compress
from core.backup_manager import BackupManager
from utils.platform_utils import get_file_timestamps, set_file_timestamps, atomic_write
from utils.logger import get_logger

logger = get_logger("core.font_builder")


@dataclass
class FontBuildResult:
    """Result of a font build + patch operation."""
    success: bool
    message: str
    original_size: int = 0
    new_size: int = 0
    glyphs_added: int = 0
    pua_glyphs_added: int = 0
    paz_crc: int = 0
    pamt_crc: int = 0
    papgt_crc: int = 0
    backup_dir: str = ""
    errors: list[str] = field(default_factory=list)


@dataclass
class FontInfo:
    """Information about a font file in the game archives."""
    filename: str
    path: str
    paz_file: str
    offset: int
    comp_size: int
    orig_size: int
    paz_index: int
    compression_type: int
    encrypted: bool
    group: str
    entry: PamtFileEntry


def find_game_fonts(packages_path: str, vfs=None) -> list[FontInfo]:
    """Scan all package groups for font files (.ttf, .otf).

    ``vfs`` (optional): when provided, this function reuses the
    PAMTs already cached on the VfsManager instead of re-parsing
    them from disk. Without it we cold-parse every group's PAMT
    again — on a shipping install that's ~10 seconds of redundant
    work at every Font Builder tab init.
    """
    fonts = []
    if vfs is not None:
        try:
            groups = vfs.list_package_groups()
        except Exception:
            groups = sorted(os.listdir(packages_path))
    else:
        groups = sorted(os.listdir(packages_path))

    for grp in groups:
        try:
            if vfs is not None:
                pamt = vfs.load_pamt(grp)  # cached: instant on warm calls
            else:
                pamt_path = os.path.join(packages_path, grp, "0.pamt")
                if not os.path.isfile(pamt_path):
                    continue
                pamt = parse_pamt(
                    pamt_path,
                    paz_dir=os.path.join(packages_path, grp),
                )
            for entry in pamt.file_entries:
                ext = os.path.splitext(entry.path.lower())[1]
                if ext in (".ttf", ".otf", ".woff", ".woff2"):
                    fonts.append(FontInfo(
                        filename=os.path.basename(entry.path),
                        path=entry.path,
                        paz_file=entry.paz_file,
                        offset=entry.offset,
                        comp_size=entry.comp_size,
                        orig_size=entry.orig_size,
                        paz_index=entry.paz_index,
                        compression_type=entry.compression_type,
                        encrypted=entry.encrypted,
                        group=grp,
                        entry=entry,
                    ))
        except Exception as e:
            logger.warning("Error scanning %s for fonts: %s", grp, e)
    return fonts


def extract_font(font_info: FontInfo) -> bytes:
    """Extract a font file from PAZ (decompress, NO decrypt for fonts)."""
    with open(font_info.paz_file, "rb") as f:
        f.seek(font_info.offset)
        data = f.read(font_info.comp_size)

    if font_info.compression_type == 2:
        data = decompress(data, font_info.orig_size, 2)

    return data


def load_ttfont(data: bytes) -> TTFont:
    """Load a TTFont from raw bytes."""
    return TTFont(io.BytesIO(data))


def save_ttfont(font: TTFont) -> bytes:
    """Save a TTFont to raw bytes.

    If compilation fails due to GSUB/GPOS referencing missing glyphs,
    strips the offending tables and retries — the font will still render
    correctly, it just loses some ligature/positioning rules.
    """
    buf = io.BytesIO()
    try:
        font.save(buf)
        return buf.getvalue()
    except (KeyError, struct.error, OverflowError) as e:
        logger.warning("Font save failed: %s — applying fixes and retrying", e)

        # Fix 1: Force recalcBBoxes to clamp glyph coordinates to valid range
        # CJK/Korean fonts can have coordinates > 65535 which overflow 'H' format
        if "glyf" in font:
            for glyph_name in font.getGlyphOrder():
                try:
                    g = font["glyf"][glyph_name]
                    if hasattr(g, "xMin"):
                        g.xMin = max(-32768, min(32767, g.xMin))
                        g.yMin = max(-32768, min(32767, g.yMin))
                        g.xMax = max(-32768, min(32767, g.xMax))
                        g.yMax = max(-32768, min(32767, g.yMax))
                except Exception:
                    pass

        # Fix 2: Strip broken GSUB/GPOS/GDEF tables
        for table_tag in ("GSUB", "GPOS", "GDEF"):
            if table_tag in font:
                try:
                    font[table_tag].compile(font)
                except Exception:
                    logger.warning("Removing broken %s table from font", table_tag)
                    del font[table_tag]

        buf = io.BytesIO()
        font.save(buf)
        return buf.getvalue()


def get_font_stats(font: TTFont) -> dict:
    """Get statistics about a font including per-script coverage."""
    from core.script_ranges import detect_font_scripts, SCRIPT_REGISTRY
    cmap = font.getBestCmap()
    glyph_order = font.getGlyphOrder()
    pua = [cp for cp in cmap if 0xE000 <= cp <= 0xF8FF]
    scripts = detect_font_scripts(cmap)
    gsub_scripts = []
    if "GSUB" in font:
        for sr in font["GSUB"].table.ScriptList.ScriptRecord:
            gsub_scripts.append(sr.ScriptTag)
    return {
        "total_glyphs": len(glyph_order),
        "cmap_entries": len(cmap),
        "scripts": scripts,
        "pua_glyphs": len(pua),
        "gsub_scripts": gsub_scripts,
        "units_per_em": font["head"].unitsPerEm,
    }


def add_script_glyphs(
    target_font: TTFont,
    donor_font: TTFont,
    script_name: str,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> dict:
    """Add glyphs for a specific script from a donor font.

    Copies glyphs at their original codepoints and merges GSUB/GDEF/GPOS
    when the script needs shaping.

    Args:
        target_font: The game font to modify.
        donor_font: Font to copy glyphs from (e.g. NotoSans for the target script).
        script_name: Script name from SCRIPT_REGISTRY (e.g. "Cyrillic").
        progress_callback: callback(done, total, message).

    Returns dict with stats.
    """
    from core.script_ranges import SCRIPT_REGISTRY
    script_info = SCRIPT_REGISTRY.get(script_name)
    if not script_info:
        raise ValueError(f"Unknown script: {script_name}. Available: {list(SCRIPT_REGISTRY.keys())}")

    return _add_glyphs_direct(target_font, donor_font, script_info, progress_callback)


def _add_glyphs_direct(
    target_font: TTFont,
    donor_font: TTFont,
    script_info,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> dict:
    """Copy glyphs from donor font at their original codepoints."""
    target_cmap = target_font.getBestCmap()
    donor_cmap = donor_font.getBestCmap()
    target_go = list(target_font.getGlyphOrder())
    target_glyf = target_font["glyf"]
    target_hmtx = target_font["hmtx"]
    donor_glyf = donor_font["glyf"]
    donor_hmtx = donor_font["hmtx"]

    target_upm = target_font["head"].unitsPerEm
    donor_upm = donor_font["head"].unitsPerEm
    scale = target_upm / donor_upm if target_upm != donor_upm else 1.0

    codepoints_to_add = []
    for start, end in script_info.ranges:
        for cp in range(start, end + 1):
            if cp in donor_cmap and cp not in target_cmap:
                codepoints_to_add.append(cp)

    stats = {"glyphs_added": 0, "codepoints_mapped": 0, "errors": []}
    total = len(codepoints_to_add)
    glyph_rename_map = {}  # donor_name → target_name for GSUB/GPOS remapping

    for idx, cp in enumerate(codepoints_to_add):
        donor_name = donor_cmap[cp]
        new_name = f"uni{cp:04X}"
        if new_name in target_go:
            new_name = f"u{cp:05X}"
        glyph_rename_map[donor_name] = new_name

        try:
            if donor_name not in donor_glyf:
                continue
            src = donor_glyf[donor_name]
            if src.numberOfContours == 0 and not src.isComposite():
                pen = TTGlyphPen(None)
                target_glyf[new_name] = pen.glyph()
            else:
                rec_pen = DecomposingRecordingPen(donor_font.getGlyphSet())
                donor_font.getGlyphSet()[donor_name].draw(rec_pen)
                pen = TTGlyphPen(None)
                if scale != 1.0:
                    for op, args in rec_pen.value:
                        if op == "moveTo":
                            pen.moveTo(*[(int(x * scale), int(y * scale)) for x, y in args])
                        elif op == "lineTo":
                            pen.lineTo(*[(int(x * scale), int(y * scale)) for x, y in args])
                        elif op == "qCurveTo":
                            pen.qCurveTo(*[(int(x * scale), int(y * scale)) for x, y in args])
                        elif op == "closePath":
                            pen.closePath()
                        elif op == "endPath":
                            pen.endPath()
                else:
                    rec_pen.replay(pen)
                target_glyf[new_name] = pen.glyph()

            if donor_name in donor_hmtx.metrics:
                w, lsb = donor_hmtx.metrics[donor_name]
                target_hmtx.metrics[new_name] = (int(w * scale), int(lsb * scale))
            else:
                target_hmtx.metrics[new_name] = (600, 0)

            target_go.append(new_name)
            for table in target_font["cmap"].tables:
                if not hasattr(table, "cmap"):
                    continue
                if table.format == 0 and cp > 255:
                    continue
                table.cmap[cp] = new_name
            stats["glyphs_added"] += 1
            stats["codepoints_mapped"] += 1
        except Exception as e:
            stats["errors"].append(f"U+{cp:04X}: {e}")

        if progress_callback and (idx + 1) % 50 == 0:
            progress_callback(idx + 1, total, f"Copying glyphs: {idx + 1}/{total}")

    target_font.setGlyphOrder(target_go)
    target_font["maxp"].numGlyphs = len(target_go)

    # Copy extra glyphs referenced by GSUB/GPOS (ligature products, mark combos, etc.)
    if script_info.needs_gsub:
        extra = _collect_gsub_gpos_referenced_glyphs(donor_font)
        target_go = list(target_font.getGlyphOrder())
        target_go_set = set(target_go)
        extra_needed = [g for g in extra if g not in target_go_set and g not in glyph_rename_map.values()]
        extra_added = []
        for glyph_name in extra_needed:
            if glyph_name not in donor_glyf:
                continue
            try:
                src = donor_glyf[glyph_name]
                if src.numberOfContours == 0 and not src.isComposite():
                    pen = TTGlyphPen(None)
                    target_glyf[glyph_name] = pen.glyph()
                else:
                    rec_pen = DecomposingRecordingPen(donor_font.getGlyphSet())
                    donor_font.getGlyphSet()[glyph_name].draw(rec_pen)
                    pen = TTGlyphPen(None)
                    if scale != 1.0:
                        for op, args in rec_pen.value:
                            if op == "moveTo":
                                pen.moveTo(*[(int(x * scale), int(y * scale)) for x, y in args])
                            elif op == "lineTo":
                                pen.lineTo(*[(int(x * scale), int(y * scale)) for x, y in args])
                            elif op == "qCurveTo":
                                pen.qCurveTo(*[(int(x * scale), int(y * scale)) for x, y in args])
                            elif op == "closePath":
                                pen.closePath()
                            elif op == "endPath":
                                pen.endPath()
                    else:
                        rec_pen.replay(pen)
                    target_glyf[glyph_name] = pen.glyph()
                if glyph_name in donor_hmtx.metrics:
                    w, lsb = donor_hmtx.metrics[glyph_name]
                    target_hmtx.metrics[glyph_name] = (int(w * scale), int(lsb * scale))
                else:
                    target_hmtx.metrics[glyph_name] = (600, 0)
                extra_added.append(glyph_name)
            except Exception:
                pass
        if extra_added:
            target_go.extend(extra_added)
            target_font.setGlyphOrder(target_go)
            target_font["maxp"].numGlyphs = len(target_go)
            logger.info("Copied %d extra glyphs for GSUB/GPOS references", len(extra_added))

    if script_info.needs_gsub:
        if "GSUB" in donor_font:
            try:
                if "GSUB" not in target_font:
                    _create_gsub_from_donor(target_font, donor_font, glyph_rename_map)
                else:
                    _merge_gsub(target_font, donor_font, glyph_rename_map)
            except Exception as e:
                stats["errors"].append(f"GSUB merge: {e}")
        if "GDEF" in donor_font:
            try:
                if "GDEF" not in target_font:
                    _create_gdef_from_donor(target_font, donor_font, glyph_rename_map)
                else:
                    _merge_gdef(target_font, donor_font, glyph_rename_map)
            except Exception as e:
                stats["errors"].append(f"GDEF merge: {e}")
        if "GPOS" in donor_font:
            try:
                if "GPOS" not in target_font:
                    _create_gpos_from_donor(target_font, donor_font, glyph_rename_map)
                else:
                    _merge_gpos(target_font, donor_font, glyph_rename_map)
            except Exception as e:
                stats["errors"].append(f"GPOS merge: {e}")

    if progress_callback:
        progress_callback(total, total, f"Added {stats['glyphs_added']} glyphs")

    return stats


def _collect_gsub_gpos_referenced_glyphs(font):
    """Collect all glyph names referenced by GSUB and GPOS tables."""
    glyphs = set()
    for tag in ("GSUB", "GPOS"):
        if tag not in font:
            continue
        table = font[tag].table
        for lookup in table.LookupList.Lookup:
            try:
                for subtable in lookup.SubTable:
                    for attr in ("Coverage", "BaseCoverage", "MarkCoverage", "LigatureCoverage"):
                        cov = getattr(subtable, attr, None)
                        if cov and hasattr(cov, "glyphs"):
                            glyphs.update(cov.glyphs)
                    for attr in ("BacktrackCoverage", "LookAheadCoverage", "InputCoverage"):
                        covs = getattr(subtable, attr, None)
                        if covs:
                            for c in covs:
                                if c and hasattr(c, "glyphs"):
                                    glyphs.update(c.glyphs)
                    if hasattr(subtable, "mapping"):
                        for k, v in subtable.mapping.items():
                            glyphs.add(k)
                            if isinstance(v, list):
                                glyphs.update(v)
                            else:
                                glyphs.add(v)
                    if hasattr(subtable, "alternates"):
                        for k, v in subtable.alternates.items():
                            glyphs.add(k)
                            glyphs.update(v)
                    if hasattr(subtable, "ligatures"):
                        for glyph, ligs in subtable.ligatures.items():
                            glyphs.add(glyph)
                            for lig in ligs:
                                glyphs.update(lig.Component)
                                glyphs.add(lig.LigGlyph)
            except Exception:
                pass
    return glyphs


def _remap_coverage(cov, rename_map):
    """Remap glyph names in a Coverage object or list of Coverage objects."""
    if cov is None:
        return
    if isinstance(cov, list):
        for c in cov:
            _remap_coverage(c, rename_map)
    elif hasattr(cov, "glyphs"):
        cov.glyphs = [rename_map.get(g, g) for g in cov.glyphs]


def _remap_glyph_names_in_lookup(lookup, rename_map):
    """Remap glyph names in a GSUB/GPOS lookup according to rename_map."""
    for subtable in lookup.SubTable:
        # All coverage-type attributes
        for attr in ("Coverage", "BacktrackCoverage", "LookAheadCoverage",
                     "InputCoverage", "BaseCoverage", "MarkCoverage", "LigatureCoverage"):
            _remap_coverage(getattr(subtable, attr, None), rename_map)
        # Substitution mappings (SingleSubst, MultipleSubst, etc.)
        if hasattr(subtable, "mapping"):
            new_mapping = {}
            for k, v in subtable.mapping.items():
                new_k = rename_map.get(k, k)
                if isinstance(v, list):
                    new_v = [rename_map.get(g, g) for g in v]
                else:
                    new_v = rename_map.get(v, v)
                new_mapping[new_k] = new_v
            subtable.mapping = new_mapping
        # Alternates (AlternateSubst)
        if hasattr(subtable, "alternates"):
            subtable.alternates = {
                rename_map.get(k, k): [rename_map.get(g, g) for g in v]
                for k, v in subtable.alternates.items()
            }
        # Ligature substitution
        if hasattr(subtable, "ligatures"):
            new_ligs = {}
            for glyph, lig_list in subtable.ligatures.items():
                new_key = rename_map.get(glyph, glyph)
                for lig in lig_list:
                    lig.Component = [rename_map.get(g, g) for g in lig.Component]
                    lig.LigGlyph = rename_map.get(lig.LigGlyph, lig.LigGlyph)
                new_ligs[new_key] = lig_list
            subtable.ligatures = new_ligs
        # PairPos (GPOS)
        if hasattr(subtable, "PairSet"):
            pass  # PairSet uses Coverage for first glyph, already handled


def _lookup_references_missing_glyphs(lookup, glyph_order_set):
    """Check if a GSUB/GPOS lookup references glyphs not in the target font."""
    try:
        for subtable in lookup.SubTable:
            # All Coverage-based attributes
            for attr in ("Coverage", "BaseCoverage", "MarkCoverage", "LigatureCoverage",
                         "BacktrackCoverage", "LookAheadCoverage", "InputCoverage"):
                cov = getattr(subtable, attr, None)
                if cov is None:
                    continue
                # Can be a single Coverage or a list of Coverages
                covs = cov if isinstance(cov, list) else [cov]
                for c in covs:
                    if c and hasattr(c, "glyphs"):
                        for glyph in c.glyphs:
                            if glyph not in glyph_order_set:
                                return True
            # Substitution mappings
            if hasattr(subtable, "mapping"):
                for k, v in subtable.mapping.items():
                    if k not in glyph_order_set:
                        return True
                    if isinstance(v, list):
                        for g in v:
                            if g not in glyph_order_set:
                                return True
                    elif v not in glyph_order_set:
                        return True
            # Alternates
            if hasattr(subtable, "alternates"):
                for k, v in subtable.alternates.items():
                    if k not in glyph_order_set:
                        return True
                    for g in v:
                        if g not in glyph_order_set:
                            return True
            # Ligatures
            if hasattr(subtable, "ligatures"):
                for glyph, ligs in subtable.ligatures.items():
                    if glyph not in glyph_order_set:
                        return True
                    for lig in ligs:
                        for g in lig.Component:
                            if g not in glyph_order_set:
                                return True
                        if lig.LigGlyph not in glyph_order_set:
                            return True
    except Exception:
        return True
    return False


def _filter_and_remap_lookups(donor_table, target_go_set, rename_map):
    """Deep-copy donor lookups, remap glyph names, filter out invalid ones.

    Returns (valid_lookups, lookup_index_map).
    """
    valid_lookups = []
    lookup_map = {}
    for i, lookup in enumerate(donor_table.LookupList.Lookup):
        new_lookup = copy.deepcopy(lookup)
        _remap_glyph_names_in_lookup(new_lookup, rename_map)
        if _lookup_references_missing_glyphs(new_lookup, target_go_set):
            continue
        lookup_map[i] = len(valid_lookups)
        valid_lookups.append(new_lookup)
    return valid_lookups, lookup_map


def _remap_features(donor_table, lookup_map):
    """Deep-copy and remap feature records. Returns (features, feature_map)."""
    features = []
    feature_map = {}
    for i, fr in enumerate(donor_table.FeatureList.FeatureRecord):
        new_fr = copy.deepcopy(fr)
        new_fr.Feature.LookupListIndex = [
            lookup_map[li] for li in fr.Feature.LookupListIndex if li in lookup_map
        ]
        if new_fr.Feature.LookupListIndex:
            feature_map[i] = len(features)
            features.append(new_fr)
    return features, feature_map


def _remap_scripts(donor_table, feature_map):
    """Deep-copy and remap script records."""
    scripts = []
    for sr in donor_table.ScriptList.ScriptRecord:
        new_script = copy.deepcopy(sr)
        if new_script.Script.DefaultLangSys:
            new_script.Script.DefaultLangSys.FeatureIndex = [
                feature_map[fi] for fi in new_script.Script.DefaultLangSys.FeatureIndex if fi in feature_map
            ]
        for lsr in getattr(new_script.Script, "LangSysRecord", []):
            lsr.LangSys.FeatureIndex = [
                feature_map[fi] for fi in lsr.LangSys.FeatureIndex if fi in feature_map
            ]
        scripts.append(new_script)
    return scripts


def _create_gsub_from_donor(target_font, donor_font, rename_map):
    """Create a GSUB table in target from donor when target has none."""
    from fontTools.ttLib.tables import otTables
    donor_gsub = donor_font["GSUB"].table
    target_go_set = set(target_font.getGlyphOrder())

    lookups, lookup_map = _filter_and_remap_lookups(donor_gsub, target_go_set, rename_map)
    if not lookups:
        return
    features, feature_map = _remap_features(donor_gsub, lookup_map)
    scripts = _remap_scripts(donor_gsub, feature_map)

    new_gsub = copy.deepcopy(donor_gsub)
    new_gsub.LookupList.Lookup = lookups
    new_gsub.LookupList.LookupCount = len(lookups)
    new_gsub.FeatureList.FeatureRecord = features
    new_gsub.FeatureList.FeatureCount = len(features)
    new_gsub.ScriptList.ScriptRecord = scripts
    new_gsub.ScriptList.ScriptCount = len(scripts)

    from fontTools.ttLib import newTable
    gsub_table = newTable("GSUB")
    gsub_table.table = new_gsub
    target_font["GSUB"] = gsub_table
    logger.info("Created GSUB table with %d lookups from donor", len(lookups))


def _create_gdef_from_donor(target_font, donor_font, rename_map):
    """Create a GDEF table in target from donor when target has none."""
    donor_gdef = donor_font["GDEF"].table
    target_go_set = set(target_font.getGlyphOrder())

    new_gdef = copy.deepcopy(donor_gdef)
    if new_gdef.GlyphClassDef:
        new_defs = {}
        for glyph, cls in new_gdef.GlyphClassDef.classDefs.items():
            mapped = rename_map.get(glyph, glyph)
            if mapped in target_go_set:
                new_defs[mapped] = cls
        new_gdef.GlyphClassDef.classDefs = new_defs

    from fontTools.ttLib import newTable
    gdef_table = newTable("GDEF")
    gdef_table.table = new_gdef
    target_font["GDEF"] = gdef_table
    logger.info("Created GDEF table from donor")


def _create_gpos_from_donor(target_font, donor_font, rename_map):
    """Create a GPOS table in target from donor when target has none."""
    donor_gpos = donor_font["GPOS"].table
    target_go_set = set(target_font.getGlyphOrder())

    lookups, lookup_map = _filter_and_remap_lookups(donor_gpos, target_go_set, rename_map)
    if not lookups:
        return
    features, feature_map = _remap_features(donor_gpos, lookup_map)
    scripts = _remap_scripts(donor_gpos, feature_map)

    new_gpos = copy.deepcopy(donor_gpos)
    new_gpos.LookupList.Lookup = lookups
    new_gpos.LookupList.LookupCount = len(lookups)
    new_gpos.FeatureList.FeatureRecord = features
    new_gpos.FeatureList.FeatureCount = len(features)
    new_gpos.ScriptList.ScriptRecord = scripts
    new_gpos.ScriptList.ScriptCount = len(scripts)

    from fontTools.ttLib import newTable
    gpos_table = newTable("GPOS")
    gpos_table.table = new_gpos
    target_font["GPOS"] = gpos_table
    logger.info("Created GPOS table with %d lookups from donor", len(lookups))


def _merge_gsub(target_font, donor_font, rename_map=None):
    """Merge GSUB features from a donor font.

    Remaps glyph names per rename_map, then copies lookups whose glyphs
    all exist in the target font.
    """
    if rename_map is None:
        rename_map = {}
    target_gsub = target_font["GSUB"].table
    donor_gsub = donor_font["GSUB"].table
    target_go_set = set(target_font.getGlyphOrder())
    existing_count = len(target_gsub.LookupList.Lookup)

    lookups, raw_map = _filter_and_remap_lookups(donor_gsub, target_go_set, rename_map)
    lookup_map = {k: v + existing_count for k, v in raw_map.items()}
    target_gsub.LookupList.Lookup.extend(lookups)

    existing_features = len(target_gsub.FeatureList.FeatureRecord)
    features, raw_fmap = _remap_features(donor_gsub, lookup_map)
    feature_map = {k: v + existing_features for k, v in raw_fmap.items()}
    target_gsub.FeatureList.FeatureRecord.extend(features)

    scripts = _remap_scripts(donor_gsub, feature_map)
    target_gsub.ScriptList.ScriptRecord.extend(scripts)

    target_gsub.LookupList.LookupCount = len(target_gsub.LookupList.Lookup)
    target_gsub.FeatureList.FeatureCount = len(target_gsub.FeatureList.FeatureRecord)
    target_gsub.ScriptList.ScriptCount = len(target_gsub.ScriptList.ScriptRecord)


def _merge_gdef(target_font, donor_font, rename_map=None):
    """Merge glyph class definitions from a donor font."""
    if rename_map is None:
        rename_map = {}
    target_gdef = target_font["GDEF"].table
    donor_gdef = donor_font["GDEF"].table
    target_go_set = set(target_font.getGlyphOrder())
    if donor_gdef.GlyphClassDef and target_gdef.GlyphClassDef:
        for glyph, cls in donor_gdef.GlyphClassDef.classDefs.items():
            mapped = rename_map.get(glyph, glyph)
            if mapped in target_go_set and mapped not in target_gdef.GlyphClassDef.classDefs:
                target_gdef.GlyphClassDef.classDefs[mapped] = cls


def _merge_gpos(target_font, donor_font, rename_map=None):
    """Merge GPOS features from a donor font.

    Remaps glyph names and skips lookups referencing missing glyphs.
    """
    if rename_map is None:
        rename_map = {}
    target_gpos = target_font["GPOS"].table
    donor_gpos = donor_font["GPOS"].table
    target_go_set = set(target_font.getGlyphOrder())
    existing_count = len(target_gpos.LookupList.Lookup)

    lookups, raw_map = _filter_and_remap_lookups(donor_gpos, target_go_set, rename_map)
    lookup_map = {k: v + existing_count for k, v in raw_map.items()}
    target_gpos.LookupList.Lookup.extend(lookups)

    existing_features = len(target_gpos.FeatureList.FeatureRecord)
    features, raw_fmap = _remap_features(donor_gpos, lookup_map)
    feature_map = {k: v + existing_features for k, v in raw_fmap.items()}
    target_gpos.FeatureList.FeatureRecord.extend(features)

    scripts = _remap_scripts(donor_gpos, feature_map)
    target_gpos.ScriptList.ScriptRecord.extend(scripts)

    target_gpos.LookupList.LookupCount = len(target_gpos.LookupList.Lookup)
    target_gpos.FeatureList.FeatureCount = len(target_gpos.FeatureList.FeatureRecord)
    target_gpos.ScriptList.ScriptCount = len(target_gpos.ScriptList.ScriptRecord)


def patch_font_to_game(
    font_data: bytes,
    font_info: FontInfo,
    packages_path: str,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> FontBuildResult:
    """Patch a modified font back into the game with full checksum chain.

    CRITICAL: Fonts are LZ4 only, NOT encrypted.

    Args:
        font_data: Raw TTF bytes of the modified font.
        font_info: Original font metadata from PAMT.
        packages_path: Path to the game packages/ directory.
        progress_callback: callback(step, total, message).
    """
    TOTAL = 8
    result = FontBuildResult(success=False, message="", original_size=font_info.orig_size, new_size=len(font_data))

    def step(n, msg):
        if progress_callback:
            progress_callback(n, TOTAL, msg)

    try:
        group = font_info.group
        group_dir = os.path.join(packages_path, group)
        pamt_path = os.path.join(group_dir, "0.pamt")
        papgt_path = os.path.join(packages_path, "meta", "0.papgt")

        fresh_pamt = parse_pamt(pamt_path, paz_dir=group_dir)
        entry = None
        for fe in fresh_pamt.file_entries:
            if fe.path == font_info.path and fe.paz_index == font_info.paz_index:
                entry = fe
                break
        if not entry:
            result.message = f"Font entry not found in PAMT: {font_info.path}"
            return result

        step(1, "Compressing font with LZ4 (no encryption)...")
        compressed = compress(font_data, 2)
        new_comp_size = len(compressed)
        new_orig_size = len(font_data)

        step(2, "Creating backup...")
        paz_path = entry.paz_file
        backup_dir = os.path.join(packages_path, "..", "crimsonforge_backups")
        bm = BackupManager(backup_dir)
        backup = bm.create_backup(
            [paz_path, pamt_path, papgt_path],
            description=f"Font patch: {font_info.filename}",
        )
        result.backup_dir = backup.backup_dir

        step(3, "Writing to PAZ archive...")
        space_map = build_space_map(fresh_pamt.file_entries)
        new_offset, _ = write_entry_payload(entry, compressed, space_map)

        step(4, "Computing PAZ checksum...")
        new_paz_crc = checksum_file(paz_path)
        new_paz_size = os.path.getsize(paz_path)
        result.paz_crc = new_paz_crc

        step(5, "Updating PAMT index...")
        pamt_raw = bytearray(fresh_pamt.raw_data)
        for te in fresh_pamt.paz_table:
            if te.index == entry.paz_index:
                update_pamt_paz_entry(pamt_raw, te, new_paz_crc, new_paz_size)
                break
        for fe in fresh_pamt.file_entries:
            if fe.offset == entry.offset and fe.paz_index == entry.paz_index:
                update_pamt_file_entry(
                    pamt_raw,
                    fe,
                    new_comp_size,
                    new_orig_size,
                    new_offset=new_offset,
                )
                break
        new_pamt_crc = update_pamt_self_crc(pamt_raw)
        result.pamt_crc = new_pamt_crc

        ts_pamt = get_file_timestamps(pamt_path)
        atomic_write(pamt_path, bytes(pamt_raw))
        set_file_timestamps(pamt_path, ts_pamt["modified"], ts_pamt["accessed"])

        step(6, "Updating PAPGT root index...")
        papgt_data = parse_papgt(papgt_path)
        papgt_raw = bytearray(papgt_data.raw_data)
        folder_number = int(group)
        pamt_crc_offset = get_pamt_crc_offset(papgt_data, folder_number)
        update_papgt_pamt_crc(papgt_raw, pamt_crc_offset, new_pamt_crc)
        new_papgt_crc = update_papgt_self_crc(papgt_raw)
        result.papgt_crc = new_papgt_crc

        ts_papgt = get_file_timestamps(papgt_path)
        atomic_write(papgt_path, bytes(papgt_raw))
        set_file_timestamps(papgt_path, ts_papgt["modified"], ts_papgt["accessed"])

        step(7, "Verifying checksums...")
        from core.checksum_engine import verify_papgt_checksum, verify_pamt_checksum
        ok_papgt, _, _ = verify_papgt_checksum(papgt_path)
        ok_pamt, _, _ = verify_pamt_checksum(pamt_path)
        if not ok_papgt or not ok_pamt:
            result.message = "Checksum verification failed after font patch."
            return result

        step(8, "Font patched successfully!")
        result.success = True
        result.message = (
            f"Font patched: {font_info.filename}\n"
            f"Size: {font_info.orig_size:,} -> {new_orig_size:,} bytes\n"
            f"Compressed: {font_info.comp_size:,} -> {new_comp_size:,} bytes"
        )
        return result

    except Exception as e:
        result.message = str(e)
        result.errors.append(str(e))
        return result
