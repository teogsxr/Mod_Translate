"""Browse tab - file tree with multi-format preview pane."""

import os
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QSplitter,
)
from PySide6.QtCore import Qt, Signal

from ui.widgets.file_tree import FileTreeWidget
from ui.widgets.preview_pane import PreviewPane
from ui.dialogs.file_picker import pick_directory
from utils.logger import get_logger

logger = get_logger("ui.tab_browse")


class BrowseTab(QWidget):
    """Tab for browsing extracted game files with preview."""

    file_open_requested = Signal(str)

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self._config = config
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        path_row = QHBoxLayout()
        path_row.addWidget(QLabel("Root:"))
        self._root_path = QLineEdit(self._config.get("general.last_output_path", ""))
        self._root_path.setPlaceholderText("Select extracted files directory...")
        path_row.addWidget(self._root_path, 1)
        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(self._browse)
        path_row.addWidget(browse_btn)
        open_btn = QPushButton("Open in Editor")
        open_btn.clicked.connect(self._open_in_editor)
        path_row.addWidget(open_btn)
        layout.addLayout(path_row)

        splitter = QSplitter(Qt.Horizontal)

        self._file_tree = FileTreeWidget()
        self._file_tree.file_selected.connect(self._on_file_selected)
        self._file_tree.file_double_clicked.connect(self._on_file_double_clicked)
        splitter.addWidget(self._file_tree)

        self._preview = PreviewPane()
        splitter.addWidget(self._preview)

        splitter.setSizes([300, 700])
        layout.addWidget(splitter, 1)

    def _browse(self):
        path = pick_directory(self, "Select Directory to Browse")
        if path:
            self._root_path.setText(path)
            self._file_tree.set_root_path(path)

    def set_root_path(self, path: str):
        """Set the browse root directory (called from other tabs)."""
        self._root_path.setText(path)
        self._file_tree.set_root_path(path)

    def _on_file_selected(self, path: str):
        self._preview.preview_file(path)

    def _on_file_double_clicked(self, path: str):
        self.file_open_requested.emit(path)

    def _open_in_editor(self):
        path = self._file_tree.get_selected_path()
        if path:
            self.file_open_requested.emit(path)
