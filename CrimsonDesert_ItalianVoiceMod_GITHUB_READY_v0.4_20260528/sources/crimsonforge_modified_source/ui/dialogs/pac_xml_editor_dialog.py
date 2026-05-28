"""PAC XML editor dialog.

Surfaces every attribute + text node in a ``.pac_xml`` file as a
searchable, filterable, editable table. Mirrors the pattern used
by :mod:`ui.dialogs.prefab_editor_dialog` — info bar / filter bar
/ table / details / action row — so anyone who has used that
dialog can drive this one without relearning.

Layout
------
  Info bar:    filename + field counts per category
  Filter bar:  category dropdown + text search
  Main table:  Path / Tag / Attribute / Value (editable) / Kind
  Details:     selected row's full path + full value (unelided)
  Bottom:      Revert / Save As... / Patch to Game / Close

Editing flow
------------
Double-click any Value cell -> edit in place. Multiple edits are
queued in memory; nothing is persisted until the user clicks
Save As... or Patch to Game.

Patch to Game serialises the edited tree, hands the bytes to
``RepackEngine`` with the original PamtFileEntry, and the engine
automatically re-applies LZ4 compression + ChaCha20 encryption
before writing back into the live archive. A backup is created
automatically by RepackEngine.
"""

from __future__ import annotations

import os
from typing import Optional

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QSize,
    Qt,
)
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QStyledItemDelegate,
    QTableView,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.pac_xml_parser import (
    ParsedPacXml,
    apply_edits,
    categorize_field,
    parse_pac_xml,
    serialize_pac_xml,
    summarize,
)
from core.pamt_parser import PamtFileEntry
from core.vfs_manager import VfsManager
from utils.logger import get_logger

logger = get_logger("ui.dialogs.pac_xml_editor")


# ── Category display ──────────────────────────────────────────────

_CATEGORY_COLORS = {
    "path":    QColor("#a6e3a1"),   # green  — the big "swap this texture"
    "name":    QColor("#f9e2af"),   # yellow — submesh / material names
    "id":      QColor("#89b4fa"),   # blue   — ItemID / IdBase / Index
    "flag":    QColor("#f5c2e7"),   # pink   — booleans + scalar rig hints
    "version": QColor("#cba6f7"),   # mauve  — ReflectObjectXMLDataVersion
    "text":    QColor("#94e2d5"),   # teal   — element text content
    "other":   QColor("#a6adc8"),   # muted  — everything else
}

_CATEGORY_LABELS = {
    "path":    "Path / Texture",
    "name":    "Name",
    "id":      "ID",
    "flag":    "Flag / Number",
    "version": "Version",
    "text":    "Text Content",
    "other":   "Other",
}


# ── Edit-state colours ───────────────────────────────────────────
#
# The category tint alone isn't enough — users need a glance-visible
# signal for "I edited this but haven't saved" vs "this was just
# saved successfully". Catppuccin-mocha palette:
#   red   = unsaved edit, loud enough that save-before-close sticks
#   green = saved / patched successfully, fades on next edit
#   (category tint applies only when the row is in the "original"
#    state — i.e. neither edited nor saved)
_EDIT_STATE_COLOR = {
    "edited":  QColor("#f38ba8"),   # Catppuccin red
    "saved":   QColor("#a6e3a1"),   # Catppuccin green
}

# Transparency applied to BG tints so text stays legible on both
# dark and light themes. Foreground text stays at the theme default.
_STATE_BG_ALPHA = 110      # loud — "you haven't saved this"
_CATEGORY_BG_ALPHA = 40    # subtle — "just grouping hint"


# ── Table model ───────────────────────────────────────────────────

