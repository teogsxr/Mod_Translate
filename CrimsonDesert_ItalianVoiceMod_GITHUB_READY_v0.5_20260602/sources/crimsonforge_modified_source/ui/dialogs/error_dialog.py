"""Detailed error display dialog with traceback support."""

import traceback
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QTextEdit, QPushButton, QHBoxLayout,
)
from PySide6.QtGui import QFont
from PySide6.QtCore import Qt


class ErrorDialog(QDialog):
    """Error dialog that shows the error message and optional traceback."""

    def __init__(self, title: str, message: str, exception: Exception = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumSize(500, 300)

        layout = QVBoxLayout(self)

        msg_label = QLabel(message)
        msg_label.setWordWrap(True)
        msg_label.setStyleSheet("font-size: 13px; padding: 8px;")
        layout.addWidget(msg_label)

        if exception:
            tb_text = "".join(traceback.format_exception(type(exception), exception, exception.__traceback__))
            self._detail = QTextEdit()
            self._detail.setReadOnly(True)
            self._detail.setFont(QFont("Courier New", 10))
            self._detail.setPlainText(tb_text)
            layout.addWidget(self._detail, 1)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)
