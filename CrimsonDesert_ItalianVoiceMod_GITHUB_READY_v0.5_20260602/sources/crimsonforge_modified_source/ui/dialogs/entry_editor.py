"""Entry editor dialog - double-click to edit a single translation entry.

Shows original baseline text, current source text, editable translation field,
status, AI button with current provider info, and metadata.
"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QTextEdit,
    QPushButton, QComboBox, QGroupBox, QFormLayout,
)
from PySide6.QtCore import Signal

from translation.translation_state import TranslationEntry, StringStatus


STATUS_LABELS = {
    StringStatus.PENDING: "Pending",
    StringStatus.TRANSLATED: "Translated",
    StringStatus.REVIEWED: "Reviewed",
    StringStatus.APPROVED: "Approved",
}


class EntryEditorDialog(QDialog):
    """Modal dialog for editing a single translation entry."""

    ai_requested = Signal(int)
    entry_saved = Signal(int, str, str)

    def __init__(self, entry: TranslationEntry, baseline_text: str = None, provider_name: str = "",
                 model_name: str = "", parent=None):
        super().__init__(parent)
        self._entry = entry
        self._baseline_text = baseline_text
        self._provider_name = provider_name
        self._model_name = model_name
        self.setWindowTitle(f"Edit Entry #{entry.index + 1} - {entry.key}")
        self.setMinimumSize(750, 550)
        self.setModal(True)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        info_layout = QFormLayout()
        info_layout.addRow("Key:", QLabel(self._entry.key))
        info_layout.addRow("Index:", QLabel(str(self._entry.index + 1)))
        if self._provider_name:
            info_layout.addRow("AI Provider:", QLabel(
                f"{self._provider_name} / {self._model_name}" if self._model_name
                else self._provider_name
            ))
        layout.addLayout(info_layout)

        if self._baseline_text is not None:
            baseline_group = QGroupBox("Original Baseline (immutable)")
            baseline_layout = QVBoxLayout(baseline_group)
            baseline_edit = QTextEdit()
            baseline_edit.setReadOnly(True)
            baseline_edit.setPlainText(self._baseline_text)
            baseline_edit.setMaximumHeight(100)
            baseline_edit.setStyleSheet("background-color: #1e1e2e; color: #6c7086;")
            baseline_layout.addWidget(baseline_edit)
            layout.addWidget(baseline_group)

        orig_group = QGroupBox("Source Text (current game)")
        orig_layout = QVBoxLayout(orig_group)
        self._orig_text = QTextEdit()
        self._orig_text.setReadOnly(True)
        self._orig_text.setPlainText(self._entry.original_text)
        self._orig_text.setMaximumHeight(120)
        orig_layout.addWidget(self._orig_text)
        layout.addWidget(orig_group)

        trans_group = QGroupBox("Translation")
        trans_layout = QVBoxLayout(trans_group)
        self._trans_text = QTextEdit()
        self._trans_text.setPlainText(self._entry.translated_text)
        self._trans_text.textChanged.connect(self._on_text_changed)
        trans_layout.addWidget(self._trans_text)
        layout.addWidget(trans_group, 1)

        status_row = QHBoxLayout()
        status_row.addWidget(QLabel("Status:"))
        self._status_combo = QComboBox()
        self._status_combo.addItems(list(STATUS_LABELS.values()))
        self._status_combo.setCurrentText(STATUS_LABELS.get(self._entry.status, "Pending"))
        status_row.addWidget(self._status_combo)
        status_row.addStretch()

        ai_label = ""
        if self._provider_name:
            ai_label = f"Translate with AI ({self._provider_name})"
        else:
            ai_label = "Translate with AI"
        ai_btn = QPushButton(ai_label)
        ai_btn.clicked.connect(self._on_ai)
        status_row.addWidget(ai_btn)

        if self._baseline_text is not None:
            revert_btn = QPushButton("Revert to Original")
            revert_btn.setToolTip("Clear translation and revert to baseline text")
            revert_btn.clicked.connect(self._on_revert)
            status_row.addWidget(revert_btn)

        layout.addLayout(status_row)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        save_btn = QPushButton("Save")
        save_btn.setObjectName("primary")
        save_btn.clicked.connect(self._on_save)
        btn_row.addWidget(save_btn)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

    def _on_text_changed(self):
        """Auto-update status combo when translation text changes."""
        text = self._trans_text.toPlainText().strip()
        current_status = self._status_combo.currentText()
        if text and current_status == "Pending":
            self._status_combo.setCurrentText("Translated")
        elif not text and current_status != "Pending":
            self._status_combo.setCurrentText("Pending")

    def _on_ai(self):
        self.ai_requested.emit(self._entry.index)

    def _on_revert(self):
        if self._baseline_text is not None:
            self._trans_text.setPlainText("")
            self._status_combo.setCurrentText("Pending")

    def update_translation(self, text: str):
        """Called externally after AI translation completes."""
        self._trans_text.setPlainText(text)
        self._status_combo.setCurrentText("Translated")

    def _on_save(self):
        new_text = self._trans_text.toPlainText()
        new_status = self._status_combo.currentText()
        # Auto-promote Pending -> Translated if text was entered
        if new_text.strip() and new_status == "Pending":
            new_status = "Translated"
        # Auto-revert to Pending if text was cleared
        if not new_text.strip() and new_status != "Pending":
            new_status = "Pending"
        self.entry_saved.emit(self._entry.index, new_text, new_status)
        self.accept()