class _FieldsModel(QAbstractTableModel):
    """Table model: Path / Tag / Attr / Value / Kind.

    The table itself is the single source of truth for pending
    edits — the dialog reads pending values off of ``_edits`` and
    applies them to the parser on save.
    """

    COLS = ("Path", "Tag", "Attribute", "Value", "Kind")

    def __init__(self, parsed: ParsedPacXml):
        super().__init__()
        self._parsed = parsed
        # Pending edits: field_index -> new_value. Unedited fields
        # are not present in the dict.
        self._edits: dict[int, str] = {}
        # Field indices that were saved/patched in this session. The
        # set is independent of ``_edits`` — after a successful save,
        # edits move into ``_saved`` and ``_edits`` is cleared. If
        # the user then edits a saved field again it moves back into
        # ``_edits`` (red) and out of ``_saved`` (green).
        self._saved: set[int] = set()

    # ── mandatory API ────────────────────────────────────

    def rowCount(self, parent=QModelIndex()):  # noqa: N802
        return 0 if parent.isValid() else len(self._parsed.fields)

    def columnCount(self, parent=QModelIndex()):  # noqa: N802
        return 0 if parent.isValid() else len(self.COLS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):  # noqa: N802
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return self.COLS[section]
        return None

    def flags(self, index):
        base = super().flags(index)
        if not index.isValid():
            return base
        # Only the Value column is editable.
        if index.column() == 3:
            return base | Qt.ItemIsEditable
        return base

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        f = self._parsed.fields[index.row()]
        col = index.column()

        if role in (Qt.DisplayRole, Qt.EditRole):
            if col == 0:
                return f.path
            if col == 1:
                return f.element_tag
            if col == 2:
                return f.attr if f.kind == "attribute" else "(text)"
            if col == 3:
                return self._edits.get(f.index, f.value)
            if col == 4:
                return f.kind

        if role == Qt.BackgroundRole:
            # Edit state wins over category tint. Three-state
            # precedence — edited (red) > saved (green) > category
            # (faint colour). Each row has exactly one state.
            if f.index in self._edits:
                c = _EDIT_STATE_COLOR["edited"]
                return QColor(c.red(), c.green(), c.blue(), _STATE_BG_ALPHA)
            if f.index in self._saved:
                c = _EDIT_STATE_COLOR["saved"]
                return QColor(c.red(), c.green(), c.blue(), _STATE_BG_ALPHA)
            cat = categorize_field(f)
            colour = _CATEGORY_COLORS.get(cat, _CATEGORY_COLORS["other"])
            return QColor(colour.red(), colour.green(), colour.blue(),
                          _CATEGORY_BG_ALPHA)

        if role == Qt.FontRole and col == 3:
            # Bold the Value column for both edited and saved rows
            # so users can scan long tables for their modified cells.
            if f.index in self._edits or f.index in self._saved:
                font = QFont()
                font.setBold(True)
                return font

        if role == Qt.ToolTipRole and col == 3:
            original = f.value
            current = self._edits.get(f.index, f.value)
            if f.index in self._edits and current != original:
                return (
                    f"EDITED (unsaved):\n"
                    f"Original:  {original}\n"
                    f"Pending:   {current}"
                )
            if f.index in self._saved:
                return (
                    f"SAVED this session:\n"
                    f"Current value: {original}"
                )
            return f"Original (unchanged):\n{original}"

        return None

    def setData(self, index, value, role=Qt.EditRole):  # noqa: N802
        if role != Qt.EditRole or index.column() != 3:
            return False
        f = self._parsed.fields[index.row()]
        new = str(value)
        if new == f.value and f.index in self._edits:
            # Reverting to the original — drop the edit record entirely
            # so the dialog stops reporting it as "pending".
            del self._edits[f.index]
        elif new != f.value:
            self._edits[f.index] = new
            # Re-editing a previously-saved field drops the green
            # highlight back to red. The saved state was accurate at
            # the moment of save; a fresh edit invalidates it.
            self._saved.discard(f.index)
        else:
            return True   # no-op
        tl = self.index(index.row(), 0)
        br = self.index(index.row(), len(self.COLS) - 1)
        self.dataChanged.emit(tl, br, [Qt.DisplayRole, Qt.FontRole,
                                       Qt.ToolTipRole, Qt.BackgroundRole])
        return True

    # ── helpers for the dialog ───────────────────────────

    def pending_edits(self) -> dict[int, str]:
        return dict(self._edits)

    def revert_all(self) -> None:
        if not self._edits:
            return
        self.beginResetModel()
        self._edits.clear()
        self.endResetModel()

    def commit_edits_as_saved(self) -> None:
        """Move every pending edit from ``_edits`` into ``_saved``.

        Called by the dialog after a successful Save As / Patch to
        Game — the values are now persisted, so the row colour
        transitions from red (unsaved) to green (saved-this-session).
        The dialog keeps the green highlight for the remainder of
        the session so users see which fields were touched.
        """
        if not self._edits:
            return
        self.beginResetModel()
        self._saved.update(self._edits.keys())
        self._edits.clear()
        self.endResetModel()

    def rebind(self, parsed: ParsedPacXml) -> None:
        """Re-point the model at a fresh ``ParsedPacXml`` instance
        while preserving the saved-state highlights. Used after
        Patch-to-Game reparses the on-disk bytes: the saved set
        carries over so users still see the green cells, but the
        field values reflect the new on-disk state.
        """
        self.beginResetModel()
        self._parsed = parsed
        # Clear any pending edits (they should all be empty post-save
        # anyway, but be defensive against partial Patch flows).
        self._edits.clear()
        self.endResetModel()


