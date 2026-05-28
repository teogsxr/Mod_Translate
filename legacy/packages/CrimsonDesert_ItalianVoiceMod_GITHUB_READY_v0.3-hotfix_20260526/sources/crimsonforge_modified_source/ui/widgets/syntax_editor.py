"""Syntax-highlighted text editor widget."""

from PySide6.QtWidgets import QPlainTextEdit, QWidget, QTextEdit
from PySide6.QtGui import (
    QFont, QSyntaxHighlighter, QTextCharFormat, QColor, QTextDocument,
    QPainter,
)
from PySide6.QtCore import Qt, QRect, QSize, Signal


class LineNumberArea(QWidget):
    """Line number gutter for the syntax editor."""

    def __init__(self, editor):
        super().__init__(editor)
        self._editor = editor

    def sizeHint(self):
        return QSize(self._editor.line_number_area_width(), 0)

    def paintEvent(self, event):
        self._editor.line_number_area_paint_event(event)


class SyntaxEditor(QPlainTextEdit):
    """Text editor with line numbers and basic syntax highlighting."""

    content_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFont(QFont("Courier New", 11))
        self.setTabStopDistance(self.fontMetrics().horizontalAdvance(" ") * 4)
        self.setLineWrapMode(QPlainTextEdit.NoWrap)

        self._line_number_area = LineNumberArea(self)
        self.blockCountChanged.connect(self._update_line_number_width)
        self.updateRequest.connect(self._update_line_number_area)
        self._update_line_number_width(0)

        self._highlighter = None
        self._modified = False
        self.textChanged.connect(self._on_text_changed)

    def line_number_area_width(self) -> int:
        digits = max(1, len(str(self.blockCount())))
        return 10 + self.fontMetrics().horizontalAdvance("9") * digits

    def _update_line_number_width(self, _):
        self.setViewportMargins(self.line_number_area_width(), 0, 0, 0)

    def _update_line_number_area(self, rect, dy):
        if dy:
            self._line_number_area.scroll(0, dy)
        else:
            self._line_number_area.update(0, rect.y(), self._line_number_area.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._update_line_number_width(0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        cr = self.contentsRect()
        self._line_number_area.setGeometry(
            QRect(cr.left(), cr.top(), self.line_number_area_width(), cr.height())
        )

    def line_number_area_paint_event(self, event):
        painter = QPainter(self._line_number_area)
        painter.fillRect(event.rect(), QColor("#313244"))
        block = self.firstVisibleBlock()
        block_number = block.blockNumber()
        top = round(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        bottom = top + round(self.blockBoundingRect(block).height())

        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                number = str(block_number + 1)
                painter.setPen(QColor("#6c7086"))
                painter.drawText(
                    0, top,
                    self._line_number_area.width() - 4,
                    self.fontMetrics().height(),
                    Qt.AlignRight, number,
                )
            block = block.next()
            top = bottom
            bottom = top + round(self.blockBoundingRect(block).height())
            block_number += 1
        painter.end()

    def set_syntax(self, syntax_type: str) -> None:
        """Set syntax highlighting type: 'css', 'html', 'xml', 'json', 'plain'."""
        if self._highlighter:
            self._highlighter.setDocument(None)
        if syntax_type != "plain":
            self._highlighter = BasicHighlighter(self.document(), syntax_type)

    def load_file(self, path: str, encoding: str = "utf-8") -> None:
        """Load a file into the editor."""
        with open(path, "r", encoding=encoding, errors="replace") as f:
            self.setPlainText(f.read())
        self._modified = False

    def save_file(self, path: str, encoding: str = "utf-8") -> None:
        """Save the editor content to a file."""
        with open(path, "w", encoding=encoding) as f:
            f.write(self.toPlainText())
        self._modified = False

    def _on_text_changed(self):
        self._modified = True
        self.content_changed.emit()

    @property
    def modified(self) -> bool:
        return self._modified

    @modified.setter
    def modified(self, value: bool):
        self._modified = value


class BasicHighlighter(QSyntaxHighlighter):
    """Basic syntax highlighter for CSS, HTML, XML, JSON."""

    def __init__(self, document: QTextDocument, syntax_type: str):
        super().__init__(document)
        self._rules = []
        self._syntax = syntax_type

        keyword_fmt = QTextCharFormat()
        keyword_fmt.setForeground(QColor("#cba6f7"))
        keyword_fmt.setFontWeight(QFont.Bold)

        string_fmt = QTextCharFormat()
        string_fmt.setForeground(QColor("#a6e3a1"))

        comment_fmt = QTextCharFormat()
        comment_fmt.setForeground(QColor("#6c7086"))
        comment_fmt.setFontItalic(True)

        number_fmt = QTextCharFormat()
        number_fmt.setForeground(QColor("#fab387"))

        tag_fmt = QTextCharFormat()
        tag_fmt.setForeground(QColor("#89b4fa"))

        attr_fmt = QTextCharFormat()
        attr_fmt.setForeground(QColor("#f9e2af"))

        import re

        if syntax_type == "css":
            self._rules = [
                (re.compile(r'"[^"]*"'), string_fmt),
                (re.compile(r"'[^']*'"), string_fmt),
                (re.compile(r"/\*.*?\*/", re.DOTALL), comment_fmt),
                (re.compile(r"\b\d+(\.\d+)?(px|em|rem|%|vh|vw|pt|cm|mm|in)?\b"), number_fmt),
                (re.compile(r"#[0-9A-Fa-f]{3,8}\b"), number_fmt),
                (re.compile(r"\.[a-zA-Z_][\w-]*"), tag_fmt),
                (re.compile(r"\b(font-family|font-size|font-weight|color|background|margin|padding|border|display|position|width|height|text-align|line-height|overflow|opacity|transform|transition)\b"), keyword_fmt),
            ]
        elif syntax_type in ("html", "xml"):
            self._rules = [
                (re.compile(r"</?[a-zA-Z][^>]*>"), tag_fmt),
                (re.compile(r'\b[a-zA-Z-]+(?==)'), attr_fmt),
                (re.compile(r'"[^"]*"'), string_fmt),
                (re.compile(r"'[^']*'"), string_fmt),
                (re.compile(r"<!--.*?-->", re.DOTALL), comment_fmt),
            ]
        elif syntax_type == "json":
            self._rules = [
                (re.compile(r'"[^"\\]*(\\.[^"\\]*)*"(?=\s*:)'), attr_fmt),
                (re.compile(r'"[^"\\]*(\\.[^"\\]*)*"'), string_fmt),
                (re.compile(r"\b(true|false|null)\b"), keyword_fmt),
                (re.compile(r"\b-?\d+(\.\d+)?([eE][+-]?\d+)?\b"), number_fmt),
            ]

    def highlightBlock(self, text: str):
        for pattern, fmt in self._rules:
            for match in pattern.finditer(text):
                self.setFormat(match.start(), match.end() - match.start(), fmt)
