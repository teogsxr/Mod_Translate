"""UI editor for ``.pabgh`` (Game Data Header) files.

A ``.pabgh`` is the small index file that pairs with a ``.pabgb`` body.
Layout (verified in ``core/pabgb_parser.parse_header``)::

    [count : uint16-LE]
    repeated `count` times:
      simple:  [row_id : uint8] [data_offset : uint32-LE]   (5 bytes/row)
      hashed:  [row_hash : uint32-LE] [data_offset : uint32-LE] (8 bytes/row)

Detection between the two flavours is by file size: ``2 + count * 5``
or ``2 + count * 8``.

This editor exposes the count, the format flavour, and a fully
editable table of (hash/id, offset) pairs. It can:

- view + reorder rows
- edit individual hash/id and offset values
- add or remove rows (count auto-updates)
- save to a sidecar file or patch back to game
"""

from __future__ import annotations

import os
import struct
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from core.pabgb_parser import parse_header
from core.pamt_parser import PamtFileEntry
from core.vfs_manager import VfsManager
from utils.logger import get_logger

logger = get_logger("ui.dialogs.pabgh_editor")


def serialize_pabgh(rows: list[tuple[int, int]], is_simple: bool) -> bytes:
    """Inverse of ``parse_header``. Returns bytes ready to write."""
    count = len(rows)
    if count > 0xFFFF:
        raise ValueError(f"row count {count} exceeds u16 max 65535")
    out = bytearray(struct.pack("<H", count))
    for hash_or_id, offset in rows:
        if is_simple:
            if not (0 <= hash_or_id <= 0xFF):
                raise ValueError(
                    f"simple row id {hash_or_id} doesn't fit in u8 (0-255)"
                )
            out.append(hash_or_id & 0xFF)
            out.extend(struct.pack("<I", offset & 0xFFFFFFFF))
        else:
            out.extend(struct.pack(
                "<II", hash_or_id & 0xFFFFFFFF, offset & 0xFFFFFFFF,
            ))
    return bytes(out)


# Column indices.
_COL_INDEX = 0
_COL_HASH = 1
_COL_OFFSET = 2
_COL_HASH_HEX = 3
_COL_OFFSET_HEX = 4
_COL_COUNT = 5
_HEADERS = ["#", "Hash / ID", "Offset", "Hash (hex)", "Offset (hex)"]


