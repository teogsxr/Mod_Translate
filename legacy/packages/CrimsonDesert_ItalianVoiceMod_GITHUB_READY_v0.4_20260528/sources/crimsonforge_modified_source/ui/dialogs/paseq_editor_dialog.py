"""UI editor for Pearl Abyss sequencer files.

Handles ``.paseq``, ``.paseqc``, ``.pastage``, plus any other PA
reflection container that uses the standard length-prefixed-string
layout (``.prefab``, ``.pami``, ``.pae``, ``.binarygimmick``,
``.binaryproperty`` …). All editing goes through
:mod:`core.paseq_parser`.

Layout:
- left: filter bar + searchable string list with kind colour
- right: detail pane (offset / length / kind / before-after diff +
  multi-line editor for the value)
- bottom: counts + Save-To-File / Patch-To-Game buttons

Edits are *fixed-length by default*. The "Allow size changes" checkbox
opts into variable-length edits (which the serializer handles by
rewriting the length prefix and shifting subsequent bytes — see
``paseq_parser.serialize_paseq`` for the mechanics).
"""

from __future__ import annotations

import os
from typing import Optional

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableView,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.paseq_parser import (
    PaseqEdit,
    PaseqFile,
    PaseqString,
    parse_paseq,
    serialize_paseq,
    kind_summary,
)
from core.pamt_parser import PamtFileEntry
from core.vfs_manager import VfsManager
from utils.logger import get_logger

logger = get_logger("ui.dialogs.paseq_editor")


_KIND_COLORS = {
    "audio_event":  QColor("#a6e3a1"),   # green — most edit-worthy
    "animation":    QColor("#fab387"),   # orange
    "mesh_path":    QColor("#94e2d5"),   # teal
    "object_path":  QColor("#89b4fa"),   # blue
    "timeline_cmd": QColor("#cba6f7"),   # purple
    "ui_label":     QColor("#f9e2af"),   # yellow
    "type_name":    QColor("#6c7086"),   # dim grey — reflection metadata
    "string":       QColor("#cdd6f4"),   # default
}

_KIND_TOOLTIPS = {
    "audio_event":  "Wwise event / state name (bgm_*, sfx_*, vce_*, region_event_*, st_bgm_*)",
    "animation":    "Animation file path (.paa / .paao)",
    "mesh_path":    "Mesh / texture file path (.pam, .pami, .dds, .pac, …)",
    "object_path":  "Resource path inside object/, character/, leveldata/, effect/, sound/, ui/, …",
    "timeline_cmd": "Sequencer Timeline.* directive — editing these may break flow control",
    "ui_label":     "UI translation label (UI_*) — links to localizationstring_*.paloc",
    "type_name":    "Reflection type or field-name identifier — DO NOT EDIT (will corrupt file)",
    "string":       "Generic ASCII content — likely a value the engine consumes",
}


# Column indices for the model.
_COL_INDEX = 0
_COL_KIND = 1
_COL_LENGTH = 2
_COL_OFFSET = 3
_COL_VALUE = 4
_COL_COUNT = 5
_HEADERS = ["#", "Kind", "Len", "Offset", "Value"]


