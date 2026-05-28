"""File and directory picker dialogs."""

from PySide6.QtWidgets import QFileDialog


def pick_directory(parent, title: str = "Select Directory", start_dir: str = "") -> str:
    """Show a directory picker dialog. Returns path or empty string if cancelled."""
    return QFileDialog.getExistingDirectory(parent, title, start_dir)


def pick_file(parent, title: str = "Select File", start_dir: str = "",
              filters: str = "All Files (*.*)") -> str:
    """Show a file picker dialog. Returns path or empty string if cancelled."""
    path, _ = QFileDialog.getOpenFileName(parent, title, start_dir, filters)
    return path


def pick_save_file(parent, title: str = "Save File", start_dir: str = "",
                   filters: str = "All Files (*.*)") -> str:
    """Show a save file dialog. Returns path or empty string if cancelled."""
    path, _ = QFileDialog.getSaveFileName(parent, title, start_dir, filters)
    return path
