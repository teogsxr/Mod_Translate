"""Reusable search box with persisted recent-history popup."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


class _SearchHistoryPopup(QFrame):
    """Popup list that shows recent search entries with delete buttons."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint)
        self.setObjectName("searchHistoryPopup")
        self.setStyleSheet(
            """
            QFrame#searchHistoryPopup {
                background-color: #11111b;
                border: 1px solid #313244;
                border-radius: 8px;
            }
            QPushButton#searchHistoryItem {
                background: transparent;
                border: none;
                color: #cdd6f4;
                padding: 6px 8px;
                text-align: left;
            }
            QPushButton#searchHistoryItem:hover {
                background-color: #1e1e2e;
                border-radius: 6px;
            }
            QToolButton#searchHistoryDelete {
                background: transparent;
                border: none;
                color: #a6adc8;
                padding: 4px 6px;
            }
            QToolButton#searchHistoryDelete:hover {
                background-color: #302d41;
                color: #f38ba8;
                border-radius: 6px;
            }
            """
        )
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(6, 6, 6, 6)
        self._layout.setSpacing(2)

    def rebuild(self, entries: list[str], on_select, on_delete) -> None:
        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        for text in entries:
            row = QWidget(self)
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(4)

            item_btn = QPushButton(text, row)
            item_btn.setObjectName("searchHistoryItem")
            item_btn.setFlat(True)
            item_btn.setCursor(Qt.PointingHandCursor)
            item_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            item_btn.clicked.connect(lambda _checked=False, value=text: on_select(value))
            row_layout.addWidget(item_btn, 1)

            delete_btn = QToolButton(row)
            delete_btn.setObjectName("searchHistoryDelete")
            delete_btn.setText("x")
            delete_btn.setCursor(Qt.PointingHandCursor)
            delete_btn.clicked.connect(lambda _checked=False, value=text: on_delete(value))
            row_layout.addWidget(delete_btn)

            self._layout.addWidget(row)


class SearchHistoryLineEdit(QLineEdit):
    """QLineEdit with per-field persisted search history."""

    MAX_HISTORY = 10

    def __init__(self, config=None, history_key: str = "", parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._config = config
        self._history_key = history_key.strip() or "default"
        self._popup = _SearchHistoryPopup(self)
        self.returnPressed.connect(self.record_current_search)
        self.textEdited.connect(self._on_text_edited)

    def history(self) -> list[str]:
        if self._config is None:
            return []
        raw = self._config.get(f"ui.search_history.{self._history_key}", [])
        if not isinstance(raw, list):
            return []
        result: list[str] = []
        seen: set[str] = set()
        for item in raw:
            if not isinstance(item, str):
                continue
            text = item.strip()
            if not text:
                continue
            folded = text.casefold()
            if folded in seen:
                continue
            seen.add(folded)
            result.append(text)
            if len(result) >= self.MAX_HISTORY:
                break
        return result

    def set_history(self, entries: list[str]) -> None:
        if self._config is None:
            return
        normalized: list[str] = []
        seen: set[str] = set()
        for item in entries:
            text = str(item).strip()
            if not text:
                continue
            folded = text.casefold()
            if folded in seen:
                continue
            seen.add(folded)
            normalized.append(text)
            if len(normalized) >= self.MAX_HISTORY:
                break
        self._config.set(f"ui.search_history.{self._history_key}", normalized)
        self._config.save()
        if self._popup.isVisible():
            self.show_history_popup()

    def record_current_search(self) -> None:
        text = self.text().strip()
        if not text:
            return
        existing = [item for item in self.history() if item.casefold() != text.casefold()]
        self.set_history([text, *existing])

    def remove_history_entry(self, text: str) -> None:
        target = text.strip().casefold()
        self.set_history([item for item in self.history() if item.casefold() != target])

    def show_history_popup(self) -> None:
        entries = self._filtered_history()
        if not entries:
            self._popup.hide()
            return
        self._popup.rebuild(entries, self._apply_history_entry, self.remove_history_entry)
        self._popup.resize(max(self.width(), 260), self._popup.sizeHint().height())
        self._popup.move(self.mapToGlobal(QPoint(0, self.height() + 2)))
        self._popup.show()
        self._popup.raise_()

    def focusInEvent(self, event) -> None:
        super().focusInEvent(event)
        self.show_history_popup()

    def focusOutEvent(self, event) -> None:
        if not self._popup.isVisible() or not self._popup.frameGeometry().contains(QCursor.pos()):
            self.record_current_search()
        super().focusOutEvent(event)

    def mousePressEvent(self, event) -> None:
        super().mousePressEvent(event)
        self.show_history_popup()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Down:
            self.show_history_popup()
            event.accept()
            return
        if event.key() == Qt.Key_Escape and self._popup.isVisible():
            self._popup.hide()
            event.accept()
            return
        super().keyPressEvent(event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._popup.isVisible():
            self.show_history_popup()

    def _on_text_edited(self, _text: str) -> None:
        if self._popup.isVisible():
            self.show_history_popup()

    def _filtered_history(self) -> list[str]:
        query = self.text().strip().casefold()
        entries = self.history()
        if not query:
            return entries

        exact_prefix = [item for item in entries if item.casefold().startswith(query)]
        contains = [item for item in entries if query in item.casefold() and item not in exact_prefix]
        return [*exact_prefix, *contains]

    def _apply_history_entry(self, text: str) -> None:
        self.setFocus()
        self.setText(text)
        self.record_current_search()
        self._popup.hide()
