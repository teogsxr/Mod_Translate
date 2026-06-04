"""Progress bar with status message widget."""

from PySide6.QtWidgets import QWidget, QHBoxLayout, QProgressBar, QLabel
from PySide6.QtCore import Qt


class ProgressWidget(QWidget):
    """Combined progress bar and status message."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._status_label = QLabel("Ready")
        self._status_label.setMinimumWidth(120)
        layout.addWidget(self._status_label)

        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_bar.setTextVisible(True)
        layout.addWidget(self._progress_bar, 1)

        self._detail_label = QLabel("")
        self._detail_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._detail_label.setMinimumWidth(100)
        layout.addWidget(self._detail_label)

    def set_progress(self, value: int, status: str = "", detail: str = "") -> None:
        self._progress_bar.setValue(value)
        if status:
            self._status_label.setText(status)
        if detail:
            self._detail_label.setText(detail)

    def set_status(self, text: str) -> None:
        self._status_label.setText(text)

    def set_detail(self, text: str) -> None:
        self._detail_label.setText(text)

    def reset(self) -> None:
        self._progress_bar.setValue(0)
        self._status_label.setText("Ready")
        self._detail_label.setText("")

    def set_indeterminate(self, status: str = "Processing...") -> None:
        self._progress_bar.setRange(0, 0)
        self._status_label.setText(status)

    def set_determinate(self) -> None:
        self._progress_bar.setRange(0, 100)
