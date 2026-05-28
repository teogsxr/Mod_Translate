"""Placeholder-scan QA dialog.

Surfaces every translated entry with a broken / altered / leaked
placeholder as a colour-coded table, then lets the user fix them
surgically — either one row at a time or in bulk.

Why this exists
---------------
Even with :mod:`core.translation_tokenizer` locking tokens before
they reach the AI, a small tail of entries come back with
problems:

  * The model dropped a placeholder the source had
    (``MISSING``).
  * The model altered a namespace that should have been
    preserved (``ALTERED``).
  * A tokenizer sentinel leaked into the final translation
    (``LEAKED_SENTINEL``).
  * The model invented an extra placeholder that wasn't in the
    source (``EXTRA_TOKEN`` — flagged, never auto-fixed).

The scanner module (:mod:`core.placeholder_scanner`) detects all
four. This dialog is the QA surface that hands them to a human.

Layout
------
  Header stats bar:   total scanned / broken / auto-fixable
  Filter bar:         issue-kind dropdown + key search
  Main table:         Index / Key / Source (tokens bold) /
                      Translation (broken tokens highlighted) /
                      Issue / Action
  Details pane:       full source + translation with colour
                      highlighting so users can see EXACTLY
                      which token broke and where
  Bottom:             Auto-Fix Selected / Auto-Fix All /
                      Open in Editor / Close

Auto-fix guarantees
-------------------
The "Auto-Fix All" button only touches issues flagged
``auto_fixable=True``. It invokes
:func:`core.placeholder_scanner.autofix_entry`, which does bounded
string edits — translated prose outside the broken placeholder
region is untouched byte-for-byte. ``EXTRA_TOKEN`` issues are
never auto-fixed (they need human judgement).

After fixing, the dialog re-scans and updates the UI so users can
see the outcome and loop through any stragglers.
"""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import (
    QAbstractTableModel, QModelIndex, Qt, Signal,
)
from PySide6.QtGui import QColor, QFont, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QFrame, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton,
    QSplitter, QTableView, QTextEdit, QVBoxLayout, QWidget,
)

from core.placeholder_scanner import (
    IssueKind, PlaceholderIssue, ScanResult, autofix_entry,
    scan_entry,
)
from translation.translation_state import TranslationEntry
from utils.logger import get_logger

logger = get_logger("ui.dialogs.placeholder_scan")


# ── Catppuccin-mocha palette ──────────────────────────────────────
#
# We reuse the same colour language the pac_xml editor uses so
# users familiar with one dialog recognise the state signals in
# the other. Red = the source had something the translation lost
# or corrupted; orange = tokenizer noise we can strip; blue = the
# AI inserted something extra that needs human eyes.
_KIND_COLORS: dict[IssueKind, QColor] = {
    IssueKind.MISSING:         QColor("#f38ba8"),   # red
    IssueKind.ALTERED:         QColor("#fab387"),   # peach
    IssueKind.LEAKED_SENTINEL: QColor("#f9e2af"),   # yellow
    IssueKind.EXTRA_TOKEN:     QColor("#89b4fa"),   # blue
}

_KIND_LABELS: dict[IssueKind, str] = {
    IssueKind.MISSING:         "Missing Token",
    IssueKind.ALTERED:         "Altered Token",
    IssueKind.LEAKED_SENTINEL: "Leaked Sentinel",
    IssueKind.EXTRA_TOKEN:     "Extra Token (review)",
}

_KIND_DESCRIPTIONS: dict[IssueKind, str] = {
    IssueKind.MISSING: (
        "The source had this placeholder but it's missing from the "
        "translation. Auto-fix appends the token to the end of the "
        "translation (safe — does not touch translated prose)."
    ),
    IssueKind.ALTERED: (
        "A placeholder in the translation has a different namespace "
        "/ identifier than the source. Auto-fix replaces just the "
        "broken token with the source's original."
    ),
    IssueKind.LEAKED_SENTINEL: (
        "A tokenizer sentinel leaked through the AI round-trip. "
        "Auto-fix strips just the sentinel and collapses any "
        "double spaces."
    ),
    IssueKind.EXTRA_TOKEN: (
        "A placeholder appears in the translation that was NOT in "
        "the source. This might be a legitimate edit or a model "
        "hallucination — auto-fix is DISABLED. Please review."
    ),
}

# Highlighted-fragment background alpha for the Source / Translation
# rich-text panes. Loud enough to spot at a glance, light enough to
# leave Korean / English text readable.
_HIGHLIGHT_ALPHA = 130


