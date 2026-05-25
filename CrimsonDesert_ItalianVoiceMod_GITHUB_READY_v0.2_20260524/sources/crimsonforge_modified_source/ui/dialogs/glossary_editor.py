"""Glossary editor dialog for managing proper noun translations.

Displays all glossary entries in a table with editable translation fields,
category dropdowns, filtering, and AI batch translate.
"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QComboBox, QLineEdit,
    QHeaderView, QAbstractItemView, QFileDialog, QProgressBar,
    QApplication,
)
from PySide6.QtCore import Qt, Signal

from translation.glossary_manager import (
    GlossaryManager, GlossaryEntry, GlossaryCategory, CATEGORY_LABELS,
)
from utils.logger import get_logger

logger = get_logger("ui.glossary_editor")


class GlossaryEditorDialog(QDialog):
    """Modal dialog for editing the translation glossary with AI translate."""

    def __init__(self, glossary_mgr: GlossaryManager,
                 ai_translate_fn=None, parent=None):
        """
        Args:
            glossary_mgr: The glossary manager instance.
            ai_translate_fn: Callback(term, source_lang, target_lang) -> str.
                Called for each entry during AI translate. Returns translated text.
        """
        super().__init__(parent)
        self._glossary = glossary_mgr
        self._ai_translate_fn = ai_translate_fn
        self._stop_ai = False
        self.setWindowTitle(
            f"Glossary Editor — {glossary_mgr.translated_count}/{glossary_mgr.entry_count} translated"
        )
        self.setMinimumSize(950, 650)
        self.setModal(True)
        self._setup_ui()
        self._populate()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Filter:"))
        self._cat_filter = QComboBox()
        self._cat_filter.addItem("All Categories", "")
        self._cat_filter.addItem("Untranslated Only", "__untranslated__")
        for cat, label in CATEGORY_LABELS.items():
            self._cat_filter.addItem(label, cat.value)
        self._cat_filter.currentIndexChanged.connect(self._populate)
        filter_row.addWidget(self._cat_filter)

        filter_row.addWidget(QLabel("Search:"))
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search terms...")
        self._search.textChanged.connect(self._populate)
        filter_row.addWidget(self._search, 1)

        self._count_label = QLabel("")
        filter_row.addWidget(self._count_label)
        layout.addLayout(filter_row)

        self._table = QTableWidget()
        self._table.setColumnCount(5)
        self._table.setHorizontalHeaderLabels(["Term", "Translation", "Category", "Mentions", "Locked"])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Fixed)
        self._table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Fixed)
        self._table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Fixed)
        self._table.setColumnWidth(2, 120)
        self._table.setColumnWidth(3, 70)
        self._table.setColumnWidth(4, 60)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._table.setAlternatingRowColors(True)
        self._table.setSortingEnabled(True)
        self._table.cellChanged.connect(self._on_cell_changed)
        layout.addWidget(self._table, 1)

        self._progress = QProgressBar()
        self._progress.setFixedHeight(16)
        self._progress.setTextVisible(True)
        self._progress.setVisible(False)
        layout.addWidget(self._progress)

        btn_row = QHBoxLayout()

        ai_selected_btn = QPushButton("AI Translate Selected")
        ai_selected_btn.setObjectName("primary")
        ai_selected_btn.setToolTip("Translate selected entries with AI")
        ai_selected_btn.clicked.connect(self._ai_translate_selected)
        btn_row.addWidget(ai_selected_btn)

        ai_all_btn = QPushButton("AI Translate All Untranslated")
        ai_all_btn.setToolTip("Translate all entries that have no translation yet")
        ai_all_btn.clicked.connect(self._ai_translate_all)
        btn_row.addWidget(ai_all_btn)

        self._stop_btn = QPushButton("Stop")
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self._stop_ai_translate)
        btn_row.addWidget(self._stop_btn)

        btn_row.addWidget(QLabel("|"))

        save_btn = QPushButton("Save")
        save_btn.clicked.connect(self._save)
        btn_row.addWidget(save_btn)

        export_btn = QPushButton("Export JSON")
        export_btn.clicked.connect(self._export)
        btn_row.addWidget(export_btn)

        import_btn = QPushButton("Import JSON")
        import_btn.clicked.connect(self._import)
        btn_row.addWidget(import_btn)

        btn_row.addStretch()

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self._on_close)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

    def _populate(self):
        self._table.blockSignals(True)
        self._table.setRowCount(0)

        cat_value = self._cat_filter.currentData()
        search = self._search.text().strip().lower()

        entries = self._glossary.entries
        if cat_value == "__untranslated__":
            entries = [e for e in entries if not e.translation and e.category != GlossaryCategory.SKIP]
        elif cat_value:
            try:
                cat = GlossaryCategory(cat_value)
                entries = [e for e in entries if e.category == cat]
            except ValueError:
                pass
        if search:
            entries = [e for e in entries if search in e.term.lower() or search in e.translation.lower()]

        self._table.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            term_item = QTableWidgetItem(entry.term)
            term_item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
            term_item.setData(Qt.UserRole, entry.term)
            self._table.setItem(row, 0, term_item)

            trans_item = QTableWidgetItem(entry.translation)
            self._table.setItem(row, 1, trans_item)

            cat_combo = QComboBox()
            for cat, label in CATEGORY_LABELS.items():
                cat_combo.addItem(label, cat.value)
            cat_combo.setCurrentText(CATEGORY_LABELS.get(entry.category, "Other"))
            cat_combo.currentIndexChanged.connect(lambda _, r=row: self._on_category_changed(r))
            self._table.setCellWidget(row, 2, cat_combo)

            mentions_item = QTableWidgetItem()
            mentions_item.setData(Qt.DisplayRole, entry.mentions)
            mentions_item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
            self._table.setItem(row, 3, mentions_item)

            lock_item = QTableWidgetItem("Y" if entry.locked else "")
            lock_item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
            self._table.setItem(row, 4, lock_item)

        self._update_counts()
        self._table.blockSignals(False)

    def _update_counts(self):
        tc = self._glossary.translated_count
        total = self._glossary.entry_count
        shown = self._table.rowCount()
        self._count_label.setText(f"{shown} shown | {tc}/{total} translated")
        self.setWindowTitle(f"Glossary Editor — {tc}/{total} translated")

    def _on_cell_changed(self, row, col):
        if col != 1:
            return
        term_item = self._table.item(row, 0)
        if not term_item:
            return
        term = term_item.data(Qt.UserRole)
        trans = self._table.item(row, 1).text()
        self._glossary.set_translation(term, trans)
        self._update_counts()

    def _on_category_changed(self, row):
        term_item = self._table.item(row, 0)
        if not term_item:
            return
        term = term_item.data(Qt.UserRole)
        combo = self._table.cellWidget(row, 2)
        if combo:
            try:
                cat = GlossaryCategory(combo.currentData())
                self._glossary.set_category(term, cat)
            except ValueError:
                pass

    def _ai_translate_selected(self):
        """AI translate only selected rows."""
        if not self._ai_translate_fn:
            return
        indexes = self._table.selectionModel().selectedRows()
        if not indexes:
            return
        terms = []
        for idx in indexes:
            term_item = self._table.item(idx.row(), 0)
            if term_item:
                term = term_item.data(Qt.UserRole)
                entry = self._glossary.get_entry(term)
                if entry and not entry.locked:
                    terms.append((idx.row(), entry))
        self._do_ai_translate(terms)

    def _ai_translate_all(self):
        """AI translate all untranslated entries."""
        if not self._ai_translate_fn:
            return
        terms = []
        for row in range(self._table.rowCount()):
            term_item = self._table.item(row, 0)
            if not term_item:
                continue
            term = term_item.data(Qt.UserRole)
            entry = self._glossary.get_entry(term)
            if entry and not entry.translation and not entry.locked and entry.category != GlossaryCategory.SKIP:
                terms.append((row, entry))
        self._do_ai_translate(terms)

    def _do_ai_translate(self, terms: list[tuple[int, GlossaryEntry]]):
        """Run AI translation on a list of (row, entry) pairs."""
        if not terms:
            return
        self._stop_ai = False
        self._stop_btn.setEnabled(True)
        self._progress.setVisible(True)
        self._progress.setMaximum(len(terms))
        self._progress.setValue(0)
        self._progress.setFormat(f"Translating 0/{len(terms)}...")

        translated = 0
        for i, (row, entry) in enumerate(terms):
            if self._stop_ai:
                break

            QApplication.processEvents()

            try:
                result = self._ai_translate_fn(entry.term)
                if result:
                    entry.translation = result
                    trans_item = self._table.item(row, 1)
                    if trans_item:
                        self._table.blockSignals(True)
                        trans_item.setText(result)
                        self._table.blockSignals(False)
                    translated += 1
            except Exception as e:
                logger.error("Glossary AI translate failed for '%s': %s", entry.term, e)

            self._progress.setValue(i + 1)
            self._progress.setFormat(f"Translating {i + 1}/{len(terms)}... ({translated} done)")

        self._stop_btn.setEnabled(False)
        self._progress.setFormat(f"Done: {translated}/{len(terms)} translated")
        self._glossary.save()
        self._update_counts()

    def _stop_ai_translate(self):
        self._stop_ai = True

    def _save(self):
        self._glossary.save()
        self._update_counts()

    def _export(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export Glossary", "", "JSON Files (*.json)")
        if path:
            self._glossary.export_json(path)

    def _import(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import Glossary", "", "JSON Files (*.json)")
        if path:
            count = self._glossary.import_json(path)
            self._populate()
            self._count_label.setText(f"Imported {count} entries")

    def _on_close(self):
        self._glossary.save()
        self.accept()
