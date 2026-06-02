"""Font Builder tab - language-driven font modification and patching.

Flow:
1. Select destination language (e.g. German, Korean)
2. Auto-detects which script is needed (Latin, Hangul...)
3. Shows what the game font HAS vs what it NEEDS for that language
4. Select donor font (e.g. NotoSans, NotoSansCJK)
5. Click "Add Glyphs" to copy missing glyphs from donor
6. Preview shows ONLY the target language characters
7. "Patch Font to Game" writes it with full checksum chain

Per ReadMetoSeeCorrectWay.md:
- Fonts are LZ4 only, NOT encrypted
- sefont/eng.ttf must be deleted
"""

import os
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QGroupBox, QTextEdit, QSplitter,
)
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtCore import Qt

from core.font_builder import (
    find_game_fonts, extract_font, load_ttfont, save_ttfont,
    get_font_stats, add_script_glyphs, patch_font_to_game,
    FontInfo,
)
from core.script_ranges import (
    get_script_for_lang, SCRIPT_REGISTRY, LANG_TO_SCRIPT,
)
from core.vfs_manager import VfsManager
from translation.language_config import LanguageConfig
from ui.widgets.progress_widget import ProgressWidget
from ui.dialogs.confirmation import show_error, show_info, confirm_action
from ui.dialogs.file_picker import pick_file, pick_save_file
from ui.dialogs.patch_progress import PatchProgressDialog
from utils.thread_worker import FunctionWorker
from utils.platform_utils import format_file_size
from utils.logger import get_logger

logger = get_logger("ui.tab_font")