# ── Row model ─────────────────────────────────────────────────────
#
# Each row is one broken ENTRY (not one issue). The Issue column
# summarises all issues in that entry so users can scan the table.
#
# Rationale: if an entry has 2 MISSING + 1 ALTERED, we want one row
# with an "Auto-Fix" button that fixes them all, not three rows.

class _BrokenEntry:
    """One row of the dialog: a broken translation entry + its scan."""

    __slots__ = ("entry", "result")

    def __init__(self, entry: TranslationEntry, result: ScanResult):
        self.entry = entry
        self.result = result

    @property
    def auto_fixable_count(self) -> int:
        return self.result.auto_fixable

    @property
    def total_issues(self) -> int:
        return len(self.result.issues)

    @property
    def primary_kind(self) -> IssueKind:
        """The most serious issue kind for row colouring."""
        # Precedence: EXTRA > MISSING > ALTERED > LEAKED.
        priority = {
            IssueKind.EXTRA_TOKEN: 4,
            IssueKind.MISSING: 3,
            IssueKind.ALTERED: 2,
            IssueKind.LEAKED_SENTINEL: 1,
        }
        return max(
            (i.kind for i in self.result.issues),
            key=lambda k: priority.get(k, 0),
            default=IssueKind.MISSING,
        )


class _BrokenEntriesModel(QAbstractTableModel):
    """Table model — one row per broken entry."""

    COLS = ("#", "Key", "Source", "Translation", "Issues", "Fix")

    def __init__(self, rows: list[_BrokenEntry]):
        super().__init__()
        self._rows = rows

    # ── mandatory API ────────────────────────────────────

    def rowCount(self, parent=QModelIndex()):  # noqa: N802
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()):  # noqa: N802
        return 0 if parent.isValid() else len(self.COLS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):  # noqa: N802
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return self.COLS[section]
        return None

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        row = self._rows[index.row()]
        col = index.column()

        if role in (Qt.DisplayRole, Qt.ToolTipRole):
            if col == 0:
                return str(row.entry.index + 1)
            if col == 1:
                return row.entry.key
            if col == 2:
                return self._elide(row.entry.original_text)
            if col == 3:
                return self._elide(row.entry.translated_text)
            if col == 4:
                # Summarise the per-kind counts.
                from collections import Counter
                c = Counter(i.kind for i in row.result.issues)
                parts = []
                for kind, n in c.most_common():
                    label = _KIND_LABELS[kind]
                    parts.append(f"{n}× {label}")
                summary = ", ".join(parts)
                if role == Qt.ToolTipRole:
                    detail = _KIND_DESCRIPTIONS.get(row.primary_kind, "")
                    return f"{summary}\n\n{detail}"
                return summary
            if col == 5:
                if row.auto_fixable_count == 0:
                    return "Manual only"
                if row.auto_fixable_count == row.total_issues:
                    return f"Auto-Fix ({row.total_issues})"
                return (
                    f"Fix {row.auto_fixable_count}/{row.total_issues}"
                )

        if role == Qt.BackgroundRole:
            # Tint the row by its primary (most serious) issue kind.
            c = _KIND_COLORS[row.primary_kind]
            return QColor(c.red(), c.green(), c.blue(), 40)

        if role == Qt.ForegroundRole and col == 5:
            # Bold the action label in its kind colour.
            return _KIND_COLORS[row.primary_kind]

        if role == Qt.FontRole and (col == 0 or col == 5):
            font = QFont()
            font.setBold(True)
            return font

        if role == Qt.TextAlignmentRole and col in (0, 5):
            return int(Qt.AlignCenter)

        return None

    # ── helpers for the dialog ───────────────────────────

    @staticmethod
    def _elide(text: str, limit: int = 80) -> str:
        """Collapse newlines + truncate for single-line table display."""
        flat = text.replace("\n", " ").replace("\r", "")
        if len(flat) <= limit:
            return flat
        return flat[: limit - 1] + "\u2026"

    def row_at(self, model_row: int) -> Optional[_BrokenEntry]:
        if 0 <= model_row < len(self._rows):
            return self._rows[model_row]
        return None

    def replace_rows(self, rows: list[_BrokenEntry]) -> None:
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()

    def update_row(self, model_row: int, row: _BrokenEntry) -> None:
        if not (0 <= model_row < len(self._rows)):
            return
        self._rows[model_row] = row
        tl = self.index(model_row, 0)
        br = self.index(model_row, len(self.COLS) - 1)
        self.dataChanged.emit(tl, br)

    def remove_row(self, model_row: int) -> None:
        if not (0 <= model_row < len(self._rows)):
            return
        self.beginRemoveRows(QModelIndex(), model_row, model_row)
        self._rows.pop(model_row)
        self.endRemoveRows()

    @property
    def rows(self) -> list[_BrokenEntry]:
        return self._rows


