"""Unpack tab - select package group and extract files.

Game path is set by the main window on startup. This tab only needs
to select which package group and files to extract.
"""

import os
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QComboBox, QTreeWidget, QTreeWidgetItem, QCheckBox, QHeaderView,
    QAbstractItemView,
)
from PySide6.QtCore import Qt, Signal

from core.vfs_manager import VfsManager
from core.pamt_parser import PamtData, PamtFileEntry
from core.file_detector import detect_file_type
from ui.widgets.progress_widget import ProgressWidget
from ui.dialogs.file_picker import pick_directory
from ui.dialogs.confirmation import show_error, show_info
from utils.thread_worker import FunctionWorker
from utils.platform_utils import format_file_size
from utils.logger import get_logger

logger = get_logger("ui.tab_unpack")


class UnpackTab(QWidget):
    """Tab for extracting files from PAZ archives."""

    files_extracted = Signal(str)

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self._config = config
        self._vfs: VfsManager = None
        self._pamt_data: PamtData = None
        self._worker: FunctionWorker = None
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        top_row = QHBoxLayout()
        top_row.addWidget(QLabel("Package Group:"))
        self._group_combo = QComboBox()
        self._group_combo.setMinimumWidth(200)
        self._group_combo.currentTextChanged.connect(self._on_group_changed)
        top_row.addWidget(self._group_combo)
        top_row.addWidget(QLabel("Filter:"))
        self._filter_combo = QComboBox()
        self._filter_combo.addItems(["*.*", "*.paloc", "*.css", "*.html", "*.thtml", "*.xml", "*.ttf", "*.otf"])
        self._filter_combo.setEditable(True)
        self._filter_combo.currentTextChanged.connect(self._apply_filter)
        top_row.addWidget(self._filter_combo)
        top_row.addStretch()
        layout.addLayout(top_row)

        output_row = QHBoxLayout()
        output_row.addWidget(QLabel("Output Dir:"))
        self._output_path = QLineEdit(self._config.get("general.last_output_path", ""))
        self._output_path.setPlaceholderText("Select output directory for extracted files...")
        output_row.addWidget(self._output_path, 1)
        out_browse = QPushButton("Browse...")
        out_browse.clicked.connect(self._browse_output)
        output_row.addWidget(out_browse)
        layout.addLayout(output_row)

        self._file_tree = QTreeWidget()
        self._file_tree.setHeaderLabels(["", "File", "Size", "Compressed", "Type"])
        self._file_tree.setUniformRowHeights(True)
        self._file_tree.header().setSectionResizeMode(1, QHeaderView.Stretch)
        self._file_tree.setAlternatingRowColors(True)
        self._file_tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        layout.addWidget(self._file_tree, 1)

        btn_row = QHBoxLayout()
        select_all = QPushButton("Select All")
        select_all.clicked.connect(self._select_all)
        btn_row.addWidget(select_all)
        deselect_all = QPushButton("Deselect All")
        deselect_all.clicked.connect(self._deselect_all)
        btn_row.addWidget(deselect_all)
        btn_row.addStretch()
        self._extract_btn = QPushButton("Extract Selected")
        self._extract_btn.setObjectName("primary")
        self._extract_btn.clicked.connect(self._extract_selected)
        btn_row.addWidget(self._extract_btn)
        extract_all_btn = QPushButton("Extract All")
        extract_all_btn.clicked.connect(self._extract_all)
        btn_row.addWidget(extract_all_btn)
        layout.addLayout(btn_row)

        self._progress = ProgressWidget()
        layout.addWidget(self._progress)

    def initialize_from_game(self, vfs: VfsManager, groups: list[str]) -> None:
        """Called by main_window after game is loaded. No manual browse needed."""
        self._vfs = vfs
        self._group_combo.clear()
        self._group_combo.addItems(groups)
        self._progress.set_status(f"Game loaded: {len(groups)} package groups")

    def _browse_output(self):
        path = pick_directory(self, "Select Output Directory")
        if path:
            self._output_path.setText(path)

    def _on_group_changed(self, group: str):
        if not self._vfs or not group:
            return
        try:
            self._pamt_data = self._vfs.load_pamt(group)
            self._populate_file_tree()
        except Exception as e:
            show_error(self, "PAMT Error", str(e))

    def _populate_file_tree(self):
        self._file_tree.clear()
        if not self._pamt_data:
            return

        filter_pattern = self._filter_combo.currentText().strip()
        entries = self._pamt_data.file_entries

        if filter_pattern and filter_pattern != "*.*":
            import fnmatch
            entries = [e for e in entries
                       if fnmatch.fnmatch(os.path.basename(e.path).lower(), filter_pattern.lower())]

        for entry in entries:
            item = QTreeWidgetItem()
            item.setCheckState(0, Qt.Checked)
            item.setText(1, entry.path)
            item.setText(2, format_file_size(entry.orig_size))
            comp_type = "LZ4" if entry.compression_type == 2 else ("zlib" if entry.compression_type == 4 else "None")
            enc = "+ChaCha20" if entry.encrypted else ""
            item.setText(3, f"{comp_type}{enc}")
            file_info = detect_file_type(entry.path)
            item.setText(4, file_info.description)
            item.setData(0, Qt.UserRole, entry)
            self._file_tree.addTopLevelItem(item)

        self._progress.set_status(f"{self._file_tree.topLevelItemCount()} files")

    def _apply_filter(self):
        self._populate_file_tree()

    def _select_all(self):
        for i in range(self._file_tree.topLevelItemCount()):
            self._file_tree.topLevelItem(i).setCheckState(0, Qt.Checked)

    def _deselect_all(self):
        for i in range(self._file_tree.topLevelItemCount()):
            self._file_tree.topLevelItem(i).setCheckState(0, Qt.Unchecked)

    def _get_checked_entries(self) -> list[PamtFileEntry]:
        entries = []
        for i in range(self._file_tree.topLevelItemCount()):
            item = self._file_tree.topLevelItem(i)
            if item.checkState(0) == Qt.Checked:
                entry = item.data(0, Qt.UserRole)
                if entry:
                    entries.append(entry)
        return entries

    def _extract_selected(self):
        entries = self._get_checked_entries()
        if not entries:
            show_error(self, "Error", "No files selected for extraction.")
            return
        self._do_extract(entries)

    def _extract_all(self):
        entries = []
        for i in range(self._file_tree.topLevelItemCount()):
            entry = self._file_tree.topLevelItem(i).data(0, Qt.UserRole)
            if entry:
                entries.append(entry)
        if not entries:
            show_error(self, "Error", "No files to extract.")
            return
        self._do_extract(entries)

    def _do_extract(self, entries: list[PamtFileEntry]):
        output = self._output_path.text().strip()
        if not output:
            show_error(self, "Error", "Select an output directory first.")
            return

        self._config.set("general.last_output_path", output)
        self._config.save()
        self._extract_btn.setEnabled(False)

        def extract_work(worker, _entries=entries, _output=output):
            total = len(_entries)
            results = {"extracted": 0, "errors": 0, "decrypted": 0, "decompressed": 0}
            for i, entry in enumerate(_entries):
                if worker.is_cancelled():
                    break
                try:
                    result = self._vfs.extract_entry(entry, _output)
                    results["extracted"] += 1
                    if result.get("decrypted"):
                        results["decrypted"] += 1
                    if result.get("decompressed"):
                        results["decompressed"] += 1
                except Exception as e:
                    results["errors"] += 1
                    logger.error("Extract error: %s - %s", entry.path, e)
                pct = int(((i + 1) / total) * 100)
                worker.report_progress(pct, f"Extracting {os.path.basename(entry.path)}...")
            return results

        self._worker = FunctionWorker(extract_work)
        self._worker.progress.connect(lambda p, m: self._progress.set_progress(p, m))
        self._worker.finished_result.connect(self._on_extract_done)
        self._worker.error_occurred.connect(lambda e: show_error(self, "Extract Error", e))
        self._worker.start()

    def _on_extract_done(self, results):
        self._extract_btn.setEnabled(True)
        msg = f"Extracted {results['extracted']} files"
        if results["decrypted"]:
            msg += f", {results['decrypted']} decrypted"
        if results["decompressed"]:
            msg += f", {results['decompressed']} decompressed"
        if results["errors"]:
            msg += f", {results['errors']} errors"
        self._progress.set_progress(100, msg)
        show_info(self, "Extraction Complete", msg)
        self.files_extracted.emit(self._output_path.text().strip())
