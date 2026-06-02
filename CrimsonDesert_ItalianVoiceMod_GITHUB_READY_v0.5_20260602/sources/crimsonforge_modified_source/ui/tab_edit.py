"""Edit tab - text editor with syntax highlighting for game files."""

import os
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QComboBox,
)
from PySide6.QtCore import Signal

from core.file_detector import get_syntax_type, is_text_file
from ui.widgets.syntax_editor import SyntaxEditor
from ui.dialogs.file_picker import pick_file, pick_save_file
from ui.dialogs.confirmation import show_error, show_info, confirm_action
from utils.logger import get_logger

logger = get_logger("ui.tab_edit")


class EditTab(QWidget):
    """Tab for editing text files with syntax highlighting."""

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self._config = config
        self._current_file = ""
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        top_row = QHBoxLayout()
        top_row.addWidget(QLabel("File:"))
        self._file_label = QLineEdit()
        self._file_label.setReadOnly(True)
        self._file_label.setPlaceholderText("No file opened")
        top_row.addWidget(self._file_label, 1)
        open_btn = QPushButton("Open...")
        open_btn.clicked.connect(self._open_file)
        top_row.addWidget(open_btn)
        self._save_btn = QPushButton("Save")
        self._save_btn.setObjectName("primary")
        self._save_btn.clicked.connect(self._save_file)
        top_row.addWidget(self._save_btn)
        save_as_btn = QPushButton("Save As...")
        save_as_btn.clicked.connect(self._save_as)
        top_row.addWidget(save_as_btn)
        layout.addLayout(top_row)

        self._editor = SyntaxEditor()
        layout.addWidget(self._editor, 1)

        bottom_row = QHBoxLayout()
        bottom_row.addWidget(QLabel("Syntax:"))
        self._syntax_combo = QComboBox()
        self._syntax_combo.addItems(["plain", "css", "html", "xml", "json", "paloc"])
        self._syntax_combo.currentTextChanged.connect(self._on_syntax_changed)
        bottom_row.addWidget(self._syntax_combo)

        bottom_row.addWidget(QLabel("Encoding:"))
        self._encoding_combo = QComboBox()
        self._encoding_combo.addItems(["utf-8", "utf-16", "latin-1", "ascii"])
        bottom_row.addWidget(self._encoding_combo)

        self._status_label = QLabel("Ready")
        bottom_row.addStretch()
        bottom_row.addWidget(self._status_label)
        layout.addLayout(bottom_row)

    def _open_file(self):
        path = pick_file(
            self, "Open File", "",
            "Text Files (*.css *.html *.thtml *.xml *.json *.paloc *.txt);;All Files (*.*)"
        )
        if path:
            self.open_file(path)

    def open_file(self, path: str):
        """Open a file in the editor (called from Browse tab too)."""
        if not os.path.isfile(path):
            show_error(self, "Error", f"File not found: {path}")
            return

        if self._editor.modified:
            if not confirm_action(self, "Unsaved Changes",
                                  "Current file has unsaved changes. Discard them?"):
                return

        try:
            encoding = self._encoding_combo.currentText()
            self._editor.load_file(path, encoding)
            self._current_file = path
            self._file_label.setText(path)

            syntax = get_syntax_type(path)
            self._syntax_combo.setCurrentText(syntax)
            self._editor.set_syntax(syntax)
            self._editor.modified = False
            self._status_label.setText(f"Opened: {os.path.basename(path)}")
        except Exception as e:
            show_error(self, "Open Error", f"Failed to open {path}: {e}")

    def _save_file(self):
        if not self._current_file:
            self._save_as()
            return
        try:
            encoding = self._encoding_combo.currentText()
            self._editor.save_file(self._current_file, encoding)
            self._status_label.setText(f"Saved: {os.path.basename(self._current_file)}")
        except Exception as e:
            show_error(self, "Save Error", f"Failed to save: {e}")

    def _save_as(self):
        path = pick_save_file(self, "Save As", self._current_file or "")
        if path:
            try:
                encoding = self._encoding_combo.currentText()
                self._editor.save_file(path, encoding)
                self._current_file = path
                self._file_label.setText(path)
                self._status_label.setText(f"Saved: {os.path.basename(path)}")
            except Exception as e:
                show_error(self, "Save Error", f"Failed to save: {e}")

    def _on_syntax_changed(self, syntax: str):
        self._editor.set_syntax(syntax)