# ── Dialog ────────────────────────────────────────────────────────

class PlaceholderScanDialog(QDialog):
    """QA dialog for reviewing + fixing broken placeholder tokens.

    Parameters
    ----------
    entries
        The full translation-project entry list. Only entries with
        non-empty ``translated_text`` are scanned — everything else
        is skipped as "not yet translated".
    apply_fix
        Callback invoked when the dialog wants to update the
        translated text for one entry. Signature:
        ``apply_fix(entry_index: int, new_text: str) -> None``.
        The caller is responsible for persisting the change into
        the TranslationProject + updating the table widget.
    parent
        Qt parent.
    """

    # Emitted after any Auto-Fix All / Auto-Fix Selected action so
    # the host tab can refresh its translation table.
    fixes_applied = Signal(int)

    def __init__(
        self,
        entries: list[TranslationEntry],
        apply_fix: Callable[[int, str], None],
        parent=None,
    ):
        super().__init__(parent)
        self._entries = entries
        self._apply_fix = apply_fix
        self._total_scanned = 0
        self.setWindowTitle("Placeholder Scan - QA Review")
        self.setModal(False)
        self.resize(1200, 760)
        self._setup_ui()
        self._scan_all()

    # ── UI construction ─────────────────────────────────

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)

        # Header stats bar.
        self._stats = QLabel()
        self._stats.setWordWrap(True)
        self._stats.setStyleSheet(
            "padding: 6px 8px;"
            "color: #cdd6f4;"
            "background: #1e1e2e;"
            "border: 1px solid #313244;"
            "border-radius: 6px;"
            "font-size: 12px;"
        )
        root.addWidget(self._stats)

        # Legend — one line, colour-coded dots.
        legend_row = QHBoxLayout()
        legend_row.setSpacing(12)
        for kind in (
            IssueKind.MISSING, IssueKind.ALTERED,
            IssueKind.LEAKED_SENTINEL, IssueKind.EXTRA_TOKEN,
        ):
            dot = QLabel("\u25CF  " + _KIND_LABELS[kind])
            dot.setToolTip(_KIND_DESCRIPTIONS[kind])
            c = _KIND_COLORS[kind]
            dot.setStyleSheet(
                f"color: {c.name()};"
                "font-size: 11px; font-weight: 600;"
            )
            legend_row.addWidget(dot)
        legend_row.addStretch()
        root.addLayout(legend_row)

        # Filter bar.
        filter_row = QHBoxLayout()
        self._kind_combo = QComboBox()
        self._kind_combo.addItem("All issues", "")
        for kind in (
            IssueKind.MISSING, IssueKind.ALTERED,
            IssueKind.LEAKED_SENTINEL, IssueKind.EXTRA_TOKEN,
        ):
            self._kind_combo.addItem(_KIND_LABELS[kind], kind.value)
        self._kind_combo.currentIndexChanged.connect(self._apply_filter)
        filter_row.addWidget(QLabel("Filter:"))
        filter_row.addWidget(self._kind_combo)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Search key / text")
        self._search.textChanged.connect(self._apply_filter)
        filter_row.addWidget(self._search, 1)

        rescan_btn = QPushButton("Rescan")
        rescan_btn.setToolTip(
            "Re-run the scan over every entry. Use after external "
            "edits (e.g. manual fixes in the main table) to refresh "
            "this dialog."
        )
        rescan_btn.clicked.connect(self._scan_all)
        filter_row.addWidget(rescan_btn)
        root.addLayout(filter_row)

        # Main split: table on top, details below.
        splitter = QSplitter(Qt.Vertical)
        root.addWidget(splitter, 1)

        # Table.
        self._model = _BrokenEntriesModel([])
        self._table = QTableView()
        self._table.setModel(self._model)
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(24)
        self._table.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)

        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)  # #
        header.setSectionResizeMode(1, QHeaderView.Interactive)      # Key
        header.setSectionResizeMode(2, QHeaderView.Interactive)      # Src
        header.setSectionResizeMode(3, QHeaderView.Stretch)          # Trl
        header.setSectionResizeMode(4, QHeaderView.Interactive)      # Iss
        header.setSectionResizeMode(5, QHeaderView.ResizeToContents)  # Fix
        self._table.setColumnWidth(1, 220)
        self._table.setColumnWidth(2, 260)
        self._table.setColumnWidth(4, 180)

        self._table.selectionModel().currentRowChanged.connect(
            self._refresh_details
        )
        self._table.doubleClicked.connect(self._on_row_double_clicked)
        splitter.addWidget(self._table)

        # Details pane — rich-text panes that show the full source
        # and translation, with broken tokens highlighted in their
        # kind's colour so users can see EXACTLY what the fix will
        # touch before they hit Auto-Fix.
        details = QWidget()
        details_layout = QVBoxLayout(details)
        details_layout.setContentsMargins(0, 0, 0, 0)

        src_label = QLabel("Source (protected tokens bold):")
        src_label.setStyleSheet("color: #a6adc8; font-size: 11px;")
        details_layout.addWidget(src_label)

        self._src_view = QTextEdit()
        self._src_view.setReadOnly(True)
        self._src_view.setPlaceholderText("Select a row above.")
        self._src_view.setMaximumHeight(120)
        self._src_view.setFont(self._mono_font())
        details_layout.addWidget(self._src_view)

        trl_label = QLabel("Translation (broken tokens highlighted):")
        trl_label.setStyleSheet("color: #a6adc8; font-size: 11px;")
        details_layout.addWidget(trl_label)

        self._trl_view = QTextEdit()
        self._trl_view.setReadOnly(True)
        self._trl_view.setPlaceholderText("Select a row above.")
        self._trl_view.setFont(self._mono_font())
        details_layout.addWidget(self._trl_view, 1)

        self._issue_detail = QLabel("")
        self._issue_detail.setWordWrap(True)
        self._issue_detail.setStyleSheet(
            "color: #cdd6f4; padding: 6px 8px;"
            "background: #181825; border-radius: 6px;"
            "font-size: 11px;"
        )
        details_layout.addWidget(self._issue_detail)

        splitter.addWidget(details)
        splitter.setSizes([420, 280])

        # Bottom action row.
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        self._fix_sel_btn = QPushButton("Auto-Fix Selected")
        self._fix_sel_btn.setObjectName("primary")
        self._fix_sel_btn.setToolTip(
            "Apply every auto-fixable issue on the selected rows. "
            "EXTRA_TOKEN issues are skipped (require human review).\n"
            "Auto-fix NEVER touches translated prose outside a "
            "broken placeholder region."
        )
        self._fix_sel_btn.clicked.connect(self._fix_selected)
        btn_row.addWidget(self._fix_sel_btn)

        self._fix_all_btn = QPushButton("Auto-Fix All")
        self._fix_all_btn.setObjectName("success")
        self._fix_all_btn.setToolTip(
            "Apply every auto-fixable issue across every broken "
            "entry. EXTRA_TOKEN issues are left for human review."
        )
        self._fix_all_btn.clicked.connect(self._fix_all)
        btn_row.addWidget(self._fix_all_btn)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)

        root.addLayout(btn_row)

    @staticmethod
    def _mono_font() -> QFont:
        font = QFont("Consolas")
        if not font.exactMatch():
            font = QFont("Courier New")
        font.setPointSize(10)
        return font

    # ── Scan & filter ───────────────────────────────────

    def _scan_all(self) -> None:
        """Run scanner over every translated entry."""
        rows: list[_BrokenEntry] = []
        scanned = 0
        for entry in self._entries:
            # Skip entries without a translation yet.
            if not entry.translated_text:
                continue
            scanned += 1
            res = scan_entry(entry.original_text, entry.translated_text)
            if res.broken:
                rows.append(_BrokenEntry(entry, res))
        self._total_scanned = scanned
        # Row order: most serious first, then by entry index for
        # predictability.
        priority = {
            IssueKind.EXTRA_TOKEN: 4,
            IssueKind.MISSING: 3,
            IssueKind.ALTERED: 2,
            IssueKind.LEAKED_SENTINEL: 1,
        }
        rows.sort(
            key=lambda r: (-priority[r.primary_kind], r.entry.index)
        )
        self._model.replace_rows(rows)
        self._refresh_stats()
        self._apply_filter()

    def _refresh_stats(self) -> None:
        broken = len(self._model.rows)
        from collections import Counter
        by_kind: Counter[IssueKind] = Counter()
        auto_fixable = 0
        for row in self._model.rows:
            for issue in row.result.issues:
                by_kind[issue.kind] += 1
                if issue.auto_fixable:
                    auto_fixable += 1

        parts = [
            f"<b>{self._total_scanned:,}</b> translated entries scanned",
            f"<b>{broken:,}</b> broken",
            f"<b>{auto_fixable:,}</b> auto-fixable issues",
        ]
        if by_kind:
            kind_parts = []
            for kind, n in by_kind.most_common():
                c = _KIND_COLORS[kind]
                kind_parts.append(
                    f"<span style='color:{c.name()};'>"
                    f"{_KIND_LABELS[kind]}: {n}</span>"
                )
            parts.append(" · ".join(kind_parts))

        self._stats.setText(" &nbsp;|&nbsp; ".join(parts))

        # Disable action buttons when there's nothing to fix.
        self._fix_all_btn.setEnabled(auto_fixable > 0)
        self._fix_sel_btn.setEnabled(broken > 0)

    def _apply_filter(self) -> None:
        """Re-filter visible rows based on kind combo + search box."""
        kind_val = self._kind_combo.currentData()
        needle = self._search.text().strip().lower()

        for row_idx, row in enumerate(self._model.rows):
            visible = True

            # Kind filter.
            if kind_val:
                has_kind = any(
                    i.kind.value == kind_val
                    for i in row.result.issues
                )
                if not has_kind:
                    visible = False

            # Search filter.
            if visible and needle:
                hay = (
                    row.entry.key.lower()
                    + " " + row.entry.original_text.lower()
                    + " " + row.entry.translated_text.lower()
                )
                if needle not in hay:
                    visible = False

            self._table.setRowHidden(row_idx, not visible)

    # ── Details pane ────────────────────────────────────

    def _refresh_details(self, current: QModelIndex, _prev=None) -> None:
        if not current.isValid():
            self._src_view.clear()
            self._trl_view.clear()
            self._issue_detail.clear()
            return

        row = self._model.row_at(current.row())
        if row is None:
            return

        # Build colour-coded rich text for source + translation.
        self._render_source(row)
        self._render_translation(row)
        self._render_issue_summary(row)

    def _render_source(self, row: _BrokenEntry) -> None:
        """Show source with each protected token in bold + subtle bg."""
        from core.placeholder_scanner import _find_source_tokens
        self._src_view.clear()
        cursor = self._src_view.textCursor()
        base_fmt = QTextCharFormat()
        token_fmt = QTextCharFormat()
        token_fmt.setFontWeight(QFont.Bold)
        # Neutral, low-saturation bg so prose stays readable.
        token_fmt.setBackground(QColor(68, 71, 90, 90))
        token_fmt.setForeground(QColor("#cdd6f4"))

        text = row.entry.original_text
        tokens = _find_source_tokens(text)
        pos = 0
        for tok, (s, e) in tokens:
            if s > pos:
                cursor.insertText(text[pos:s], base_fmt)
            cursor.insertText(text[s:e], token_fmt)
            pos = e
        if pos < len(text):
            cursor.insertText(text[pos:], base_fmt)

    def _render_translation(self, row: _BrokenEntry) -> None:
        """Show translation with broken spans highlighted by kind."""
        self._trl_view.clear()
        cursor = self._trl_view.textCursor()
        text = row.entry.translated_text
        base_fmt = QTextCharFormat()

        # Build a merged list of (start, end, kind) spans from the
        # issues that actually have a translated_span (MISSING does
        # not — it's about an absence in translation, so nothing to
        # highlight in the translation pane for that kind).
        spans: list[tuple[int, int, IssueKind]] = []
        for issue in row.result.issues:
            start, end = issue.translated_span
            if end > start:
                spans.append((start, end, issue.kind))
        spans.sort()

        pos = 0
        for start, end, kind in spans:
            if start > pos:
                cursor.insertText(text[pos:start], base_fmt)
            fmt = QTextCharFormat()
            c = _KIND_COLORS[kind]
            fmt.setBackground(
                QColor(c.red(), c.green(), c.blue(), _HIGHLIGHT_ALPHA)
            )
            fmt.setFontWeight(QFont.Bold)
            # Clamp to text length defensively — a stale span from
            # a pre-edit scan would otherwise IndexError here.
            safe_end = min(end, len(text))
            safe_start = min(start, safe_end)
            cursor.insertText(text[safe_start:safe_end], fmt)
            pos = safe_end
        if pos < len(text):
            cursor.insertText(text[pos:], base_fmt)

    def _render_issue_summary(self, row: _BrokenEntry) -> None:
        lines: list[str] = []
        for issue in row.result.issues:
            c = _KIND_COLORS[issue.kind]
            label = _KIND_LABELS[issue.kind]
            if issue.kind == IssueKind.MISSING:
                detail = (
                    f"source token <code>{_escape(issue.source_token)}"
                    "</code> is missing"
                )
            elif issue.kind == IssueKind.ALTERED:
                detail = (
                    f"<code>{_escape(issue.translated_fragment)}</code> "
                    f"→ <code>{_escape(issue.source_token)}</code>"
                )
            elif issue.kind == IssueKind.LEAKED_SENTINEL:
                detail = (
                    f"leaked sentinel <code>"
                    f"{_escape(issue.translated_fragment)}</code>"
                )
            else:  # EXTRA_TOKEN
                detail = (
                    f"extra token in translation: <code>"
                    f"{_escape(issue.translated_fragment)}</code>"
                )
            suffix = "" if issue.auto_fixable else " <i>(manual only)</i>"
            lines.append(
                f"<span style='color:{c.name()};font-weight:600;'>"
                f"{label}</span> — {detail}{suffix}"
            )
        self._issue_detail.setText("<br>".join(lines))

    # ── Fix actions ─────────────────────────────────────

    def _fix_selected(self) -> None:
        sel = self._table.selectionModel().selectedRows()
        if not sel:
            QMessageBox.information(
                self, "Auto-Fix Selected",
                "No rows selected. Pick one or more rows in the table first."
            )
            return
        # Sort descending so row removal doesn't invalidate indices.
        rows_to_fix = sorted(
            (idx.row() for idx in sel), reverse=True,
        )
        fixes_applied = self._apply_fixes_to_rows(rows_to_fix)
        if fixes_applied > 0:
            self.fixes_applied.emit(fixes_applied)

    def _fix_all(self) -> None:
        if not self._model.rows:
            return
        confirm = QMessageBox.question(
            self, "Auto-Fix All",
            f"Apply auto-fixes to all {len(self._model.rows)} broken "
            "entries?\n\n"
            "Translated prose outside each broken placeholder is "
            "NOT touched. EXTRA_TOKEN issues are left for review.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if confirm != QMessageBox.Yes:
            return
        rows_to_fix = list(range(len(self._model.rows) - 1, -1, -1))
        fixes_applied = self._apply_fixes_to_rows(rows_to_fix)
        if fixes_applied > 0:
            self.fixes_applied.emit(fixes_applied)

    def _apply_fixes_to_rows(self, rows_desc: list[int]) -> int:
        """Apply autofix_entry() to each row in descending order.

        Returns the total number of fixes applied (sum of per-entry
        fix counts). The dialog re-scans after applying so the
        table reflects the post-fix state.
        """
        total_fixes = 0
        rows_changed = 0
        for model_row in rows_desc:
            row = self._model.row_at(model_row)
            if row is None:
                continue
            fixed_text, n = autofix_entry(
                row.entry.original_text,
                row.entry.translated_text,
                row.result,
            )
            if n == 0 or fixed_text == row.entry.translated_text:
                continue
            try:
                self._apply_fix(row.entry.index, fixed_text)
            except Exception as exc:  # pragma: no cover - defensive
                logger.exception(
                    "apply_fix failed for entry %d: %s",
                    row.entry.index, exc,
                )
                continue
            total_fixes += n
            rows_changed += 1

        # Full re-scan — simpler and correct, and the scanner is
        # cheap (~1 µs per entry even for 100 K+ rows).
        if rows_changed:
            self._scan_all()
            QMessageBox.information(
                self, "Auto-Fix Complete",
                f"Fixed {total_fixes} issue(s) across {rows_changed} "
                "entries.\n\nRemaining rows need human review."
            )
        else:
            QMessageBox.information(
                self, "Auto-Fix",
                "No auto-fixable issues in the selected rows."
            )
        return total_fixes

    def _on_row_double_clicked(self, index: QModelIndex) -> None:
        """Double-click = auto-fix just that row."""
        if not index.isValid():
            return
        self._apply_fixes_to_rows([index.row()])


# ── Helpers ────────────────────────────────────────────────────────

def _escape(text: str) -> str:
    """Minimal HTML-escape for embedding user text in rich-text labels."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