# ── Wide-editor delegate ──────────────────────────────────────────

class _WideEditDelegate(QStyledItemDelegate):
    """Custom delegate that gives the inline Value editor a sensible
    minimum width + full-row-visible height.

    Problem it solves
    -----------------
    Qt's default ``QItemDelegate`` makes the editor the exact size
    of the cell. Texture-path attribute values in .pac_xml can be
    60+ characters long (``character/texture/cd_phw_00_eyecovermaterial_0001_n.dds``)
    but the Value column sits at ~300 px on a 1100-px dialog, so
    users can't see or comfortably edit the path inline.

    This delegate returns a ``QLineEdit`` sized to a generous min-
    width (500 px) and with a clear-button + frame. For even longer
    values the bottom details pane has a multi-line editable view
    (see ``PacXmlEditorDialog._details_edit``).
    """

    def createEditor(self, parent, option, index):  # noqa: N802
        editor = QLineEdit(parent)
        editor.setClearButtonEnabled(True)
        editor.setMinimumWidth(500)
        editor.setFrame(True)
        return editor

    def updateEditorGeometry(self, editor, option, index):  # noqa: N802
        # Keep the editor aligned with the cell but don't shrink it
        # below our minimum width. The alignment keeps the rest of
        # the table visible underneath the editor popup.
        rect = option.rect
        desired_w = max(rect.width(), 500)
        rect.setWidth(desired_w)
        editor.setGeometry(rect)


# ── Dialog ────────────────────────────────────────────────────────

