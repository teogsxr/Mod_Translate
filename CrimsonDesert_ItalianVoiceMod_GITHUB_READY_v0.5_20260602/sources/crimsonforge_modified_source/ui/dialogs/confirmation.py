"""Confirmation, error, and info dialog utilities.

Provides convenience functions used throughout the UI for
user notifications and confirmations.
"""

from PySide6.QtWidgets import QMessageBox


def show_error(parent, title: str, message: str) -> None:
    """Display an error message dialog."""
    QMessageBox.critical(parent, title, message)


def show_info(parent, title: str, message: str) -> None:
    """Display an information message dialog."""
    QMessageBox.information(parent, title, message)


def show_warning(parent, title: str, message: str) -> None:
    """Display a warning message dialog."""
    QMessageBox.warning(parent, title, message)


def confirm_action(parent, title: str, message: str) -> bool:
    """Show a Yes/No confirmation dialog. Returns True if user clicks Yes."""
    result = QMessageBox.question(
        parent,
        title,
        message,
        QMessageBox.Yes | QMessageBox.No,
        QMessageBox.No,
    )
    return result == QMessageBox.Yes