class FontTab(QWidget):
    """Font Builder tab driven by destination language."""

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self._config = config
        self._vfs: VfsManager = None
        self._packages_path = ""
        self._game_fonts: list[FontInfo] = []
        self._current_font_data: bytes = b""
        self._modified_font_data: bytes = b""
        self._worker: FunctionWorker = None
        self._donor_path = ""
        self._lang_config = LanguageConfig()
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        top = QHBoxLayout()

        font_group = QGroupBox("Game Font")
        fg_layout = QVBoxLayout(font_group)
        self._font_combo = QComboBox()
        self._font_combo.setToolTip("Select a font from the game's archives to extract, analyze, or modify.")
        self._font_combo.currentIndexChanged.connect(self._on_font_selected)
        fg_layout.addWidget(self._font_combo)

        extract_row = QHBoxLayout()
        self._extract_btn = QPushButton("Extract")
        self._extract_btn.setToolTip("Extract the selected font from game archives into memory for analysis and editing.")
        self._extract_btn.clicked.connect(self._extract_current_font)
        extract_row.addWidget(self._extract_btn)
        self._save_btn = QPushButton("Save TTF...")
        self._save_btn.setToolTip("Save the extracted or modified font as a .ttf file on disk.")
        self._save_btn.clicked.connect(self._save_font_to_disk)
        extract_row.addWidget(self._save_btn)
        self._replace_btn = QPushButton("Replace TTF...")
        self._replace_btn.setToolTip("Replace the entire game font with a custom .ttf file from your computer.")
        self._replace_btn.clicked.connect(self._replace_font)
        extract_row.addWidget(self._replace_btn)
        fg_layout.addLayout(extract_row)
        top.addWidget(font_group)

        lang_group = QGroupBox("Destination Language")
        lg_layout = QVBoxLayout(lang_group)
        self._lang_combo = QComboBox()
        self._lang_combo.setToolTip(
            "Select the language you are translating into.\n"
            "This determines which Unicode character set (glyphs) the font needs to support."
        )
        all_langs = self._lang_config.get_all_languages()
        for lang in all_langs:
            if lang.script == "Arabic" and lang.code != "ar":
                continue
            script_name = LANG_TO_SCRIPT.get(lang.code, lang.script or "Latin")
            self._lang_combo.addItem(f"{lang.name} ({script_name})", lang.code)
        self._lang_combo.currentIndexChanged.connect(self._on_lang_changed)
        lg_layout.addWidget(self._lang_combo)
        self._script_label = QLabel("Script: Latin")
        self._script_label.setToolTip("The Unicode script system required for the selected language (Latin, Arabic, Hangul, etc.).")
        self._script_label.setStyleSheet("font-size: 11px; color: #a6adc8;")
        lg_layout.addWidget(self._script_label)
        self._coverage_label = QLabel("")
        self._coverage_label.setToolTip("How many glyphs the current font has vs. how many are needed.\nGreen = full coverage, Red = missing glyphs (use a donor font).")
        self._coverage_label.setStyleSheet("font-size: 11px;")
        lg_layout.addWidget(self._coverage_label)
        top.addWidget(lang_group)

        donor_group = QGroupBox("Donor Font")
        dg_layout = QVBoxLayout(donor_group)
        donor_row = QHBoxLayout()
        self._donor_btn = QPushButton("Select Donor Font...")
        self._donor_btn.setToolTip(
            "Select a .ttf/.otf font that has the characters your target language needs.\n"
            "Missing glyphs will be copied from this donor into the game font."
        )
        self._donor_btn.clicked.connect(self._select_donor_font)
        donor_row.addWidget(self._donor_btn)
        dg_layout.addLayout(donor_row)
        self._donor_label = QLabel("No donor selected")
        self._donor_label.setToolTip("Shows the selected donor font file and its glyph statistics.")
        self._donor_label.setStyleSheet("font-size: 11px; color: #a6adc8;")
        self._donor_label.setWordWrap(True)
        dg_layout.addWidget(self._donor_label)
        self._add_glyphs_btn = QPushButton("Add Glyphs from Donor")
        self._add_glyphs_btn.setObjectName("primary")
        self._add_glyphs_btn.setToolTip(
            "Copy missing glyphs from the donor font into the game font.\n"
            "Only characters needed by the selected language are added.\n"
            "The game font is modified in memory — use 'Patch Font to Game' to apply."
        )
        self._add_glyphs_btn.clicked.connect(self._add_glyphs)
        self._add_glyphs_btn.setEnabled(False)
        dg_layout.addWidget(self._add_glyphs_btn)
        top.addWidget(donor_group)

        layout.addLayout(top)

        splitter = QSplitter(Qt.Horizontal)

        stats_group = QGroupBox("Font Analysis")
        sg_layout = QVBoxLayout(stats_group)
        self._stats_text = QTextEdit()
        self._stats_text.setReadOnly(True)
        self._stats_text.setFont(QFont("Courier New", 10))
        sg_layout.addWidget(self._stats_text)
        splitter.addWidget(stats_group)

        preview_group = QGroupBox("Font Preview")
        pg_layout = QVBoxLayout(preview_group)
        self._preview_label = QLabel("Extract a font to preview")
        self._preview_label.setAlignment(Qt.AlignCenter)
        self._preview_label.setWordWrap(True)
        self._preview_label.setMinimumHeight(200)
        pg_layout.addWidget(self._preview_label)
        splitter.addWidget(preview_group)

        layout.addWidget(splitter, 1)

        warn_row = QHBoxLayout()
        self._sefont_warn = QLabel("")
        self._sefont_warn.setToolTip("Warning about a conflicting font file that can cause game crashes.")
        self._sefont_warn.setStyleSheet("color: #f38ba8; font-weight: bold;")
        warn_row.addWidget(self._sefont_warn, 1)
        self._delete_sefont_btn = QPushButton("Delete sefont/eng.ttf")
        self._delete_sefont_btn.setToolTip("Delete the conflicting sefont/eng.ttf file to prevent game crashes.\nThis file overrides the main font and can cause missing characters.")
        self._delete_sefont_btn.clicked.connect(self._delete_sefont)
        self._delete_sefont_btn.setVisible(False)
        warn_row.addWidget(self._delete_sefont_btn)
        layout.addLayout(warn_row)

        patch_row = QHBoxLayout()
        patch_row.addStretch()
        self._patch_btn = QPushButton("Patch Font to Game")
        self._patch_btn.setObjectName("primary")
        self._patch_btn.setToolTip(
            "Write the modified font back into the game archives.\n"
            "Updates the PAZ file, PAMT index, and PAPGT checksum chain.\n"
            "The game will use the patched font on next launch."
        )
        self._patch_btn.clicked.connect(self._patch_font)
        self._patch_btn.setEnabled(False)
        patch_row.addWidget(self._patch_btn)
        patch_row.addStretch()
        layout.addLayout(patch_row)

        self._progress = ProgressWidget()
        layout.addWidget(self._progress)

    def initialize_from_game(self, vfs: VfsManager) -> None:
        self._vfs = vfs
        self._packages_path = str(vfs._packages_path)
        # Pass the cached VfsManager so ``find_game_fonts`` reuses the
        # already-parsed PAMTs instead of re-parsing every 0.pamt from
        # disk — that was ~10 s of redundant work on each Font Builder
        # init (verified via log timing on a shipping 34-group install).
        self._game_fonts = find_game_fonts(self._packages_path, vfs=vfs)

        self._font_combo.clear()
        for fi in self._game_fonts:
            size_str = format_file_size(fi.orig_size)
            self._font_combo.addItem(f"{fi.filename}  ({size_str})", fi)

        self._check_sefont()

    def _check_sefont(self):
        if not self._packages_path:
            return
        game_root = os.path.dirname(self._packages_path)
        sefont_path = os.path.join(game_root, "sefont", "eng.ttf")
        if os.path.isfile(sefont_path):
            self._sefont_warn.setText("WARNING: sefont/eng.ttf exists! WILL cause crashes with PAZ font mods.")
            self._delete_sefont_btn.setVisible(True)
        else:
            self._sefont_warn.setText("")
            self._delete_sefont_btn.setVisible(False)

    def _delete_sefont(self):
        game_root = os.path.dirname(self._packages_path)
        sefont_path = os.path.join(game_root, "sefont", "eng.ttf")
        if confirm_action(self, "Delete sefont/eng.ttf", f"Delete {sefont_path}?\nRequired for PAZ font mods."):
            try:
                os.remove(sefont_path)
                self._check_sefont()
                show_info(self, "Deleted", "sefont/eng.ttf deleted.")
            except OSError as e:
                show_error(self, "Error", str(e))

    def _on_font_selected(self):
        fi = self._font_combo.currentData()
        if fi:
            self._update_coverage()

    def _on_lang_changed(self):
        lang_code = self._lang_combo.currentData()
        if lang_code:
            script = get_script_for_lang(lang_code)
            self._script_label.setText(f"Script: {script.name}")
            self._update_coverage()
            self._update_preview()

    def _update_coverage(self):
        if not self._current_font_data:
            self._coverage_label.setText("Extract font first to see coverage")
            return
        lang_code = self._lang_combo.currentData()
        if not lang_code:
            return
        script = get_script_for_lang(lang_code)
        try:
            font = load_ttfont(self._modified_font_data or self._current_font_data)
            cmap = font.getBestCmap()
            font.close()

            total_needed = 0
            total_have = 0
            for start, end in script.ranges:
                for cp in range(start, end + 1):
                    total_needed += 1
                    if cp in cmap:
                        total_have += 1

            pct = (total_have / total_needed * 100) if total_needed else 0
            missing = total_needed - total_have
            if missing == 0:
                self._coverage_label.setText(f"Coverage: {total_have}/{total_needed} ({pct:.0f}%) - COMPLETE")
                self._coverage_label.setStyleSheet("font-size: 11px; color: #a6e3a1;")
            else:
                self._coverage_label.setText(f"Coverage: {total_have}/{total_needed} ({pct:.0f}%) - {missing} MISSING")
                self._coverage_label.setStyleSheet("font-size: 11px; color: #f9e2af;")
        except Exception as e:
            self._coverage_label.setText(f"Error: {e}")

    def _extract_current_font(self):
        fi = self._font_combo.currentData()
        if not fi:
            show_error(self, "Error", "Select a font first.")
            return
        try:
            self._current_font_data = extract_font(fi)
            self._modified_font_data = b""
            self._patch_btn.setEnabled(False)
            self._add_glyphs_btn.setEnabled(True)
            self._show_font_stats(self._current_font_data, "Current Game Font")
            self._update_preview()
            self._update_coverage()
            self._progress.set_progress(100, f"Extracted: {format_file_size(len(self._current_font_data))}")
        except Exception as e:
            show_error(self, "Extract Error", str(e))

    def _save_font_to_disk(self):
        data = self._modified_font_data or self._current_font_data
        if not data:
            show_error(self, "Error", "Extract a font first.")
            return
        fi = self._font_combo.currentData()
        name = fi.filename if fi else "font.ttf"
        path = pick_save_file(self, "Save Font", name, "Font Files (*.ttf *.otf)")
        if path:
            with open(path, "wb") as f:
                f.write(data)
            show_info(self, "Saved", f"Saved to {os.path.basename(path)}")

    def _replace_font(self):
        path = pick_file(self, "Select Replacement Font", "", "Font Files (*.ttf *.otf)")
        if not path:
            return
        try:
            with open(path, "rb") as f:
                new_data = f.read()
            font = load_ttfont(new_data)
            font.close()
            self._modified_font_data = new_data
            self._patch_btn.setEnabled(True)
            self._add_glyphs_btn.setEnabled(True)
            self._show_font_stats(new_data, f"Replacement: {os.path.basename(path)}")
            self._update_preview()
            self._update_coverage()
            self._progress.set_status(f"Loaded: {os.path.basename(path)} ({format_file_size(len(new_data))})")
        except Exception as e:
            show_error(self, "Error", f"Invalid font: {e}")

    def _select_donor_font(self):
        path = pick_file(self, "Select Donor Font (e.g. NotoSans for target language)", "", "Font Files (*.ttf *.otf)")
        if path:
            self._donor_path = path
            try:
                with open(path, "rb") as f:
                    donor = load_ttfont(f.read())
                stats = get_font_stats(donor)
                donor.close()
                scripts_str = ", ".join(f"{k}: {v}" for k, v in sorted(stats["scripts"].items(), key=lambda x: -x[1])[:5])
                self._donor_label.setText(f"{os.path.basename(path)}\n{stats['total_glyphs']:,} glyphs | Scripts: {scripts_str}")
            except Exception as e:
                self._donor_label.setText(f"{os.path.basename(path)} (error reading: {e})")

    def _add_glyphs(self):
        base_data = self._modified_font_data or self._current_font_data
        if not base_data:
            show_error(self, "Error", "Extract the game font first.")
            return
        if not self._donor_path or not os.path.isfile(self._donor_path):
            show_error(self, "Error", "Select a donor font first.")
            return
        lang_code = self._lang_combo.currentData()
        if not lang_code:
            show_error(self, "Error", "Select a destination language.")
            return

        script = get_script_for_lang(lang_code)
        self._progress.set_progress(0, f"Adding {script.name} glyphs...")
        self._add_glyphs_btn.setEnabled(False)

        def do_build(worker):
            target = load_ttfont(base_data)
            with open(self._donor_path, "rb") as f:
                donor = load_ttfont(f.read())

            def pcb(done, total, msg):
                pct = int((done / total) * 100) if total else 0
                worker.report_progress(pct, msg)

            stats = add_script_glyphs(target, donor, script.name, progress_callback=pcb)
            result_data = save_ttfont(target)
            target.close()
            donor.close()
            return result_data, stats

        self._worker = FunctionWorker(do_build)
        self._worker.progress.connect(lambda p, m: self._progress.set_progress(p, m))

        def on_done(result):
            data, stats = result
            self._add_glyphs_btn.setEnabled(True)
            self._modified_font_data = data
            self._patch_btn.setEnabled(True)
            self._show_font_stats(data, f"Modified ({script.name} added)")
            self._update_preview()
            self._update_coverage()
            added = stats.get("glyphs_added", 0) or stats.get("glyphs_copied", 0)
            msg = f"Added {added} glyphs"
            self._progress.set_progress(100, msg)

        self._worker.finished_result.connect(on_done)
        self._worker.error_occurred.connect(lambda e: (self._add_glyphs_btn.setEnabled(True), show_error(self, "Error", str(e))))
        self._worker.start()

    def _show_font_stats(self, data: bytes, title: str):
        try:
            font = load_ttfont(data)
            stats = get_font_stats(font)
            font.close()

            lines = [f"=== {title} ===", f"Size:         {format_file_size(len(data))}",
                     f"Glyphs:       {stats['total_glyphs']:,}", f"Cmap:         {stats['cmap_entries']:,}",
                     f"Units/Em:     {stats['units_per_em']}", ""]

            lines.append("Script Coverage:")
            for script_name, count in sorted(stats["scripts"].items(), key=lambda x: -x[1]):
                info = SCRIPT_REGISTRY.get(script_name)
                total_range = sum(e - s + 1 for s, e in info.ranges) if info else 0
                pct = (count / total_range * 100) if total_range else 0
                marker = "FULL" if pct > 95 else f"{pct:.0f}%"
                lines.append(f"  {script_name:20s} {count:6d} codepoints ({marker})")

            missing_scripts = [s for s in SCRIPT_REGISTRY if s not in stats["scripts"]]
            if missing_scripts:
                lines.append(f"\nNot in font: {', '.join(missing_scripts[:10])}")

            pua_count = stats["pua_glyphs"]
            if pua_count > 0:
                lines.append(f"PUA glyphs: {pua_count}")
            if stats["gsub_scripts"]:
                lines.append(f"GSUB scripts: {', '.join(stats['gsub_scripts'])}")

            self._stats_text.setPlainText("\n".join(lines))
        except Exception as e:
            self._stats_text.setPlainText(f"Error: {e}")

    def _update_preview(self):
        data = self._modified_font_data or self._current_font_data
        if not data:
            self._preview_label.setText("Extract a font to preview")
            return

        lang_code = self._lang_combo.currentData() or "en"
        script = get_script_for_lang(lang_code)
        lang = self._lang_config.get_language(lang_code)
        lang_name = lang.name if lang else lang_code

        try:
            font_id = QFontDatabase.addApplicationFontFromData(data)
            if font_id >= 0:
                families = QFontDatabase.applicationFontFamilies(font_id)
                family = families[0] if families else "Unknown"
                preview_font = QFont(family, 22)

                sample = f"Font: {family}\nTarget: {lang_name} ({script.name})\n\n{script.sample_text}"
                self._preview_label.setFont(preview_font)
                self._preview_label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
                self._preview_label.setText(sample)
            else:
                self._preview_label.setText("Preview not available")
        except Exception as e:
            self._preview_label.setText(f"Preview error: {e}")

    def _patch_font(self):
        fi = self._font_combo.currentData()
        if not fi:
            show_error(self, "Error", "Select a font.")
            return
        font_data = self._modified_font_data
        if not font_data:
            show_error(self, "Error", "No modified font. Replace or add glyphs first.")
            return

        if not confirm_action(self, "Patch Font",
                              f"Patch {fi.filename} ({format_file_size(len(font_data))})?\n\n"
                              f"Original: {format_file_size(fi.orig_size)}\n"
                              f"A backup will be created."):
            return

        dialog = PatchProgressDialog(self)
        dialog.setWindowTitle("Patching Font...")
        self._patch_btn.setEnabled(False)

        def do_patch(worker):
            def pcb(s, t, m):
                worker.report_progress(int((s / t) * 100), m)
            return patch_font_to_game(font_data, fi, self._packages_path, progress_callback=pcb)

        self._worker = FunctionWorker(do_patch)

        def on_progress(pct, msg):
            dialog.set_step(max(1, pct // 12), 8, msg)
            dialog.log(msg)

        def on_done(result):
            self._patch_btn.setEnabled(True)
            if result.success:
                dialog.set_finished_success(result.paz_crc, result.pamt_crc, result.papgt_crc, result.backup_dir)
            else:
                dialog.set_finished_error(result.message)

        def on_error(e):
            self._patch_btn.setEnabled(True)
            dialog.set_finished_error(str(e))

        self._worker.progress.connect(on_progress)
        self._worker.finished_result.connect(on_done)
        self._worker.error_occurred.connect(on_error)
        self._worker.start()
        dialog.exec()
