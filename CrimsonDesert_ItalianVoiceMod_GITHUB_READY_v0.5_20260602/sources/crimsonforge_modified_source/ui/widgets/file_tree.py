"""File tree widget for browsing extracted game files."""

import os
from PySide6.QtWidgets import QTreeView, QFileSystemModel, QAbstractItemView
from PySide6.QtCore import Signal, QDir


class FileTreeWidget(QTreeView):
    """File system tree view for browsing extracted files."""

    file_selected = Signal(str)
    file_double_clicked = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._model = QFileSystemModel()
        self._model.setReadOnly(True)
        self.setModel(self._model)

        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setAnimated(True)
        self.setSortingEnabled(True)
        self.setHeaderHidden(False)

        self.hideColumn(1)
        self.hideColumn(2)
        self.hideColumn(3)

        self.clicked.connect(self._on_clicked)
        self.doubleClicked.connect(self._on_double_clicked)

    def set_root_path(self, path: str) -> None:
        """Set the root directory to display."""
        if os.path.isdir(path):
            idx = self._model.setRootPath(path)
            self.setRootIndex(idx)

    def set_name_filters(self, filters: list[str]) -> None:
        """Set file name filters (e.g., ['*.paloc', '*.css', '*.html'])."""
        self._model.setNameFilters(filters)
        self._model.setNameFilterDisables(False)

    def clear_filters(self) -> None:
        """Remove all name filters."""
        self._model.setNameFilters([])

    def _on_clicked(self, index):
        path = self._model.filePath(index)
        if os.path.isfile(path):
            self.file_selected.emit(path)

    def _on_double_clicked(self, index):
        path = self._model.filePath(index)
        if os.path.isfile(path):
            self.file_double_clicked.emit(path)

    def get_selected_path(self) -> str:
        indexes = self.selectedIndexes()
        if indexes:
            return self._model.filePath(indexes[0])
        return ""
