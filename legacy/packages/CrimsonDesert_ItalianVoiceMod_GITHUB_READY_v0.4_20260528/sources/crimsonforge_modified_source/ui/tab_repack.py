"""Repack tab - modify and repack files with full checksum chain.

Game path is set by the main window on startup. User only needs to
select modified files directory and click repack.
"""

import os
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QTreeWidget, QTreeWidgetItem, QCheckBox, QHeaderView, QGroupBox,
)
from PySide6.QtCore import Qt

from core.repack_engine import RepackEngine, ModifiedFile
from core.pamt_parser import parse_pamt, find_file_entry, find_all_file_entries
from ui.widgets.progress_widget import ProgressWidget
from ui.dialogs.file_picker import pick_directory
from ui.dialogs.confirmation import show_error, show_info, confirm_action
from utils.thread_worker import FunctionWorker
from utils.platform_utils import format_file_size
from utils.logger import get_logger

logger = get_logger("ui.tab_repack")


class RepackTab(QWidget):
    """Tab for repacking modified files into game archives."""

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self._config = config
        self._game_path = ""
        self._worker: FunctionWorker = None
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # Pre-fill the source path from the last-used value so the tab
        # shows something meaningful on first render rather than a bare
        # placeholder. The user can still browse to a different dir.
        src_row = QHBoxLayout()
        src_row.addWidget(QLabel("Modified Files:"))
        self._source_path = QLineEdit(self._config.get("repack.source_dir", ""))
        self._source_path.setPlaceholderText("Directory containing modified files to repack...")
        self._source_path.editingFinished.connect(self._on_source_path_changed)
        src_row.addWidget(self._source_path, 1)
        src_browse = QPushButton("Browse...")
        src_browse.clicked.connect(self._browse_source)
        src_row.addWidget(src_browse)
        layout.addLayout(src_row)

        backup_row = QHBoxLayout()
        backup_row.addWidget(QLabel("Backup Dir:"))
        self._backup_path = QLineEdit(self._config.get("repack.backup_dir", ""))
        self._backup_path.setPlaceholderText("Directory for backups (auto-created if empty)...")
        backup_row.addWidget(self._backup_path, 1)
        bk_browse = QPushButton("Browse...")
        bk_browse.clicked.connect(self._browse_backup)
        backup_row.addWidget(bk_browse)
        layout.addLayout(backup_row)

        # Header-level hint — the file tree is empty until we have both
        # a source dir and a game dir to scan against. The hint gives
        # the user a clear next step instead of a blank tree.
        self._hint = QLabel("")
        self._hint.setWordWrap(True)
        self._hint.setStyleSheet("color: #a6adc8; padding: 4px;")
        layout.addWidget(self._hint)

        self._file_tree = QTreeWidget()
        self._file_tree.setHeaderLabels(["", "File", "Size", "Target PAZ", "Status"])
        self._file_tree.setUniformRowHeights(True)
        self._file_tree.header().setSectionResizeMode(1, QHeaderView.Stretch)
        self._file_tree.setAlternatingRowColors(True)
        layout.addWidget(self._file_tree, 1)

        scan_btn = QPushButton("Scan Modified Files")
        scan_btn.clicked.connect(self._scan_files)
        layout.addWidget(scan_btn)

        crc_group = QGroupBox("Checksum Chain Status")
        crc_layout = QHBoxLayout(crc_group)
        self._paz_crc_label = QLabel("PAZ CRC:  Pending")
        self._pamt_crc_label = QLabel("PAMT CRC: Pending")
        self._papgt_crc_label = QLabel("PAPGT CRC: Pending")
        crc_layout.addWidget(self._paz_crc_label)
        crc_layout.addWidget(self._pamt_crc_label)
        crc_layout.addWidget(self._papgt_crc_label)
        layout.addWidget(crc_group)

        opt_row = QHBoxLayout()
        self._backup_check = QCheckBox("Create Backup")
        self._backup_check.setChecked(self._config.get("repack.auto_backup", True))
        opt_row.addWidget(self._backup_check)
        self._verify_check = QCheckBox("Verify After Repack")
        self._verify_check.setChecked(self._config.get("repack.verify_after_repack", True))
        opt_row.addWidget(self._verify_check)
        self._timestamp_check = QCheckBox("Preserve Timestamps")
        self._timestamp_check.setChecked(self._config.get("repack.preserve_timestamps", True))
        opt_row.addWidget(self._timestamp_check)
        opt_row.addStretch()
        layout.addLayout(opt_row)

        btn_row = QHBoxLayout()
        # Preview button — runs path resolution against the live PAMTs
        # and shows the user the canonical path each file will be patched
        # at, plus any shortcut aliases being skipped. Catches the
        # "patched the wrong entry" bug BEFORE it touches disk.
        preview_btn = QPushButton("Preview Resolution")
        preview_btn.setToolTip(
            "Show which canonical path each selected file resolves to "
            "(and which shortcut aliases are skipped) without modifying "
            "any game files. Run this before Repack to catch wrong-entry "
            "bugs."
        )
        preview_btn.clicked.connect(self._preview_resolution)
        btn_row.addWidget(preview_btn)
        self._repack_btn = QPushButton("Repack Selected")
        self._repack_btn.setObjectName("primary")
        self._repack_btn.clicked.connect(self._repack)
        btn_row.addWidget(self._repack_btn)
        restore_btn = QPushButton("Restore Backup")
        restore_btn.clicked.connect(self._restore_backup)
        btn_row.addWidget(restore_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._progress = ProgressWidget()
        layout.addWidget(self._progress)

    def initialize_from_game(self, packages_path: str) -> None:
        """Populate the tab once the game is loaded.

        Called by ``MainWindow`` via the lazy-tab background-init flow.
        Previously this just stashed the game path and left the tab
        looking blank — users reported "Repack tab is empty". We now:

          1. Store the game path so Repack has somewhere to write.
          2. Read the last-used Modified Files directory from config.
          3. If that directory is valid, auto-scan it so the user sees
             their mod files immediately instead of a bare tree.
          4. If nothing is configured, show a clear next-step hint.

        This runs on a background thread via FunctionWorker, so disk
        I/O in step 3 doesn't block the UI paint that surfaces the
        overlay.
        """
        self._game_path = packages_path
        source_dir = self._source_path.text().strip()
        if source_dir and os.path.isdir(source_dir):
            # Auto-scan the saved directory so the tab shows data the
            # moment it becomes visible.
            self._scan_files(notify_if_empty=False)
            self._refresh_hint()
        else:
            self._refresh_hint()

    def set_game_path(self, path: str):
        """Backward compat alias."""
        self._game_path = path

    def _refresh_hint(self) -> None:
        """Update the header hint label to reflect the current state."""
        src = self._source_path.text().strip()
        if not self._game_path:
            self._hint.setText(
                "Game not loaded. Return to Game Setup and pick your "
                "Crimson Desert packages/ directory before repacking."
            )
            return
        if not src:
            self._hint.setText(
                "Click Browse to pick a folder containing modified .pac/"
                ".pam/.paloc/etc. files, then Scan Modified Files. Files "
                "must keep their original names so they can be matched "
                "back into the game archives."
            )
            return
        if not os.path.isdir(src):
            self._hint.setText(
                f"Modified files directory does not exist: {src}"
            )
            return
        count = self._file_tree.topLevelItemCount()
        if count == 0:
            self._hint.setText(
                f"Source: {src}\nClick Scan Modified Files to populate the list."
            )
        else:
            self._hint.setText(
                f"Source: {src}\n{count} file(s) ready — tick the ones "
                "you want to repack, then click Repack Selected."
            )

    def _on_source_path_changed(self) -> None:
        path = self._source_path.text().strip()
        if path:
            self._config.set("repack.source_dir", path)
            self._config.save()
        self._refresh_hint()

    def _browse_source(self):
        path = pick_directory(self, "Select Modified Files Directory")
        if path:
            self._source_path.setText(path)
            self._config.set("repack.source_dir", path)
            self._config.save()
            self._refresh_hint()

    def _browse_backup(self):
        path = pick_directory(self, "Select Backup Directory")
        if path:
            self._backup_path.setText(path)
            self._config.set("repack.backup_dir", path)
            self._config.save()

    def _scan_files(self, notify_if_empty: bool = True):
        """Walk the Modified Files directory and populate the tree.

        ``notify_if_empty`` controls whether we surface a popup when the
        directory is invalid or empty. Auto-scan on tab init passes
        ``False`` so a missing directory doesn't spam a dialog on
        every launch.
        """
        src = self._source_path.text().strip()
        if not src or not os.path.isdir(src):
            if notify_if_empty:
                show_error(self, "Error", "Select a valid directory with modified files.")
            self._refresh_hint()
            return
        self._file_tree.clear()
        for root, dirs, files in os.walk(src):
            for fname in files:
                fpath = os.path.join(root, fname)
                try:
                    size = os.path.getsize(fpath)
                except OSError:
                    continue
                item = QTreeWidgetItem()
                item.setCheckState(0, Qt.Checked)
                item.setText(1, os.path.relpath(fpath, src))
                item.setText(2, format_file_size(size))
                item.setText(3, "Auto-detect")
                item.setText(4, "Ready")
                item.setData(0, Qt.UserRole, fpath)
                self._file_tree.addTopLevelItem(item)
        count = self._file_tree.topLevelItemCount()
        self._progress.set_status(f"Found {count} files")
        self._refresh_hint()

    def _repack(self):
        if not self._game_path or not os.path.isdir(self._game_path):
            show_error(self, "Error",
                       "Game path not set. Return to Game Setup tab and load the game first.")
            return

        checked_files = []
        for i in range(self._file_tree.topLevelItemCount()):
            item = self._file_tree.topLevelItem(i)
            if item.checkState(0) == Qt.Checked:
                checked_files.append(item.data(0, Qt.UserRole))

        if not checked_files:
            show_error(self, "Error", "No files selected for repacking.")
            return

        if not confirm_action(self, "Confirm Repack",
                              f"Repack {len(checked_files)} files into game archives?\n"
                              f"This will modify game files."):
            return

        self._repack_btn.setEnabled(False)
        self._progress.set_progress(0, "Resolving file entries...")

        papgt_path = os.path.join(self._game_path, "meta", "0.papgt")
        if not os.path.isfile(papgt_path):
            show_error(self, "Error",
                       f"PAPGT root index not found: {papgt_path}\n"
                       f"Ensure the game is loaded correctly.")
            self._repack_btn.setEnabled(True)
            return

        modified_list = []
        resolve_errors = []
        for file_path in checked_files:
            basename = os.path.basename(file_path)
            try:
                with open(file_path, "rb") as f:
                    file_data = f.read()
            except OSError as e:
                resolve_errors.append(f"Cannot read {basename}: {e}")
                continue

            pamt_data, entry = self._resolve_file_entry(basename)
            if not pamt_data or not entry:
                resolve_errors.append(
                    f"Cannot locate {basename} in any PAMT index. "
                    f"The file must match a known game file name."
                )
                continue

            group_dir = os.path.basename(os.path.dirname(pamt_data.path))
            modified_list.append(ModifiedFile(
                data=file_data,
                entry=entry,
                pamt_data=pamt_data,
                package_group=group_dir,
            ))

        if resolve_errors:
            error_text = "\n".join(resolve_errors)
            if not modified_list:
                show_error(self, "Resolve Error", f"No files could be resolved:\n{error_text}")
                self._repack_btn.setEnabled(True)
                return
            if not confirm_action(self, "Partial Resolve",
                                  f"{len(resolve_errors)} file(s) could not be resolved:\n"
                                  f"{error_text}\n\nContinue with {len(modified_list)} resolved file(s)?"):
                self._repack_btn.setEnabled(True)
                return

        backup_dir = self._backup_path.text().strip()
        engine = RepackEngine(self._game_path, backup_dir=backup_dir)

        def do_repack(worker, _files=modified_list, _papgt=papgt_path, _engine=engine):
            def progress_cb(pct, msg):
                worker.report_progress(pct, msg)
            return _engine.repack(
                modified_files=_files,
                papgt_path=_papgt,
                create_backup=self._backup_check.isChecked(),
                verify_after=self._verify_check.isChecked(),
                preserve_timestamps=self._timestamp_check.isChecked(),
                progress_callback=progress_cb,
            )

        self._worker = FunctionWorker(do_repack)
        self._worker.progress.connect(lambda p, m: self._progress.set_progress(p, m))
        self._worker.finished_result.connect(self._on_repack_done)
        self._worker.error_occurred.connect(
            lambda e: (show_error(self, "Repack Error", e), self._repack_btn.setEnabled(True))
        )
        self._worker.start()

    def _resolve_file_entry(self, basename):
        """Find the canonical PAMT + entry to patch for a given basename.

        Bug history (2026-05): basename-only lookups returned the FIRST
        matching entry, but shipping PAMTs contain BOTH a shortcut alias
        AND the real nested path for the same basename. The runtime
        loader uses the nested path; patching the alias does nothing
        in-game. Verified on `cd_phm_00_hel_00_0363.pac` — the alias
        sits at ``character/cd_phm_00_hel_00_0363.pac`` while the real
        entry is at ``character/model/1_pc/1_phm/armor/13_hel/...``.

        Fix: scan EVERY group, collect EVERY matching entry across all
        PAMTs, and pick the entry with the LONGEST canonical path. The
        nested entry always has a deeper path than its shortcut, so this
        rule selects the canonical entry by construction.
        """
        candidates: list[tuple[int, "PamtData", "PamtFileEntry"]] = []
        for item in sorted(os.listdir(self._game_path)):
            group_dir = os.path.join(self._game_path, item)
            pamt_path = os.path.join(group_dir, "0.pamt")
            if not os.path.isfile(pamt_path):
                continue
            try:
                pamt_data = parse_pamt(pamt_path, paz_dir=group_dir)
            except Exception as e:
                logger.warning("Error scanning %s: %s", item, e)
                continue
            for entry in find_all_file_entries(pamt_data, basename):
                depth = len(entry.path.replace("\\", "/"))
                candidates.append((depth, pamt_data, entry))

        if not candidates:
            return None, None

        # Deepest path wins — that's the canonical entry the game loads.
        candidates.sort(key=lambda c: -c[0])
        chosen = candidates[0]
        if len(candidates) > 1:
            shadow = ", ".join(
                f"{c[2].path} (depth {c[0]})"
                for c in candidates[1:5]
            )
            logger.info(
                "Resolved %s -> %s (depth %d). Ignored shortcut/alias "
                "entries: %s",
                basename, chosen[2].path, chosen[0], shadow,
            )
        return chosen[1], chosen[2]

    def _preview_resolution(self):
        """Show every selected file's canonical resolution + skipped
        aliases in a dialog. Read-only: never touches game files.
        Helps the user catch wrong-target bugs before patching.
        """
        if not self._game_path or not os.path.isdir(self._game_path):
            show_error(self, "Error",
                       "Game path not set. Return to Game Setup tab "
                       "and load the game first.")
            return

        checked: list[str] = []
        for i in range(self._file_tree.topLevelItemCount()):
            item = self._file_tree.topLevelItem(i)
            if item.checkState(0) == Qt.Checked:
                checked.append(item.data(0, Qt.UserRole))
        if not checked:
            show_error(self, "Error",
                       "No files selected. Tick the files you want "
                       "to preview first.")
            return

        lines: list[str] = []
        n_canonical = n_alias = n_missing = n_dds = 0
        for fpath in checked:
            basename = os.path.basename(fpath)
            cands = self._all_resolve_candidates(basename)
            if not cands:
                lines.append(f"[MISSING] {basename}")
                lines.append("    no PAMT entry found in any group "
                             "— this file cannot be patched.")
                n_missing += 1
                continue
            cands.sort(key=lambda c: -len(c[1].path.replace("\\", "/")))
            chosen_pamt, chosen = cands[0]
            chosen_group = os.path.basename(os.path.dirname(chosen_pamt.path))
            lines.append(f"[OK] {basename}")
            lines.append(
                f"    -> patches {chosen_group} :: {chosen.path}"
            )
            n_canonical += 1
            for pamt_data, entry in cands[1:]:
                grp = os.path.basename(os.path.dirname(pamt_data.path))
                depth = len(entry.path.replace("\\", "/"))
                chosen_depth = len(chosen.path.replace("\\", "/"))
                if depth < chosen_depth:
                    lines.append(
                        f"    skipped (shortcut alias, depth "
                        f"{depth} < {chosen_depth}): "
                        f"{grp} :: {entry.path}"
                    )
                    n_alias += 1
                else:
                    # Same-depth duplicate — could be a real cross-group
                    # alias. Surface it explicitly.
                    lines.append(
                        f"    NOTE another match at same depth in "
                        f"{grp} :: {entry.path}"
                    )
            if basename.lower().endswith(".dds"):
                n_dds += 1

        header = (
            f"Preview: {n_canonical} canonical target(s), "
            f"{n_alias} shortcut alias(es) skipped, "
            f"{n_missing} unresolved.\n"
        )
        if n_dds:
            header += (
                f"WARNING: {n_dds} .dds file(s) selected. CrimsonForge "
                "does NOT yet update meta/0.pathc — newly-added DDS "
                "paths may not load in-game until a future build adds "
                "PATHC handling. Existing DDS replacements are fine.\n"
            )
        header += "\n"
        text = header + "\n".join(lines)

        # Use a scrollable dialog so long lists stay readable.
        from PySide6.QtWidgets import QDialog, QPlainTextEdit
        dlg = QDialog(self)
        dlg.setWindowTitle("Repack Resolution Preview")
        dlg.resize(800, 500)
        v = QVBoxLayout(dlg)
        text_edit = QPlainTextEdit()
        text_edit.setReadOnly(True)
        text_edit.setPlainText(text)
        v.addWidget(text_edit)
        close = QPushButton("Close")
        close.clicked.connect(dlg.accept)
        v.addWidget(close)
        dlg.exec()

    def _all_resolve_candidates(self, basename):
        """Return every (pamt_data, entry) pair across all groups whose
        path or basename matches ``basename``. Used by the dry-run
        preview to show the user which alias entries are being skipped.
        """
        out: list[tuple["PamtData", "PamtFileEntry"]] = []
        for item in sorted(os.listdir(self._game_path)):
            group_dir = os.path.join(self._game_path, item)
            pamt_path = os.path.join(group_dir, "0.pamt")
            if not os.path.isfile(pamt_path):
                continue
            try:
                pamt_data = parse_pamt(pamt_path, paz_dir=group_dir)
            except Exception:
                continue
            for entry in find_all_file_entries(pamt_data, basename):
                out.append((pamt_data, entry))
        return out

    def _on_repack_done(self, result):
        self._repack_btn.setEnabled(True)
        self._paz_crc_label.setText(f"PAZ CRC:  0x{result.paz_crc:08X}")
        self._pamt_crc_label.setText(f"PAMT CRC: 0x{result.pamt_crc:08X}")
        self._papgt_crc_label.setText(f"PAPGT CRC: 0x{result.papgt_crc:08X}")

        if result.success:
            self._progress.set_progress(100, f"Repack complete: {result.files_repacked} files")
            msg = f"Successfully repacked {result.files_repacked} files.\n"
            if result.backup_dir:
                msg += f"Backup: {result.backup_dir}\n"
            msg += (
                f"\nChecksum chain:\n"
                f"  PAZ:   0x{result.paz_crc:08X}\n"
                f"  PAMT:  0x{result.pamt_crc:08X}\n"
                f"  PAPGT: 0x{result.papgt_crc:08X}"
            )
            show_info(self, "Repack Complete", msg)
        else:
            error_text = "\n".join(result.errors) if result.errors else "Unknown error"
            self._progress.set_progress(100, f"Repack failed: {error_text}")
            show_error(self, "Repack Failed", error_text)

    def _restore_backup(self):
        backup_dir = self._backup_path.text().strip()
        if not backup_dir:
            backup_dir = pick_directory(self, "Select Backup Directory")
        if not backup_dir:
            return
        if confirm_action(self, "Restore Backup",
                          "Restore game files from this backup?\n"
                          "This will overwrite current game files."):
            try:
                from core.backup_manager import BackupManager
                bm = BackupManager(backup_dir)
                backups = bm.list_backups()
                if backups:
                    restored = bm.restore_backup(backups[0]["backup_dir"])
                    show_info(self, "Restore Complete", f"Restored {len(restored)} files.")
                else:
                    show_error(self, "Error", "No backups found in the selected directory.")
            except Exception as e:
                show_error(self, "Restore Error", str(e))
