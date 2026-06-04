"""Patch progress dialog - shows step-by-step patching to game."""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QProgressBar, QPushButton,
    QTextEdit, QHBoxLayout,
)
from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QFont


class PatchProgressDialog(QDialog):
    """Modal progress dialog for the patch-to-game pipeline.

    Shows: current step, progress bar, log of operations, and
    a summary when complete.
    """

    cancel_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Patching Game...")
        self.setMinimumSize(550, 400)
        self.setModal(True)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self._setup_ui()
        self._finished = False

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        self._title_label = QLabel("Patching Translation to Game")
        self._title_label.setAlignment(Qt.AlignCenter)
        self._title_label.setStyleSheet("font-size: 16px; font-weight: bold; padding: 8px;")
        layout.addWidget(self._title_label)

        self._step_label = QLabel("Preparing...")
        self._step_label.setStyleSheet("font-size: 13px; padding: 4px;")
        layout.addWidget(self._step_label)

        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setTextVisible(True)
        layout.addWidget(self._progress)

        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setFont(QFont("Courier New", 10))
        self._log.setMaximumHeight(200)
        layout.addWidget(self._log, 1)

        self._summary_label = QLabel("")
        self._summary_label.setAlignment(Qt.AlignCenter)
        self._summary_label.setWordWrap(True)
        self._summary_label.setStyleSheet("padding: 8px;")
        layout.addWidget(self._summary_label)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.clicked.connect(self._on_cancel)
        btn_row.addWidget(self._cancel_btn)
        self._close_btn = QPushButton("Close")
        self._close_btn.clicked.connect(self.accept)
        self._close_btn.setVisible(False)
        btn_row.addWidget(self._close_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

    def set_step(self, step: int, total: int, message: str):
        pct = int((step / total) * 100) if total else 0
        self._step_label.setText(f"Step {step}/{total}: {message}")
        self._progress.setValue(pct)

    def log(self, message: str):
        self._log.append(message)
        sb = self._log.verticalScrollBar()
        sb.setValue(sb.maximum())

    def set_finished_success(self, paz_crc: int, pamt_crc: int, papgt_crc: int, backup_dir: str):
        self._finished = True
        self._progress.setValue(100)
        self._step_label.setText("Patching complete!")
        self._step_label.setStyleSheet("font-size: 13px; padding: 4px; color: #a6e3a1;")
        self._title_label.setText("Patch Applied Successfully")
        self._title_label.setStyleSheet("font-size: 16px; font-weight: bold; padding: 8px; color: #a6e3a1;")
        self._summary_label.setText(
            f"Checksum chain verified:\n"
            f"PAZ: 0x{paz_crc:08X}  |  PAMT: 0x{pamt_crc:08X}  |  PAPGT: 0x{papgt_crc:08X}\n\n"
            f"Backup saved to: {backup_dir}\n\n"
            f"The game is ready to play with your translations!"
        )
        self._cancel_btn.setVisible(False)
        self._close_btn.setVisible(True)
        self._close_btn.setFocus()

    def set_finished_error(self, error: str):
        self._finished = True
        self._progress.setValue(100)
        self._step_label.setText("Patching failed!")
        self._step_label.setStyleSheet("font-size: 13px; padding: 4px; color: #f38ba8;")
        self._title_label.setText("Patch Failed")
        self._title_label.setStyleSheet("font-size: 16px; font-weight: bold; padding: 8px; color: #f38ba8;")
        self._summary_label.setText(f"Error: {error}\n\nOriginal files remain unchanged if backup was created.")
        self._cancel_btn.setVisible(False)
        self._close_btn.setVisible(True)
        self._close_btn.setFocus()

    def _on_cancel(self):
        self.cancel_requested.emit()
        self._cancel_btn.setEnabled(False)
        self._cancel_btn.setText("Cancelling...")

    def closeEvent(self, event):
        if not self._finished:
            event.ignore()
        else:
            event.accept()