class _StringModel(QAbstractTableModel):
    def __init__(self, parsed: PaseqFile, parent=None):
        super().__init__(parent)
        self._parsed = parsed
        self._all = parsed.strings
        self._filtered: list[int] = list(range(len(self._all)))
        self._kind_filter = ""
        self._search = ""
        self._hide_type_names = True
        self._edits: dict[int, str] = {}    # prefix_offset → new value

    # ---- public mutators -------------------------------------------------
    def set_filter(self, *, kind: str = "", search: str = "",
                   hide_type_names: bool = True) -> None:
        self.beginResetModel()
        self._kind_filter = kind
        self._search = search.strip().lower()
        self._hide_type_names = hide_type_names
        self._refilter()
        self.endResetModel()

    def record_at(self, row: int) -> Optional[PaseqString]:
        if 0 <= row < len(self._filtered):
            return self._all[self._filtered[row]]
        return None

    def stage_edit(self, target: PaseqString, new_value: str) -> None:
        """Track a pending edit (or remove if reverted to original)."""
        if new_value == target.value:
            self._edits.pop(target.prefix_offset, None)
        else:
            self._edits[target.prefix_offset] = new_value
        # Force the table row to repaint with the new badge.
        for view_row, all_idx in enumerate(self._filtered):
            if self._all[all_idx].prefix_offset == target.prefix_offset:
                idx_l = self.index(view_row, 0)
                idx_r = self.index(view_row, _COL_COUNT - 1)
                self.dataChanged.emit(idx_l, idx_r, [Qt.DisplayRole, Qt.ForegroundRole])
                break

    def edits(self) -> list[PaseqEdit]:
        out: list[PaseqEdit] = []
        for s in self._all:
            new_val = self._edits.get(s.prefix_offset)
            if new_val is None:
                continue
            out.append(PaseqEdit(target=s, new_value=new_val))
        return out

    def edit_count(self) -> int:
        return len(self._edits)

    def fixed_length_only(self) -> bool:
        for s in self._all:
            new_val = self._edits.get(s.prefix_offset)
            if new_val is None:
                continue
            if len(new_val.encode("ascii", errors="replace")) != s.length:
                return False
        return True

    # ---- internals -------------------------------------------------------
    def _refilter(self) -> None:
        out: list[int] = []
        kf = self._kind_filter
        sub = self._search
        hide = self._hide_type_names
        for i, s in enumerate(self._all):
            if hide and s.kind == "type_name":
                continue
            if kf and s.kind != kf:
                continue
            if sub and sub not in s.value.lower():
                continue
            out.append(i)
        self._filtered = out

    # ---- model API -------------------------------------------------------
    def rowCount(self, parent=QModelIndex()) -> int:
        return len(self._filtered)

    def columnCount(self, parent=QModelIndex()) -> int:
        return _COL_COUNT

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return _HEADERS[section]
        return None

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        s = self.record_at(index.row())
        if s is None:
            return None
        col = index.column()
        edited = s.prefix_offset in self._edits
        if role == Qt.DisplayRole:
            if col == _COL_INDEX:
                return f"{'*' if edited else ''}{s.index}"
            if col == _COL_KIND:
                return s.kind
            if col == _COL_LENGTH:
                if edited:
                    new_len = len(self._edits[s.prefix_offset].encode('ascii', errors='replace'))
                    return f"{s.length} → {new_len}"
                return str(s.length)
            if col == _COL_OFFSET:
                return f"0x{s.prefix_offset:08X}"
            if col == _COL_VALUE:
                if edited:
                    return f"{self._edits[s.prefix_offset]}"
                return s.value
        elif role == Qt.ForegroundRole:
            if edited:
                return QColor("#f38ba8")  # pink for staged edits
            if col == _COL_KIND:
                return _KIND_COLORS.get(s.kind, QColor("#cdd6f4"))
            if col == _COL_VALUE:
                return _KIND_COLORS.get(s.kind, QColor("#cdd6f4"))
        elif role == Qt.ToolTipRole:
            return _KIND_TOOLTIPS.get(s.kind, s.kind)
        elif role == Qt.FontRole:
            if col == _COL_VALUE or col == _COL_OFFSET:
                f = QFont("Consolas", 10)
                if not f.exactMatch():
                    f = QFont("Courier New", 10)
                return f
        return None


