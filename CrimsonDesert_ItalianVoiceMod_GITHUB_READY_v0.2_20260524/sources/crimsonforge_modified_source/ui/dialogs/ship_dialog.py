"""Translation Ship to App dialog."""

from __future__ import annotations

import json
import os
import struct
import zipfile
from datetime import datetime

from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)


GAME_LANGUAGES = [
    ("kor", "Korean"),
    ("eng", "English"),
    ("jpn", "Japanese"),
    ("rus", "Russian"),
    ("tur", "Turkish"),
    ("spa-es", "Spanish (Spain)"),
    ("spa-mx", "Spanish (Mexico)"),
    ("fre", "French"),
    ("ger", "German"),
    ("ita", "Italian"),
    ("pol", "Polish"),
    ("por-br", "Portuguese (Brazil)"),
    ("zho-tw", "Chinese (Traditional)"),
    ("zho-cn", "Chinese (Simplified)"),
]

LANG_TO_PALOC = {key: f"localizationstring_{key}.paloc" for key, _ in GAME_LANGUAGES}


class ShipToAppDialog(QDialog):
    def __init__(self, project, vfs, discovered_palocs, config, parent=None):
        super().__init__(parent)
        self._project = project
        self._vfs = vfs
        self._discovered_palocs = discovered_palocs
        self._config = config
        self._built_font_data = None
        self._built_font_info = None
        self._setup_ui()

    def _setup_ui(self):
        self.setWindowTitle("Ship to App - Generate Package")
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)

        from translation.language_config import LanguageConfig

        target_lang = self._project.target_lang or ""
        lang_obj = LanguageConfig().get_language(target_lang)
        lang_name = lang_obj.name if lang_obj else target_lang

        info_group = QGroupBox("Mod Information")
        info_form = QFormLayout(info_group)
        self._mod_name = QLineEdit(f"Crimson Desert - {lang_name} Localization")
        self._translator = QLineEdit()
        self._translator.setPlaceholderText("Your name or team name")
        self._version = QLineEdit("1.0.0")
        info_form.addRow("Mod Name:", self._mod_name)
        info_form.addRow("Translator:", self._translator)
        info_form.addRow("Version:", self._version)
        layout.addWidget(info_group)

        lang_group = QGroupBox("Game Language to Replace")
        lang_form = QFormLayout(lang_group)
        self._replace_combo = QComboBox()
        for key, name in GAME_LANGUAGES:
            self._replace_combo.addItem(f"{name} ({key})", key)
        eng_idx = self._replace_combo.findData("eng")
        if eng_idx >= 0:
            self._replace_combo.setCurrentIndex(eng_idx)
        self._replace_combo.currentIndexChanged.connect(self._on_replace_changed)
        self._lang_warning = QLabel(
            f'End users will select "English" in-game to see {lang_name} text.'
        )
        self._lang_warning.setWordWrap(True)
        self._lang_warning.setStyleSheet("color: #f9e2af; font-size: 11px;")
        lang_form.addRow("Replace:", self._replace_combo)
        lang_form.addRow("", self._lang_warning)
        layout.addWidget(lang_group)

        font_group = QGroupBox("Font")
        font_form = QFormLayout(font_group)
        self._include_font = QCheckBox("Include custom font")
        self._include_font.toggled.connect(self._on_font_toggled)
        donor_row = QHBoxLayout()
        self._donor_path = QLineEdit()
        self._donor_path.setPlaceholderText("Select donor .ttf font...")
        self._donor_path.setEnabled(False)
        self._donor_btn = QPushButton("Browse...")
        self._donor_btn.setEnabled(False)
        self._donor_btn.clicked.connect(self._browse_donor)
        donor_row.addWidget(self._donor_path, 1)
        donor_row.addWidget(self._donor_btn)
        self._font_status = QLabel("Enable checkbox, then select a donor font.")
        self._font_status.setWordWrap(True)
        self._font_status.setStyleSheet("color: #6c7086; font-size: 11px;")
        font_form.addRow("", self._include_font)
        font_form.addRow("Donor Font:", donor_row)
        font_form.addRow("Status:", self._font_status)
        layout.addWidget(font_group)

        pack_group = QGroupBox("Packaging")
        pack_form = QFormLayout(pack_group)
        self._package_mode = QComboBox()
        self._package_mode.addItem("Mod Manager ZIP (small)", "manager")
        self._package_mode.addItem("Standalone ZIP (full patched archives)", "standalone")
        saved_mode = str(self._config.get("translation.ship.package_mode", "manager")).strip().lower()
        self._package_mode.setCurrentIndex(0 if saved_mode == "manager" else 1)
        self._package_mode.currentIndexChanged.connect(self._refresh_package_mode_ui)
        self._package_note = QLabel("")
        self._package_note.setWordWrap(True)
        self._package_note.setStyleSheet("color: #89b4fa; font-size: 11px;")
        pack_form.addRow("Mode:", self._package_mode)
        pack_form.addRow("", self._package_note)
        layout.addWidget(pack_group)

        self._progress = QProgressBar()
        self._progress.setVisible(False)
        self._status = QLabel("")
        layout.addWidget(self._progress)
        layout.addWidget(self._status)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        self._generate_btn = QPushButton("Generate ZIP")
        self._generate_btn.clicked.connect(self._do_generate)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(self._generate_btn)
        layout.addLayout(btn_row)
        self._refresh_package_mode_ui()

    def _package_mode_key(self) -> str:
        return str(self._package_mode.currentData() or "manager")

    def _refresh_package_mode_ui(self):
        if self._package_mode_key() == "manager":
            self._package_note.setText(
                "Generates a much smaller ZIP with loose translated files under files/, "
                "plus manifest.json and modinfo.json for CDUMM, Crimson Browser, and similar managers."
            )
            self._generate_btn.setText("Generate Manager ZIP")
        else:
            self._package_note.setText(
                "Generates patched PAZ/PAMT/PAPGT files plus install.bat and uninstall.bat "
                "for direct end-user installation."
            )
            self._generate_btn.setText("Generate Standalone ZIP")

    def _on_replace_changed(self):
        from translation.language_config import LanguageConfig

        lang_key = self._replace_combo.currentData()
        lang_name = dict(GAME_LANGUAGES).get(lang_key, lang_key)
        tl = LanguageConfig().get_language(self._project.target_lang)
        target_name = tl.name if tl else self._project.target_lang
        self._lang_warning.setText(
            f'End users will select "{lang_name}" in-game to see {target_name} text.'
        )

    def _on_font_toggled(self, checked):
        self._donor_path.setEnabled(checked)
        self._donor_btn.setEnabled(checked)
        if not checked:
            self._built_font_data = None
            self._built_font_info = None
            self._font_status.setText("Font disabled.")
            self._font_status.setStyleSheet("color: #6c7086; font-size: 11px;")

    def _browse_donor(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Donor Font", "", "Font Files (*.ttf *.otf)"
        )
        if path:
            self._donor_path.setText(path)
            self._build_font(path)

    def _build_font(self, donor_path):
        self._font_status.setText("Building font...")
        self._font_status.setStyleSheet("color: #89b4fa; font-size: 11px;")
        QApplication.processEvents()
        try:
            from core.font_builder import add_script_glyphs, load_ttfont, save_ttfont
            from core.script_ranges import LANG_TO_SCRIPT, SCRIPT_REGISTRY

            replace_key = self._replace_combo.currentData()
            target_font_name = f"basefont_{replace_key}.ttf"
            base_entry = None
            base_group = None
            for group_key, pamt in self._vfs._pamt_cache.items():
                for entry in pamt.file_entries:
                    entry_path = entry.path.replace("\\", "/").lower()
                    if entry_path.endswith(target_font_name) or entry_path.endswith("basefont.ttf"):
                        base_entry = entry
                        base_group = group_key
                        break
                if base_entry:
                    break
            if not base_entry:
                self._font_status.setText("No game font found.")
                self._font_status.setStyleSheet("color: #f38ba8; font-size: 11px;")
                return
            base_bytes = self._vfs.read_entry_data(base_entry)
            script_name = LANG_TO_SCRIPT.get(self._project.target_lang, "Latin")
            target_font = load_ttfont(base_bytes)
            with open(donor_path, "rb") as f:
                donor_font = load_ttfont(f.read())
            if SCRIPT_REGISTRY.get(script_name):
                add_script_glyphs(target_font, donor_font, script_name)
            self._built_font_data = save_ttfont(target_font)
            self._built_font_info = {
                "group": base_group,
                "full_path": base_entry.path,
            }
            self._font_status.setText(
                f"Font ready: {os.path.basename(base_entry.path)} + {os.path.basename(donor_path)} | Script: {script_name}"
            )
            self._font_status.setStyleSheet("color: #a6e3a1; font-size: 11px;")
        except Exception as exc:
            self._built_font_data = None
            self._built_font_info = None
            self._font_status.setText(f"Font failed: {exc}")
            self._font_status.setStyleSheet("color: #f38ba8; font-size: 11px;")

    def _do_generate(self):
        if not self._translator.text().strip():
            QMessageBox.warning(self, "Missing", "Enter translator name.")
            return
        default_name = self._mod_name.text().replace(" ", "_").replace("-", "_")
        if self._package_mode_key() == "manager":
            default_name += "_manager"
        save_path, _ = QFileDialog.getSaveFileName(
            self, "Save ZIP", os.path.expanduser(f"~/Desktop/{default_name}.zip"), "ZIP (*.zip)"
        )
        if not save_path:
            return
        if not save_path.lower().endswith(".zip"):
            save_path += ".zip"
        self._config.set("translation.ship.package_mode", self._package_mode_key())
        self._config.save()
        self._generate_btn.setEnabled(False)
        self._progress.setVisible(True)
        try:
            self._build_zip(save_path)
            size = os.path.getsize(save_path)
            size_str = f"{size / (1024 * 1024):.1f} MB" if size > 1024 * 1024 else f"{size / 1024:.0f} KB"
            msg = (
                "Import this ZIP into CDUMM, Crimson Browser, or another loose-file aware mod manager."
                if self._package_mode_key() == "manager"
                else "End user extracts ZIP and runs install.bat."
            )
            QMessageBox.information(
                self,
                "Done",
                f"ZIP saved to:\n{save_path}\n\nSize: {size_str}\nFont: {'Included' if self._built_font_data else 'No'}\n\n{msg}",
            )
            self.accept()
        except Exception as exc:
            QMessageBox.critical(self, "Error", str(exc))
        finally:
            self._progress.setVisible(False)
            self._generate_btn.setEnabled(True)

    def _build_zip(self, output_path):
        if self._package_mode_key() == "manager":
            self._build_manager_zip(output_path)
        else:
            self._build_standalone_zip(output_path)

    def _prepare_ctx(self):
        from core.paloc_parser import parse_paloc, splice_values_in_raw
        from core.pamt_parser import find_file_entry, parse_pamt
        from translation.language_config import LanguageConfig

        game_path = self._config.get("general.last_game_path", "")
        replace_key = self._replace_combo.currentData()
        replace_name = dict(GAME_LANGUAGES).get(replace_key, replace_key)
        target_paloc = LANG_TO_PALOC.get(replace_key, "")
        tl = LanguageConfig().get_language(self._project.target_lang)
        target_name = tl.name if tl else self._project.target_lang
        target_group = next((p["group"] for p in self._discovered_palocs if p["filename"] == target_paloc), "")
        if not target_group:
            raise ValueError(f"Target paloc '{target_paloc}' not found in game")
        self._status.setText("Reading target paloc...")
        self._progress.setValue(5)
        QApplication.processEvents()
        group_dir = os.path.join(game_path, target_group)
        pamt_data = parse_pamt(os.path.join(group_dir, "0.pamt"), paz_dir=group_dir)
        paloc_entry = find_file_entry(pamt_data, target_paloc)
        if not paloc_entry:
            raise FileNotFoundError(f"'{target_paloc}' not in PAMT")
        raw = self._vfs.read_entry_data(paloc_entry)
        target_entries = parse_paloc(raw)
        self._status.setText("Splicing translations...")
        self._progress.setValue(15)
        QApplication.processEvents()
        project_map = {entry.key: entry for entry in self._project.entries}
        replacements = [
            (pe, project_map[pe.key].translated_text)
            for pe in target_entries
            if pe.key in project_map and project_map[pe.key].translated_text
        ]
        translated_paloc = splice_values_in_raw(raw, replacements) if replacements else raw
        loose_files = {paloc_entry.path: translated_paloc}
        font_entry_path = ""
        if self._built_font_data and self._built_font_info:
            font_entry_path = self._built_font_info["full_path"]
            loose_files[font_entry_path] = self._built_font_data
        return {
            "game_path": game_path,
            "replace_key": replace_key,
            "replace_name": replace_name,
            "target_name": target_name,
            "target_group": target_group,
            "mod_name": self._mod_name.text().strip(),
            "translator": self._translator.text().strip(),
            "version": self._version.text().strip(),
            "paloc_entry": paloc_entry,
            "pamt_data": pamt_data,
            "translated_paloc": translated_paloc,
            "replacement_count": len(replacements),
            "game_build": self._detect_game_build(game_path),
            "loose_files": loose_files,
            "font_entry_path": font_entry_path,
            "font_included": bool(self._built_font_data and self._built_font_info),
        }

    def _build_standalone_zip(self, output_path):
        from core.checksum_engine import pa_checksum
        from core.compression_engine import compress
        from core.crypto_engine import encrypt
        from core.pamt_parser import find_file_entry, parse_pamt, update_pamt_file_entry, update_pamt_paz_entry, update_pamt_self_crc
        from core.papgt_manager import get_pamt_crc_offset, parse_papgt, update_papgt_pamt_crc, update_papgt_self_crc

        ctx = self._prepare_ctx()
        self._status.setText("Compressing and encrypting paloc...")
        self._progress.setValue(25)
        QApplication.processEvents()
        paloc_entry = ctx["paloc_entry"]
        translated_paloc = ctx["translated_paloc"]
        comp = compress(translated_paloc, 2)
        enc = encrypt(comp, os.path.basename(paloc_entry.path))
        self._status.setText("Building patched PAZ...")
        self._progress.setValue(35)
        QApplication.processEvents()
        with open(paloc_entry.paz_file, "rb") as f:
            paz = bytearray(f.read())
        aligned = (len(paz) + 15) & ~15
        if aligned > len(paz):
            paz.extend(b"\x00" * (aligned - len(paz)))
        new_offset = len(paz)
        paz.extend(enc)
        self._status.setText("Computing checksums...")
        self._progress.setValue(50)
        QApplication.processEvents()
        paz_crc = pa_checksum(bytes(paz))
        pamt_raw = bytearray(ctx["pamt_data"].raw_data)
        update_pamt_file_entry(pamt_raw, paloc_entry, new_comp_size=len(enc), new_orig_size=len(translated_paloc), new_offset=new_offset)
        update_pamt_paz_entry(pamt_raw, ctx["pamt_data"].paz_table[paloc_entry.paz_index], paz_crc, len(paz))
        update_pamt_self_crc(pamt_raw)
        papgt_data = parse_papgt(os.path.join(ctx["game_path"], "meta", "0.papgt"))
        papgt_raw = bytearray(papgt_data.raw_data)
        pamt_crc = pa_checksum(bytes(pamt_raw[12:]))
        crc_off = get_pamt_crc_offset(papgt_data, int(ctx["target_group"]))
        if crc_off is not None:
            update_papgt_pamt_crc(papgt_raw, crc_off, pamt_crc)
        update_papgt_self_crc(papgt_raw)
        patched = {
            f"{ctx['target_group']}/0.pamt": bytes(pamt_raw),
            f"{ctx['target_group']}/{os.path.basename(paloc_entry.paz_file)}": bytes(paz),
            "meta/0.papgt": bytes(papgt_raw),
        }
        if self._built_font_data and self._built_font_info:
            self._status.setText("Patching font...")
            self._progress.setValue(65)
            QApplication.processEvents()
            fg = self._built_font_info["group"]
            fg_dir = os.path.join(ctx["game_path"], fg)
            fg_pamt = parse_pamt(os.path.join(fg_dir, "0.pamt"), paz_dir=fg_dir)
            fentry = find_file_entry(fg_pamt, self._built_font_info["full_path"])
            if fentry:
                fcomp = compress(self._built_font_data, 2)
                with open(fentry.paz_file, "rb") as f:
                    fpaz = bytearray(f.read())
                fa = (len(fpaz) + 15) & ~15
                if fa > len(fpaz):
                    fpaz.extend(b"\x00" * (fa - len(fpaz)))
                foff = len(fpaz)
                fpaz.extend(fcomp)
                fcrc = pa_checksum(bytes(fpaz))
                fr = bytearray(fg_pamt.raw_data)
                update_pamt_file_entry(fr, fentry, new_comp_size=len(fcomp), new_orig_size=len(self._built_font_data), new_offset=foff)
                update_pamt_paz_entry(fr, fg_pamt.paz_table[fentry.paz_index], fcrc, len(fpaz))
                update_pamt_self_crc(fr)
                fcrc2 = pa_checksum(bytes(fr[12:]))
                fco = get_pamt_crc_offset(papgt_data, int(fg))
                if fco is not None:
                    update_papgt_pamt_crc(papgt_raw, fco, fcrc2)
                update_papgt_self_crc(papgt_raw)
                patched[f"{fg}/0.pamt"] = bytes(fr)
                patched[f"{fg}/{os.path.basename(fentry.paz_file)}"] = bytes(fpaz)
                patched["meta/0.papgt"] = bytes(papgt_raw)
        self._status.setText("Writing ZIP...")
        self._progress.setValue(85)
        QApplication.processEvents()
        with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
            for rel_path, data in patched.items():
                zf.writestr(f"data/{rel_path}", data)
            zf.writestr("install.bat", self._bat_install(ctx["mod_name"], ctx["translator"], ctx["version"], ctx["replace_name"], ctx["target_name"], list(patched.keys())))
            zf.writestr("uninstall.bat", self._bat_uninstall(ctx["mod_name"], ctx["replace_name"]))
            zf.writestr("README.txt", self._readme(ctx["mod_name"], ctx["translator"], ctx["version"], ctx["replace_name"], ctx["target_name"], ctx["replacement_count"]))
        self._status.setText("Done!")
        self._progress.setValue(100)

    def _build_manager_zip(self, output_path):
        ctx = self._prepare_ctx()
        self._status.setText("Writing manager ZIP...")
        self._progress.setValue(75)
        QApplication.processEvents()
        created_utc = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
        files_meta = [
            {"path": rel_path, "kind": "font" if rel_path == ctx["font_entry_path"] else "paloc"}
            for rel_path in sorted(ctx["loose_files"].keys())
        ]
        manifest = {
            "format": "v1",
            "schema_version": 1,
            "kind": "translation_loose_mod",
            "game": "Crimson Desert",
            "title": ctx["mod_name"],
            "name": ctx["mod_name"],
            "mod_name": ctx["mod_name"],
            "author": ctx["translator"],
            "version": ctx["version"],
            "created_utc": created_utc,
            "generator": "CrimsonForge",
            "generator_url": "https://github.com/hzeemr/crimsonforge",
            "game_build": ctx["game_build"],
            "replace_language": {"code": ctx["replace_key"], "name": ctx["replace_name"]},
            "target_language": {"code": self._project.target_lang, "name": ctx["target_name"]},
            "translated_entry_count": ctx["replacement_count"],
            "file_count": len(ctx["loose_files"]),
            "files_root": "files",
            "files": files_meta,
            "font_included": ctx["font_included"],
        }
        modinfo = {
            "title": ctx["mod_name"],
            "name": ctx["mod_name"],
            "author": ctx["translator"],
            "version": ctx["version"],
            "game": "Crimson Desert",
            "format": "v1",
            "type": "translation_loose_mod",
            "description": f"Translation package for {ctx['target_name']} replacing {ctx['replace_name']}.",
            "generator": "CrimsonForge",
            "generator_url": "https://github.com/hzeemr/crimsonforge",
            "game_build": ctx["game_build"],
            "created_utc": created_utc,
        }
        with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
            for rel_path, data in sorted(ctx["loose_files"].items()):
                zf.writestr(f"files/{rel_path}", data)
            zf.writestr("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))
            zf.writestr("modinfo.json", json.dumps(modinfo, indent=2, ensure_ascii=False))
            zf.writestr("README.txt", self._readme_manager(ctx["mod_name"], ctx["translator"], ctx["version"], ctx["replace_name"], ctx["target_name"], ctx["replacement_count"], ctx["font_included"], ctx["game_build"]))
        self._status.setText("Done!")
        self._progress.setValue(100)

    def _bat_install(self, mod_name, translator, version, replace_name, target_name, files):
        lines = [
            "@echo off",
            "setlocal EnableDelayedExpansion",
            "chcp 65001 >nul 2>&1",
            f"title {mod_name} v{version}",
            "echo.",
            f"echo  {mod_name}",
            f"echo  by {translator} - v{version}",
            f"echo  Replaces: {replace_name} with {target_name}",
            "echo.",
            'set "GP="',
            "for %%D in (",
            '    "C:\\Program Files (x86)\\Steam\\steamapps\\common\\Crimson Desert"',
            '    "C:\\Program Files\\Steam\\steamapps\\common\\Crimson Desert"',
            '    "D:\\SteamLibrary\\steamapps\\common\\Crimson Desert"',
            '    "E:\\SteamLibrary\\steamapps\\common\\Crimson Desert"',
            '    "F:\\SteamLibrary\\steamapps\\common\\Crimson Desert"',
            ') do ( if exist "%%~D\\meta\\0.papgt" ( set "GP=%%~D" & goto :f ) )',
            'for /f "tokens=2*" %%A in (\'reg query "HKCU\\Software\\Valve\\Steam" /v SteamPath 2^>nul\') do set "SP=%%B"',
            'if defined SP if exist "!SP!\\steamapps\\common\\Crimson Desert\\meta\\0.papgt" set "GP=!SP!\\steamapps\\common\\Crimson Desert"',
            'if not defined GP ( echo [ERROR] Game not found. & pause & exit /b 1 )',
            ":f",
            'echo [OK] !GP!',
            "echo.",
            'set "D=%~dp0data"',
        ]
        for rel_path in sorted(set(files)):
            safe_path = rel_path.replace("/", "\\")
            lines.append(f'copy /Y "!D!\\{safe_path}" "!GP!\\{safe_path}" >nul && echo   Copied: {safe_path}')
        lines += [
            "echo.",
            f"echo [DONE] {target_name} installed!",
            f'echo In-game: select "{replace_name}" to see {target_name}.',
            "echo To uninstall: run uninstall.bat",
            "echo.",
            "pause",
        ]
        return "\r\n".join(lines)

    def _bat_uninstall(self, mod_name, replace_name):
        return "\r\n".join([
            "@echo off",
            "setlocal EnableDelayedExpansion",
            "chcp 65001 >nul 2>&1",
            f"title Uninstall {mod_name}",
            "echo.",
            f"echo  Uninstall {mod_name}",
            "echo  Steam will verify and restore original files.",
            "echo.",
            'set /p C="Proceed? (Y/N): "',
            'if /i not "!C!"=="Y" exit /b 0',
            "start steam://validate/3321460",
            f"echo [OK] Steam will restore {replace_name}. Wait for it to finish.",
            "echo.",
            "pause",
        ])

    def _readme(self, mod_name, translator, version, replace_name, target_name, count):
        return (
            f"{mod_name}\n{'=' * len(mod_name)}\n\n"
            f"Translator: {translator}\nVersion: {version}\nTranslated: {count:,} entries\n\n"
            "INSTALL:\n  1. Extract ZIP\n  2. Run install.bat\n"
            f'  3. In-game select "{replace_name}" to see {target_name}\n\n'
            "UNINSTALL:\n  Run uninstall.bat (uses Steam Verify)\n\n"
            "Generated by CrimsonForge\nhttps://github.com/hzeemr/crimsonforge\n"
        )

    def _readme_manager(self, mod_name, translator, version, replace_name, target_name, count, font_included, game_build):
        return (
            f"{mod_name}\n{'=' * len(mod_name)}\n\n"
            f"Translator: {translator}\nVersion: {version}\nGame Build: {game_build}\n"
            f"Translated: {count:,} entries\nFont: {'Included' if font_included else 'No'}\n\n"
            "INSTALL\n  Import this ZIP into CDUMM, Crimson Browser, or another loose-file aware Crimson Desert mod manager.\n\n"
            f'IN-GAME\n  Select "{replace_name}" to see {target_name}.\n\n'
            "Generated by CrimsonForge\nhttps://github.com/hzeemr/crimsonforge\n"
        )

    def _detect_game_build(self, game_path):
        version_text = ""
        paver_path = os.path.join(game_path, "meta", "0.paver")
        if os.path.isfile(paver_path):
            try:
                with open(paver_path, "rb") as f:
                    data = f.read(6)
                if len(data) >= 6:
                    major, minor, patch = struct.unpack_from("<HHH", data, 0)
                    version_text = f"v{major}.{minor:02d}.{patch:02d}"
            except Exception:
                version_text = ""
        papgt_crc = ""
        papgt_path = os.path.join(game_path, "meta", "0.papgt")
        if os.path.isfile(papgt_path):
            try:
                from core.checksum_engine import pa_checksum
                with open(papgt_path, "rb") as f:
                    papgt_crc = f"CRC 0x{pa_checksum(f.read()):08X}"
            except Exception:
                papgt_crc = ""
        if version_text and papgt_crc:
            return f"{version_text} | {papgt_crc}"
        if version_text:
            return version_text
        if papgt_crc:
            return papgt_crc
        return "unknown"