class PabghEditorDialog(QDialog):
    """Editor window for a single .pabgh header."""

    def __init__(
        self,
        header_data: bytes,
        entry: PamtFileEntry,
        vfs: VfsManager,
        *,
        patch_mode: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self._original_data = header_data
        self._entry = entry
        self._vfs = vfs
        self._patch_mode = patch_mode

        rows, is_simple = parse_header(header_data)
        self._rows: list[tuple[int, int]] = list(rows)
        self._is_simple = is_simple

        title = f"Header Editor — {os.path.basename(entry.path)}"
        if patch_mode:
            title += "  (Patch to Game on save)"
        self.setWindowTitle(title)
        self.resize(960, 720)

        self._build_ui()
        self._populate_table()

    # ------------------------------------------------------------------ UI

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)

        self._info = QLabel("")
        self._info.setStyleSheet(
            "color: #a6adc8; padding: 4px 8px; font-size: 12px;"
        )
        self._info.setWordWrap(True)
        self._info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        outer.addWidget(self._info)

        self._table = QTableWidget(0, _COL_COUNT)
        self._table.setHorizontalHeaderLabels(_HEADERS)
        self._table.verticalHeader().setVisible(False)
        h = self._table.horizontalHeader()
        h.setSectionResizeMode(_COL_INDEX, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(_COL_HASH, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(_COL_OFFSET, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(_COL_HASH_HEX, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(_COL_OFFSET_HEX, QHeaderView.Stretch)
        self._table.itemChanged.connect(self._on_item_changed)
        outer.addWidget(self._table, 1)

        # Row controls
        row_btns = QHBoxLayout()
        add_btn = QPushButton("Append row")
        add_btn.clicked.connect(self._append_row)
        row_btns.addWidget(add_btn)
        dup_btn = QPushButton("Duplicate selected")
        dup_btn.clicked.connect(self._duplicate_selected)
        row_btns.addWidget(dup_btn)
        del_btn = QPushButton("Delete selected")
        del_btn.clicked.connect(self._delete_selected)
        row_btns.addWidget(del_btn)
        row_btns.addStretch()
        self._dirty_label = QLabel("")
        self._dirty_label.setStyleSheet("color: #f9e2af; padding: 0 8px;")
        row_btns.addWidget(self._dirty_label)
        outer.addLayout(row_btns)

        # Bottom save buttons
        bot = QHBoxLayout()
        bot.addStretch()
        save_label = "Save + Patch to Game" if self._patch_mode else "Save to file"
        self._save_btn = QPushButton(save_label)
        self._save_btn.setObjectName("primary")
        self._save_btn.clicked.connect(self._on_save)
        bot.addWidget(self._save_btn)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.reject)
        bot.addWidget(close_btn)
        outer.addLayout(bot)

    # ----------------------------------------------------------- populate

    def _populate_table(self) -> None:
        self._info.setText(self._info_text())
        self._table.blockSignals(True)
        self._table.setRowCount(0)
        self._table.setRowCount(len(self._rows))
        for i, (hash_or_id, offset) in enumerate(self._rows):
            self._set_row(i, hash_or_id, offset)
        self._table.blockSignals(False)
        self._dirty_label.setText("")

    def _set_row(self, i: int, hash_or_id: int, offset: int) -> None:
        idx_item = QTableWidgetItem(str(i))
        idx_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        idx_item.setForeground(QColor("#6c7086"))
        self._table.setItem(i, _COL_INDEX, idx_item)

        hash_item = QTableWidgetItem(str(hash_or_id))
        hash_item.setForeground(QColor("#cba6f7"))
        f = QFont("Consolas", 10)
        if not f.exactMatch():
            f = QFont("Courier New", 10)
        hash_item.setFont(f)
        self._table.setItem(i, _COL_HASH, hash_item)

        off_item = QTableWidgetItem(str(offset))
        off_item.setForeground(QColor("#a6e3a1"))
        off_item.setFont(f)
        self._table.setItem(i, _COL_OFFSET, off_item)

        hash_hex = QTableWidgetItem(
            f"0x{hash_or_id:02X}" if self._is_simple else f"0x{hash_or_id:08X}"
        )
        hash_hex.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        hash_hex.setForeground(QColor("#9399b2"))
        hash_hex.setFont(f)
        self._table.setItem(i, _COL_HASH_HEX, hash_hex)

        off_hex = QTableWidgetItem(f"0x{offset:08X}")
        off_hex.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        off_hex.setForeground(QColor("#9399b2"))
        off_hex.setFont(f)
        self._table.setItem(i, _COL_OFFSET_HEX, off_hex)

    def _info_text(self) -> str:
        flavour = "simple (1-byte ID + 4-byte offset = 5 bytes/row)" if self._is_simple \
            else "hashed (4-byte hash + 4-byte offset = 8 bytes/row)"
        size = 2 + len(self._rows) * (5 if self._is_simple else 8)
        return (
            f"<b>{os.path.basename(self._entry.path)}</b>   ·   "
            f"<span style='color:#cdd6f4'>{len(self._rows)}</span> rows   ·   "
            f"flavour: <span style='color:#cdd6f4'>{flavour}</span>   ·   "
            f"file size: <span style='color:#cdd6f4'>{size}</span> bytes "
            f"(original: <span style='color:#cdd6f4'>{len(self._original_data)}</span>)"
        )

    # ------------------------------------------------------------ events

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        # Only HASH and OFFSET columns are editable; the readonly idx/hex
        # columns can't fire this signal because they have NO ItemIsEditable.
        col = item.column()
        if col not in (_COL_HASH, _COL_OFFSET):
            return
        row = item.row()
        if row < 0 or row >= len(self._rows):
            return
        try:
            value = int(item.text(), 0)  # accept "123" or "0x7B"
        except ValueError:
            QMessageBox.warning(
                self, "Invalid value",
                f"'{item.text()}' is not a valid integer "
                f"(decimal or 0x-prefixed hex).",
            )
            self._populate_table()
            return

        # Bounds check
        if col == _COL_HASH:
            max_v = 0xFF if self._is_simple else 0xFFFFFFFF
            if not (0 <= value <= max_v):
                QMessageBox.warning(
                    self, "Out of range",
                    f"Value {value} doesn't fit in "
                    f"{'u8 (0-255)' if self._is_simple else 'u32 (0..0xFFFFFFFF)'}.",
                )
                self._populate_table()
                return
        elif col == _COL_OFFSET:
            if not (0 <= value <= 0xFFFFFFFF):
                QMessageBox.warning(
                    self, "Out of range",
                    f"Offset {value} doesn't fit in u32 (0..0xFFFFFFFF).",
                )
                self._populate_table()
                return

        # Write back into our model
        h, o = self._rows[row]
        if col == _COL_HASH:
            h = value
        else:
            o = value
        self._rows[row] = (h, o)

        # Update the hex columns + count
        self._table.blockSignals(True)
        self._set_row(row, h, o)
        self._table.blockSignals(False)
        self._dirty_label.setText("● modified")
        self._info.setText(self._info_text())

    def _append_row(self) -> None:
        # Default new row: hash/id = 0, offset = end of last row's data
        last_off = self._rows[-1][1] if self._rows else 0
        new_row = (0, last_off)
        self._rows.append(new_row)
        self._populate_table()
        self._dirty_label.setText("● modified")

    def _duplicate_selected(self) -> None:
        rows = sorted({i.row() for i in self._table.selectedItems() if i.row() >= 0})
        if not rows:
            return
        # Insert clones right after the last selected row.
        for r in reversed(rows):
            self._rows.insert(r + 1, self._rows[r])
        self._populate_table()
        self._dirty_label.setText("● modified")

    def _delete_selected(self) -> None:
        rows = sorted({i.row() for i in self._table.selectedItems() if i.row() >= 0},
                      reverse=True)
        if not rows:
            return
        for r in rows:
            del self._rows[r]
        self._populate_table()
        self._dirty_label.setText("● modified")

    # ------------------------------------------------------------ save

    def _on_save(self) -> None:
        try:
            new_data = serialize_pabgh(self._rows, self._is_simple)
        except ValueError as exc:
            QMessageBox.warning(
                self, "Cannot serialize",
                f"{exc}",
            )
            return

        if self._patch_mode:
            if not self._patch_to_game(new_data):
                return
            QMessageBox.information(
                self, "Patched",
                f"Patched {os.path.basename(self._entry.path)} "
                f"({len(new_data)} bytes).",
            )
            self.accept()
        else:
            from ui.dialogs.file_picker import pick_save_file
            target = pick_save_file(
                self, "Save edited .pabgh header",
                default_name=os.path.basename(self._entry.path),
                filter_str="PABGH header (*.pabgh);;All files (*.*)",
            )
            if not target:
                return
            with open(target, "wb") as f:
                f.write(new_data)
            QMessageBox.information(
                self, "Saved",
                f"Wrote {len(new_data)} bytes to:\n{target}",
            )
            self.accept()

    def _patch_to_game(self, new_data: bytes) -> bool:
        reply = QMessageBox.question(
            self, "Patch to Game",
            f"Modify {self._entry.path} in live game archives?\n"
            f"Original: {len(self._original_data)} → New: {len(new_data)} bytes\n\n"
            f"A backup will be created.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return False
        try:
            from core.repack_engine import RepackEngine, ModifiedFile
            game = os.path.dirname(os.path.dirname(self._entry.paz_file))
            papgt = os.path.join(game, "meta", "0.papgt")
            grp = os.path.basename(os.path.dirname(self._entry.paz_file))
            pamt = self._vfs.load_pamt(grp)
            mf = ModifiedFile(
                data=new_data, entry=self._entry,
                pamt_data=pamt, package_group=grp,
            )
            result = RepackEngine(game).repack([mf], papgt_path=papgt)
            if not result.success:
                QMessageBox.critical(
                    self, "Patch failed",
                    "\n".join(result.errors) if result.errors else "Unknown error",
                )
                return False
            return True
        except Exception as exc:
            logger.exception("pabgh patch failed: %s", exc)
            QMessageBox.critical(
                self, "Patch failed",
                f"Could not patch {os.path.basename(self._entry.path)}:\n\n{exc}",
            )
            return False