class PaseqEditorDialog(QDialog):
    """Top-level editor window."""

    patch_completed = Signal()

    def __init__(
        self,
        parsed: PaseqFile,
        entry: PamtFileEntry,
        vfs: VfsManager,
        *,
        patch_mode: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self._parsed = parsed
        self._entry = entry
        self._vfs = vfs
        self._patch_mode = patch_mode

        title = f"Sequencer Editor — {os.path.basename(entry.path)}"
        if patch_mode:
            title += "  (Patch to Game on save)"
        self.setWindowTitle(title)
        self.setMinimumSize(1100, 720)
        self.resize(1400, 850)

        self._build_ui()
        self._populate_kinds()
        self._refresh_summary()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)

        # ── Filter row ─────────────────────────────────────────────
        row = QHBoxLayout()
        row.setSpacing(6)

        self._kind_combo = QComboBox()
        self._kind_combo.addItem("All kinds", "")
        self._kind_combo.currentIndexChanged.connect(self._on_filter_changed)
        row.addWidget(self._kind_combo)

        self._search = QLineEdit()
        self._search.setPlaceholderText(
            "Search strings — value, file path, event name…"
        )
        self._search.textChanged.connect(self._on_filter_changed)
        row.addWidget(self._search, 2)

        self._hide_type = QCheckBox("Hide reflection type names")
        self._hide_type.setChecked(True)
        self._hide_type.setToolTip(
            "Reflection type/field identifiers (TimelineRootNode, _audioEvent, …) "
            "are structural metadata. Editing them corrupts the file. "
            "Hidden by default; uncheck to show them."
        )
        self._hide_type.toggled.connect(self._on_filter_changed)
        row.addWidget(self._hide_type)

        self._summary = QLabel("")
        self._summary.setStyleSheet("color: #a6adc8; padding: 0 6px;")
        row.addWidget(self._summary)

        outer.addLayout(row)

        # ── Splitter: left list / right edit ───────────────────────
        splitter = QSplitter(Qt.Horizontal)

        self._model = _StringModel(self._parsed, self)
        self._table = QTableView()
        self._table.setModel(self._model)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        h = self._table.horizontalHeader()
        h.setSectionResizeMode(_COL_INDEX, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(_COL_KIND, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(_COL_LENGTH, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(_COL_OFFSET, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(_COL_VALUE, QHeaderView.Stretch)
        self._table.selectionModel().selectionChanged.connect(self._on_selection)
        splitter.addWidget(self._table)

        # Right pane: detail + edit
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(6)

        self._detail_label = QLabel("Select a string to edit…")
        self._detail_label.setStyleSheet(
            "font-size: 13px; padding: 4px 8px; color: #a6adc8;"
        )
        self._detail_label.setWordWrap(True)
        self._detail_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        rl.addWidget(self._detail_label)

        edit_label = QLabel("Edit value:")
        edit_label.setStyleSheet("font-weight: 600; padding: 4px 8px 0 8px;")
        rl.addWidget(edit_label)

        self._editor = QTextEdit()
        self._editor.setAcceptRichText(False)
        f = QFont("Consolas", 11)
        if not f.exactMatch():
            f = QFont("Courier New", 11)
        self._editor.setFont(f)
        self._editor.textChanged.connect(self._on_edit_text_changed)
        rl.addWidget(self._editor, 1)

        # Length warning
        self._len_label = QLabel("")
        self._len_label.setStyleSheet("padding: 0 8px;")
        self._len_label.setWordWrap(True)
        rl.addWidget(self._len_label)

        edit_btns = QHBoxLayout()
        self._stage_btn = QPushButton("Stage edit (Ctrl+S)")
        self._stage_btn.clicked.connect(self._stage_current_edit)
        edit_btns.addWidget(self._stage_btn)
        self._revert_btn = QPushButton("Revert to original")
        self._revert_btn.clicked.connect(self._revert_current)
        edit_btns.addWidget(self._revert_btn)
        edit_btns.addStretch()
        rl.addLayout(edit_btns)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        outer.addWidget(splitter, 1)

        # ── Bottom buttons ─────────────────────────────────────────
        bot = QHBoxLayout()
        self._allow_size = QCheckBox("Allow size-changing edits")
        self._allow_size.setToolTip(
            "Off (recommended): only edits that match the original byte length are allowed.\n"
            "On: variable-length edits rewrite the u32 length prefix and shift subsequent bytes.\n"
            "    Use only when you know the file does not embed absolute byte offsets."
        )
        bot.addWidget(self._allow_size)
        bot.addStretch()

        self._save_btn = QPushButton("Save")
        save_label = "Save + Patch to Game" if self._patch_mode else "Save to file"
        self._save_btn.setText(save_label)
        self._save_btn.setObjectName("primary")
        self._save_btn.clicked.connect(self._on_save)
        bot.addWidget(self._save_btn)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.reject)
        bot.addWidget(close_btn)
        outer.addLayout(bot)

    # ----------------------------------------------------------- populate

    def _populate_kinds(self) -> None:
        summary = kind_summary(self._parsed)
        # Stable order with audio_event first (most-edited).
        order = ["audio_event", "animation", "object_path", "mesh_path",
                 "timeline_cmd", "ui_label", "string", "type_name"]
        for k in order:
            if k in summary:
                self._kind_combo.addItem(f"{k}  ({summary[k]})", k)
        # Any kinds not in our pre-defined order get appended.
        for k, n in summary.items():
            if k not in order:
                self._kind_combo.addItem(f"{k}  ({n})", k)

    def _refresh_summary(self) -> None:
        total = len(self._parsed.strings)
        shown = self._model.rowCount()
        edits = self._model.edit_count()
        edit_msg = f"   ·   {edits} pending edit(s)" if edits else ""
        if shown == total:
            self._summary.setText(f"{total:,} strings{edit_msg}")
        else:
            self._summary.setText(f"{shown:,} / {total:,} strings{edit_msg}")

    # ----------------------------------------------------------- handlers

    def _on_filter_changed(self) -> None:
        kind = self._kind_combo.currentData() or ""
        self._model.set_filter(
            kind=kind,
            search=self._search.text(),
            hide_type_names=self._hide_type.isChecked(),
        )
        self._refresh_summary()

    def _on_selection(self) -> None:
        rows = self._table.selectionModel().selectedRows()
        if not rows:
            return
        s = self._model.record_at(rows[0].row())
        if s is None:
            return
        self._show_record(s)

    def _show_record(self, s: PaseqString) -> None:
        self._current = s
        self._detail_label.setText(
            f"<b>#{s.index}</b>   ·   kind: <span style='color:#cdd6f4'>{s.kind}</span>   ·   "
            f"length: <span style='color:#cdd6f4'>{s.length}</span> bytes   ·   "
            f"prefix offset: <code>0x{s.prefix_offset:08X}</code>   ·   "
            f"content offset: <code>0x{s.content_offset:08X}</code><br>"
            f"<span style='color:#a6adc8;font-size:11px'>"
            f"{_KIND_TOOLTIPS.get(s.kind, '')}</span>"
        )
        self._editor.blockSignals(True)
        # Show staged edit if any, otherwise the original value.
        edits = self._model.edits()
        staged = next((e.new_value for e in edits if e.target.prefix_offset == s.prefix_offset), None)
        self._editor.setPlainText(staged if staged is not None else s.value)
        self._editor.blockSignals(False)
        self._update_length_label()

    def _update_length_label(self) -> None:
        if not hasattr(self, "_current") or self._current is None:
            self._len_label.setText("")
            return
        cur = self._editor.toPlainText()
        new_len = len(cur.encode("ascii", errors="replace"))
        delta = new_len - self._current.length
        if delta == 0:
            self._len_label.setText(
                f"<span style='color:#a6e3a1'>"
                f"OK — length matches original ({new_len} bytes)"
                f"</span>"
            )
        elif self._allow_size.isChecked():
            self._len_label.setText(
                f"<span style='color:#fab387'>"
                f"size change: {self._current.length} → {new_len} bytes "
                f"(Δ {delta:+d}). Allowed because 'Allow size-changing edits' is on."
                f"</span>"
            )
        else:
            self._len_label.setText(
                f"<span style='color:#f38ba8'>"
                f"size change: {self._current.length} → {new_len} bytes "
                f"(Δ {delta:+d}). Won't save — toggle 'Allow size-changing edits' "
                f"or pad to {self._current.length} bytes."
                f"</span>"
            )

    def _on_edit_text_changed(self) -> None:
        self._update_length_label()

    def _stage_current_edit(self) -> None:
        if not hasattr(self, "_current") or self._current is None:
            return
        new_val = self._editor.toPlainText()
        # Encode-check — non-ASCII chars are not allowed in this format.
        try:
            new_val.encode("ascii")
        except UnicodeEncodeError as exc:
            QMessageBox.warning(
                self, "Non-ASCII input",
                f"This format only supports ASCII characters. "
                f"Offending character at position {exc.start}.",
            )
            return
        self._model.stage_edit(self._current, new_val)
        self._refresh_summary()

    def _revert_current(self) -> None:
        if not hasattr(self, "_current") or self._current is None:
            return
        self._editor.blockSignals(True)
        self._editor.setPlainText(self._current.value)
        self._editor.blockSignals(False)
        self._model.stage_edit(self._current, self._current.value)
        self._update_length_label()
        self._refresh_summary()

    # ----------------------------------------------------------- save

    def _on_save(self) -> None:
        edits = self._model.edits()
        if not edits:
            QMessageBox.information(
                self, "No edits",
                "There are no staged edits to save.",
            )
            return

        try:
            new_data = serialize_paseq(
                self._parsed, edits,
                allow_size_change=self._allow_size.isChecked(),
            )
        except ValueError as exc:
            QMessageBox.warning(
                self, "Edit rejected",
                f"Could not serialize:\n\n{exc}",
            )
            return

        # Save back via VFS. The patch_mode flag chooses whether we
        # call the patch-to-game pipeline or just write the bytes
        # to a sidecar file.
        if self._patch_mode:
            if not self._patch_to_game(new_data):
                return
            QMessageBox.information(
                self, "Patched",
                f"Patched {os.path.basename(self._entry.path)} with "
                f"{len(edits)} edit(s).",
            )
        else:
            # Save to file picker
            from ui.dialogs.file_picker import pick_save_file
            default_name = os.path.basename(self._entry.path)
            target = pick_save_file(
                self, "Save edited sequencer file",
                default_name=default_name,
                filter_str=f"Sequencer (*{os.path.splitext(self._entry.path)[1]});;All files (*.*)",
            )
            if not target:
                return
            with open(target, "wb") as f:
                f.write(new_data)
            QMessageBox.information(
                self, "Saved",
                f"Wrote {len(new_data):,} bytes to:\n{target}",
            )
        self.patch_completed.emit()
        self.accept()

    def _patch_to_game(self, new_data: bytes) -> bool:
        """Re-encrypt + re-pack into the game archives.

        Uses the same RepackEngine path the pabgb editor does (see
        ``ui.dialogs.pabgb_editor_dialog._patch_to_game``) — backups,
        checksum re-chain, PAPGT update all live there.
        """
        reply = QMessageBox.question(
            self, "Patch to Game",
            f"Modify {self._entry.path} in live game archives?\n"
            f"Original: {len(self._parsed.raw_data):,} → "
            f"New: {len(new_data):,} bytes\n\n"
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
            logger.exception("paseq patch failed: %s", exc)
            QMessageBox.critical(
                self, "Patch failed",
                f"Could not patch {os.path.basename(self._entry.path)}:\n\n{exc}",
            )
            return False
