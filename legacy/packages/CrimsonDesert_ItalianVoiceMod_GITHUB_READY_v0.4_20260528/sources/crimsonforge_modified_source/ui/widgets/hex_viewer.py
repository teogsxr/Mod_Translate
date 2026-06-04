"""Hex viewer widget for binary files."""

from PySide6.QtWidgets import QPlainTextEdit
from PySide6.QtGui import QFont


class HexViewer(QPlainTextEdit):
    """Read-only hex viewer for binary file data."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setFont(QFont("Courier New", 9))
        self.setLineWrapMode(QPlainTextEdit.NoWrap)

    def load_data(self, data: bytes, max_bytes: int = 65536) -> None:
        """Display binary data in hex view format."""
        display_data = data[:max_bytes]
        lines = []
        for i in range(0, len(display_data), 16):
            chunk = display_data[i:i + 16]
            hex_part = " ".join(f"{b:02X}" for b in chunk)
            ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
            lines.append(f"{i:08X}  {hex_part:<48s}  {ascii_part}")

        if len(data) > max_bytes:
            lines.append(f"\n... ({len(data) - max_bytes:,} more bytes not shown)")

        self.setPlainText("\n".join(lines))

    def load_file(self, path: str, max_bytes: int = 65536) -> None:
        """Load and display a binary file."""
        with open(path, "rb") as f:
            data = f.read(max_bytes + 1)
        self.load_data(data, max_bytes)