class PacXmlEditorDialog(QDialog):
    """Edit a ``.pac_xml`` file in a popup with full save + patch flow."""

    def __init__(
        self,
        parsed: ParsedPacXml,
        entry: Optional[PamtFileEntry] = None,
        vfs: Optional[VfsManager] = None,
        parent=None,
    ):
        super().__init__(parent)
        self._parsed = parsed
        self._entry = entry
        self._vfs = vfs
        self._setup_ui()
        self._refresh_info()

    def _setup_ui(self) -> None:
        self.setWindowTitle(f"Edit PAC XML - {os.path.basename(self._parsed.path)}")
        self.resize(1100, 720)

        root = QVBoxLayout(self)

        # Info bar — file name + field stats by category.
        self._info = QLabel()
        self._info.setWordWrap(True)
        self._info.setStyleSheet("padding: 4px; color: #cdd6f4;")
        root.addWidget(self._info)

        # Filter row.
        filter_row = QHBoxLayout()
        self._cat_combo = QComboBox()
        self._cat_combo.addItem("All categories", "")
        for key, label in _CATEGORY_LABELS.items():
            self._cat_combo.addItem(label, key)
        self._cat_combo.currentIndexChanged.connect(self._apply_filter)
        filter_row.addWidget(QLabel("Category:"))
        filter_row.addWidget(self._cat_combo)

        self._search = QLineEdit()
        self._search.setPlaceholderText(
            "Search path / attribute / value (e.g. _path, cd_phw_, eyecover)"
        )
        self._search.textChanged.connect(self._apply_filter)
        filter_row.addWidget(self._search, 1)
        root.addLayout(filter_row)

        # Main split: table on top, details below.
        splitter = QSplitter(Qt.Vertical)
        root.addWidget(splitter, 1)

        self._model = _FieldsModel(self._parsed)
        self._table = QTableView()
        self._table.setModel(self._model)
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SingleSelection)
        # Wider inline editor so long texture paths fit. See
        # _WideEditDelegate for the reasoning.
        self._table.setItemDelegateForColumn(3, _WideEditDelegate(self._table))
        # Column sizing — path/attr/value get the headline room.
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Interactive)  # Path
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)  # Tag
        header.setSectionResizeMode(2, QHeaderView.Interactive)  # Attribute
        header.setSectionResizeMode(3, QHeaderView.Stretch)      # Value
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)  # Kind
        self._table.setColumnWidth(0, 260)
        self._table.setColumnWidth(2, 160)
        self._table.verticalHeader().setDefaultSectionSize(22)
        self._table.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self._table.selectionModel().currentRowChanged.connect(self._refresh_details)
        splitter.addWidget(self._table)

        # Details pane — editable for long values. The inline Value
        # cell editor is wider than the cell thanks to the delegate
        # above, but for very long paths or anything users want to
        # see on multiple lines, the details pane below is the
        # primary editing surface: full-width, multi-line, with an
        # Apply button that commits the edit back to the table row.
        details_widget = QWidget()
        details_layout = QVBoxLayout(details_widget)
        details_layout.setContentsMargins(0, 0, 0, 0)
        self._details_label = QLabel("No field selected.")
        self._details_label.setStyleSheet("color: #a6adc8;")
        details_layout.addWidget(self._details_label)

        self._details_edit = QTextEdit()
        self._details_edit.setPlaceholderText(
            "Select a row above to edit its value here, then click Apply."
        )
        self._details_edit.setAcceptRichText(False)
        # Monospace for paths / IDs so users can spot trailing
        # whitespace + compare numeric IDs without font jitter.
        mono = QFont("Consolas")
        if not mono.exactMatch():
            mono = QFont("Courier New")
        mono.setPointSize(10)
        self._details_edit.setFont(mono)
        details_layout.addWidget(self._details_edit, 1)

        details_btns = QHBoxLayout()
        self._apply_btn = QPushButton("Apply to Row")
        self._apply_btn.setToolTip(
            "Commit the value in this box to the selected table row "
            "(the row will turn red to flag it as unsaved)."
        )
        self._apply_btn.clicked.connect(self._apply_details_edit)
        self._apply_btn.setEnabled(False)
        details_btns.addWidget(self._apply_btn)

        self._revert_row_btn = QPushButton("Revert This Row")
        self._revert_row_btn.setToolTip(
            "Discard the pending edit on the selected row only."
        )
        self._revert_row_btn.clicked.connect(self._revert_current_row)
        self._revert_row_btn.setEnabled(False)
        details_btns.addWidget(self._revert_row_btn)
        details_btns.addStretch()
        details_layout.addLayout(details_btns)
        splitter.addWidget(details_widget)
        splitter.setSizes([380, 260])

        # Status + action row.
        self._status = QLabel("")
        self._status.setStyleSheet("color: #a6adc8;")
        root.addWidget(self._status)

        actions = QHBoxLayout()
        revert_btn = QPushButton("Revert All")
        revert_btn.clicked.connect(self._revert_all)
        actions.addWidget(revert_btn)

        save_btn = QPushButton("Save As...")
        save_btn.clicked.connect(self._save_as)
        actions.addWidget(save_btn)

        patch_btn = QPushButton("Patch to Game")
        patch_btn.setObjectName("primary")
        patch_btn.clicked.connect(self._patch_to_game)
        actions.addWidget(patch_btn)

        actions.addStretch()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.reject)
        actions.addWidget(close_btn)
        root.addLayout(actions)

    # ── refresh helpers ──────────────────────────────────

    def _refresh_info(self) -> None:
        stats = summarize(self._parsed)
        pieces = [f"{_CATEGORY_LABELS.get(k, k)}: {v}"
                  for k, v in sorted(stats.items(), key=lambda x: -x[1])]
        bom_tag = "BOM" if self._parsed.has_bom else "no-BOM"
        self._info.setText(
            f"<b>{os.path.basename(self._parsed.path)}</b>  "
            f"&mdash;  {len(self._parsed.fields)} fields  ({bom_tag}, "
            f"{len(self._parsed.raw):,} bytes)  "
            f"&mdash;  " + " / ".join(pieces)
        )

    def _refresh_details(self, current: QModelIndex, _prev: QModelIndex) -> None:
        if not current.isValid():
            self._details_label.setText("No field selected.")
            self._details_edit.setPlainText("")
            self._apply_btn.setEnabled(False)
            self._revert_row_btn.setEnabled(False)
            return
        f = self._parsed.fields[current.row()]
        pending = self._model.pending_edits()
        edited = pending.get(f.index)
        # Show: path / tag / attr / kind + state marker in the label.
        state = ""
        if f.index in pending:
            state = " <span style='color:#f38ba8;'><b>[EDITED — unsaved]</b></span>"
        elif f.index in self._model._saved:
            state = " <span style='color:#a6e3a1;'><b>[SAVED]</b></span>"
        self._details_label.setText(
            f"<b>Path:</b> {f.path}   "
            f"<b>Tag:</b> {f.element_tag}   "
            f"<b>Attribute:</b> "
            f"{f.attr if f.kind == 'attribute' else '(text)'}   "
            f"<b>Kind:</b> {f.kind}{state}"
        )
        # The details box now holds the EDITABLE current value —
        # whichever is active (pending edit if any, else original).
        # Block signals while we programmatically set text so our
        # textChanged hookup doesn't flag this as a user edit.
        current_value = edited if edited is not None else f.value
        self._details_edit.blockSignals(True)
        self._details_edit.setPlainText(current_value)
        self._details_edit.blockSignals(False)
        self._apply_btn.setEnabled(True)
        self._revert_row_btn.setEnabled(f.index in pending)

    def _apply_details_edit(self) -> None:
        """Commit the text in the details pane to the selected row.

        Surfacing this as an explicit button (instead of live-syncing
        on every keystroke) means the details box behaves like a
        proper editor — users can scratch around freely, paste in
        long paths, and only commit when they're happy.
        """
        idx = self._table.selectionModel().currentIndex()
        if not idx.isValid():
            return
        # Route through the model's setData so the red/green logic
        # fires automatically (including reverting to original when
        # the user edited then typed the original back).
        value_index = self._model.index(idx.row(), 3)
        new_value = self._details_edit.toPlainText()
        self._model.setData(value_index, new_value)
        # Bring focus back to the table so keyboard navigation works.
        self._table.setFocus()
        # Refresh label so the "[EDITED]" marker appears.
        self._refresh_details(idx, idx)

    def _revert_current_row(self) -> None:
        idx = self._table.selectionModel().currentIndex()
        if not idx.isValid():
            return
        f = self._parsed.fields[idx.row()]
        if f.index not in self._model.pending_edits():
            return
        value_index = self._model.index(idx.row(), 3)
        # Setting the value back to the original triggers the
        # model's auto-cleanup of the edits dict.
        self._model.setData(value_index, f.value)
        self._refresh_details(idx, idx)

    def _apply_filter(self) -> None:
        cat_key = self._cat_combo.currentData() or ""
        needle = self._search.text().strip().lower()
        for row in range(self._model.rowCount()):
            f = self._parsed.fields[row]
            cat_ok = (not cat_key) or categorize_field(f) == cat_key
            if needle:
                haystack = f"{f.path} {f.attr} {f.value}".lower()
                text_ok = needle in haystack
            else:
                text_ok = True
            self._table.setRowHidden(row, not (cat_ok and text_ok))

    # ── actions ──────────────────────────────────────────

    def _build_edited_bytes(self) -> bytes:
        """Apply pending edits and serialize back to raw bytes.

        Uses ``apply_edits`` to build a new ``ParsedPacXml`` rather
        than mutating the dialog's stored state — this lets ``Revert
        All`` and ``Patch to Game`` both reset cleanly from the same
        origin without worrying about a half-applied state.
        """
        edits = list(self._model.pending_edits().items())
        edited = apply_edits(self._parsed, edits)
        return serialize_pac_xml(edited)

    def _revert_all(self) -> None:
        if not self._model.pending_edits():
            return
        reply = QMessageBox.question(
            self, "Revert all edits",
            "Discard all pending edits and restore the original values?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            self._model.revert_all()
            self._status.setText("Reverted all edits.")
            # Refresh details pane too
            idx = self._table.selectionModel().currentIndex()
            self._refresh_details(idx, idx)

    def _save_as(self) -> None:
        try:
            new_data = self._build_edited_bytes()
        except Exception as e:
            QMessageBox.critical(self, "Serialize failed", str(e))
            return
        default = os.path.basename(self._parsed.path) or "edited.pac_xml"
        path, _ = QFileDialog.getSaveFileName(
            self, "Save PAC XML", default,
            "PAC XML (*.pac_xml);;All files (*)",
        )
        if not path:
            return
        with open(path, "wb") as f:
            f.write(new_data)
        # Promote pending edits to "saved this session" so the rows
        # turn green. Save As writes to disk so the edits ARE
        # persisted even if the user never patches the live archive.
        self._model.commit_edits_as_saved()
        # Update our local parsed state to reflect the now-saved
        # values so future edits and re-saves see the post-save
        # baseline.
        self._parsed = parse_pac_xml(new_data, self._parsed.path)
        self._model.rebind(self._parsed)
        self._refresh_info()
        # Refresh the details pane for the currently-selected row.
        idx = self._table.selectionModel().currentIndex()
        if idx.isValid():
            self._refresh_details(idx, idx)
        self._status.setText(f"Saved {len(new_data):,} bytes to {path}")

    def _patch_to_game(self) -> None:
        if self._entry is None or self._vfs is None:
            QMessageBox.warning(
                self, "Not available",
                "This dialog was opened without a VFS context — cannot "
                "patch back to the game. Use Save As... instead.",
            )
            return

        try:
            new_data = self._build_edited_bytes()
        except Exception as e:
            QMessageBox.critical(self, "Edit rejected", str(e))
            return

        if new_data == self._parsed.raw:
            QMessageBox.information(
                self, "No changes", "Nothing to patch - no edits made."
            )
            return

        reply = QMessageBox.question(
            self, "Patch to Game",
            f"Modify {self._entry.path} in live game archives?\n"
            f"Original: {len(self._parsed.raw):,} bytes -> "
            f"New: {len(new_data):,} bytes\n"
            f"A backup will be created automatically.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        try:
            from core.repack_engine import ModifiedFile, RepackEngine
            self._status.setText("Patching...")
            QApplication.processEvents()

            game = os.path.dirname(os.path.dirname(self._entry.paz_file))
            papgt = os.path.join(game, "meta", "0.papgt")
            grp = os.path.basename(os.path.dirname(self._entry.paz_file))
            pamt = self._vfs.load_pamt(grp)

            # RepackEngine auto-applies LZ4 + ChaCha20 based on the
            # entry's encrypted/compressed flags — so we hand it the
            # raw plaintext bytes and it rebuilds the encrypted blob
            # before writing into the PAZ.
            mf = ModifiedFile(
                data=new_data, entry=self._entry,
                pamt_data=pamt, package_group=grp,
            )
            result = RepackEngine(game).repack([mf], papgt_path=papgt)

            if result.success:
                QMessageBox.information(
                    self, "Patched",
                    f"Patched {self._entry.path}\n"
                    f"Original: {len(self._parsed.raw):,} -> "
                    f"New: {len(new_data):,} bytes",
                )
                self._status.setText("Patch OK.")
                # Promote the pending edits to "saved" BEFORE we
                # reparse, so the green highlight persists through
                # the rebind. Then update the parsed state to match
                # the freshly patched bytes.
                self._model.commit_edits_as_saved()
                self._parsed = parse_pac_xml(new_data, self._parsed.path)
                self._model.rebind(self._parsed)
                self._apply_filter()
                self._refresh_info()
                idx = self._table.selectionModel().currentIndex()
                if idx.isValid():
                    self._refresh_details(idx, idx)
            else:
                QMessageBox.critical(
                    self, "Failed",
                    "\n".join(result.errors) if result.errors else "Unknown error",
                )
        except Exception as e:
            logger.exception("PAC XML patch failed")
            QMessageBox.critical(self, "Patch failed", str(e))
            self._status.setText(f"Patch failed: {e}")
