"""Catalog Browser dialog — categorised, image-grid item picker.

A read-only popup dialog that mirrors the Simple Mode v3 prototype's
icon grid but extended to every item the iteminfo / multichange game
data tables describe (~19 k records, not just weapons). The grid is
backed by ``core.item_catalog.build_item_catalog_cached`` so the
records arrive pre-classified into ``top_category`` / ``category`` /
``subcategory`` / ``subsubcategory`` and pre-enriched with the
English ``display_name`` and the inventory ``icon_paths``.

Architecture
------------

The dialog is split into three coordinating Qt objects:

  * :class:`CatalogModel` — a ``QAbstractListModel`` that owns the
    in-memory record list. The view asks for ``Decoration`` (icon)
    and ``Display`` (label) values lazily, so a 19 k-row catalog
    renders without ever creating that many widgets.
  * :class:`_IconLoadWorker` — a ``QRunnable`` posted to a thread
    pool that decrypts + decompresses + decodes one DDS payload via
    ``vfs.read_entry_data`` + ``core.dds_reader.decode_dds_to_rgba``
    and emits the resulting ``QPixmap`` on a worker signal. The
    model picks it up on the GUI thread, drops it in
    ``QPixmapCache`` keyed on the icon's archive path, and emits
    ``dataChanged`` so the view repaints just that cell.
  * :class:`CatalogBrowserDialog` — the top-level dialog. Wires the
    model, two-tier search bar, category tree (built off the
    catalog's ``top_category``/.../``subsubcategory`` chain), and an
    info panel that shows the highlighted record's PAC paths and
    prefab hashes. Two action signals leak out of the dialog —
    :pyattr:`item_picked` (single-click highlight) and
    :pyattr:`item_activated` (double-click confirm) — the Explorer
    tab connects to the latter to scope its file list to the picked
    item's PAC + sidecar files.

Threading & memory
------------------

  * The model itself never blocks on disk. Every DDS decode is
    posted to ``QThreadPool.globalInstance()`` so a 200-item
    visible page renders in parallel.
  * Decoded thumbnails live in ``QPixmapCache`` (``setCacheLimit``
    bumped to ~96 MB so even the big 1024×1024 itemicon variants
    stay resident across scrolls).
  * In-flight loads are tracked in a ``set[str]`` keyed on icon
    path so we never queue the same decode twice.

The two-tier search reuses ``utils.text_search.match`` exactly as
the Explorer tab's filter does — display-name tier preferred, full
corpus as fallback.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PySide6.QtCore import (
    Qt, QAbstractListModel, QModelIndex, QSize, QObject,
    QRunnable, QThreadPool, Signal, QSortFilterProxyModel,
    QTimer,
)
from PySide6.QtGui import QPixmap, QPixmapCache, QImage, QIcon, QPainter, QColor, QFont
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QListView, QTreeWidget, QTreeWidgetItem, QSplitter, QWidget,
    QPlainTextEdit, QSizePolicy, QMessageBox, QProgressBar,
)

from core.item_catalog import (
    ItemCatalogData,
    ItemCatalogRecord,
    build_item_catalog_cached,
)
from core.vfs_manager import VfsManager
from utils import text_search
from utils.logger import get_logger

logger = get_logger("ui.dialogs.catalog_browser")

# ---------------------------------------------------------------------------
# Tuning constants (no magic numbers in the body of the file)
# ---------------------------------------------------------------------------

THUMB_SIZE = 96                     # icon edge length in the grid (px)
GRID_CELL_HEIGHT = 140              # cell height = thumb + label space
GRID_CELL_WIDTH = 132               # cell width
PIXMAP_CACHE_KB = 256 * 1024        # ~256 MB keep-alive for thumbnails
MAX_INFLIGHT_DECODES = 64           # cap parallel DDS decodes
SEARCH_DEBOUNCE_MS = 150            # debounce search input before refilter
ROOT_LABEL_ALL = "All Items"
ROOT_LABEL_UNCAT = "Uncategorized"


# ---------------------------------------------------------------------------
# Selection signal payload — exposes only what the Explorer needs
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class CatalogSelection:
    """Outbound shape for ``item_picked`` / ``item_activated``."""
    record: ItemCatalogRecord
    pac_files: list[str]            # mirrored convenience accessor
    icon_paths: list[str]


# ---------------------------------------------------------------------------
# Background DDS thumbnail decoder
# ---------------------------------------------------------------------------

class _IconWorkerSignals(QObject):
    """Signals emitted by an :class:`_IconLoadWorker`.

    Defined as a separate ``QObject`` because ``QRunnable`` does not
    inherit from ``QObject`` and therefore cannot itself host
    signals. The worker holds an instance of this class and emits
    through it.
    """
    finished = Signal(str, QImage)
    failed = Signal(str, str)


class _IconLoadWorker(QRunnable):
    """Decode one DDS into a ``QImage`` off the GUI thread.

    The decode pipeline reuses CrimsonForge's existing infrastructure:

      * ``VfsManager.read_entry_data`` for the decrypt + decompress
        + (when needed) type-1 LZ4 inflate path.
      * ``core.dds_reader.decode_dds_to_rgba`` for the actual format
        decode (DXT1/3/5, BC4-7, DX10 uncompressed, FP16/FP32 HDR
        with auto tone-map, etc. — see ``core/dds_reader.py``).

    The PamtFileEntry is resolved up-front via the model's pre-built
    icon-entry index, so the worker never re-scans PAMTs. Failure modes
    are reported via the ``failed`` signal so the model can mark the
    cell as failed and skip retries.
    """

    __slots__ = ("_vfs", "_path", "_entry", "_signals")

    def __init__(self, vfs: VfsManager, icon_path: str, entry) -> None:
        super().__init__()
        self._vfs = vfs
        self._path = icon_path
        self._entry = entry
        self._signals = _IconWorkerSignals()
        self.setAutoDelete(True)

    @property
    def signals(self) -> _IconWorkerSignals:
        return self._signals

    def run(self) -> None:                           # pragma: no cover - threading
        try:
            data = self._vfs.read_entry_data(self._entry)
            from core.dds_reader import decode_dds_to_rgba
            width, height, rgba = decode_dds_to_rgba(data)
            image = QImage(rgba, width, height, width * 4, QImage.Format_RGBA8888).copy()
            self._signals.finished.emit(self._path, image)
        except Exception as exc:                     # noqa: BLE001 - report through signal
            self._signals.failed.emit(self._path, str(exc))


# ---------------------------------------------------------------------------
# Catalog model — virtualised list of records
# ---------------------------------------------------------------------------

class CatalogModel(QAbstractListModel):
    """Read-only ``QAbstractListModel`` exposing a filtered subset of
    :class:`ItemCatalogRecord`.

    The full record list is held in ``self._all_items``; the
    currently visible subset (after category + search filtering) lives
    in ``self._filtered_items``. Filtering is rebuilt on demand by
    :meth:`set_filter` and is fast even for the full 19 k-record
    corpus because ``text_search.match`` is O(query_tokens × corpus_tokens)
    and the corpora are short strings.
    """

    RECORD_ROLE = Qt.UserRole + 1

    def __init__(self, vfs: VfsManager, items: list[ItemCatalogRecord],
                 parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._vfs = vfs
        self._all_items: list[ItemCatalogRecord] = items
        # Pre-tokenize every record once at construction so per-keystroke
        # filtering becomes a set-prefix-check loop instead of a
        # tokenize-then-check-per-row loop. ~1-second one-time cost on a
        # 19 k-record catalog vs ~3 seconds *every keystroke* otherwise.
        self._display_tokens: list[set[str]] = [
            text_search.tokens_for(r.display_name) for r in items
        ]
        self._corpus_tokens: list[set[str]] = [
            text_search.tokens_for(
                r.internal_name, r.display_name, r.search_text,
                " ".join(r.pac_files),
            ) for r in items
        ]
        self._filtered_items: list[ItemCatalogRecord] = list(items)
        self._inflight: set[str] = set()
        self._failed: set[str] = set()
        # Inverse index from icon path back to filtered-row position(s).
        # Rebuilt on every filter change so ``_on_icon_ready`` is O(1)
        # instead of an O(n) linear scan over 19 k records per icon —
        # without this, hundreds of icons resolving in parallel would
        # each take milliseconds and stall the GUI thread on every
        # ``dataChanged`` emit.
        self._icon_to_rows: dict[str, list[int]] = {}
        # Lookup table from in-archive icon path to its PamtFileEntry,
        # built once at model construction by walking every loaded
        # group's PAMT for ``.dds`` entries. The decode worker uses
        # this for an O(1) entry resolve instead of re-scanning every
        # PAMT for every visible cell.
        self._entry_index = self._build_entry_index(vfs)
        # Two distinct placeholder pixmaps so users can tell at a
        # glance whether a cell is mid-decode (the icon is on its way)
        # versus genuinely without an icon (the underlying item ships
        # no inventory icon). Both are 96×96 so cell layout doesn't
        # shift when the real thumbnail arrives.
        self._loading_pixmap = self._build_placeholder("loading…", QColor(45, 50, 60))
        self._missing_pixmap = self._build_placeholder("no icon", QColor(40, 40, 46))
        QPixmapCache.setCacheLimit(PIXMAP_CACHE_KB)
        self._rebuild_icon_inverse_index()

    @staticmethod
    def _build_entry_index(vfs: VfsManager) -> dict:
        """Build ``{lowercased_path: PamtFileEntry}`` for every ``.dds``
        entry across every package group.

        Restricting to ``.dds`` keeps the index size below ~120 k entries
        on a stock install (vs ~1.4 M total PAMT entries) so the
        indexing pass takes ~0.5 s while still covering every conceivable
        inventory-icon path the catalog records may carry.
        """
        index: dict = {}
        pkg = Path(vfs.packages_path)
        if not pkg.is_dir():
            return index
        for child in sorted(pkg.iterdir()):
            if not (child.is_dir() and child.name.isdigit() and len(child.name) == 4):
                continue
            try:
                pamt = vfs.load_pamt(child.name)
            except Exception:
                continue
            for entry in pamt.file_entries:
                p = entry.path.replace("\\", "/").lower()
                if p.endswith(".dds"):
                    index[p] = entry
        return index

    def _rebuild_icon_inverse_index(self) -> None:
        idx: dict[str, list[int]] = {}
        for row, record in enumerate(self._filtered_items):
            for path in record.icon_paths:
                idx.setdefault(path, []).append(row)
        self._icon_to_rows = idx

    # ---- view-driven api ----------------------------------------

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:    # noqa: D401, B008
        return 0 if parent.isValid() else len(self._filtered_items)

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):    # noqa: D401
        if not index.isValid():
            return None
        row = index.row()
        if not (0 <= row < len(self._filtered_items)):
            return None
        record = self._filtered_items[row]

        if role == Qt.DisplayRole:
            label = record.display_name or record.internal_name or ""
            sub = record.pac_files[0].rsplit("/", 1)[-1].rsplit(".", 1)[0] if record.pac_files else ""
            return f"{label}\n{sub}" if sub else label

        if role == Qt.ToolTipRole:
            return self._tooltip_for(record)

        if role == Qt.DecorationRole:
            icon_path = record.icon_paths[0] if record.icon_paths else ""
            return self._pixmap_for(icon_path)

        if role == self.RECORD_ROLE:
            return record

        return None

    # ---- filtering ----------------------------------------------

    def set_filter(self,
                   category_path: tuple[str, ...] = (),
                   query: str = "") -> None:
        """Recompute :pyattr:`_filtered_items`.

        ``category_path`` selects a node in the
        top/category/subcategory/subsubcategory hierarchy; an empty
        tuple means "all items". ``query`` is the same two-tier
        token-search semantics as the Explorer filter.
        """
        self.beginResetModel()
        self._filtered_items = self._compute_filter(category_path, query)
        self._rebuild_icon_inverse_index()
        self.endResetModel()

    def _compute_filter(self,
                        category_path: tuple[str, ...],
                        query: str) -> list[ItemCatalogRecord]:
        # Tokenize the query exactly once per filter pass; per-row
        # corpus tokens were precomputed in __init__.
        q_tokens = text_search.tokenize(query) if query else []
        cat_filter = bool(category_path)
        all_items = self._all_items
        display_tok = self._display_tokens
        corpus_tok = self._corpus_tokens

        # Two-tier match — display name only first, full corpus fallback.
        tier_a: list[ItemCatalogRecord] = []
        tier_b: list[ItemCatalogRecord] = []
        for i, r in enumerate(all_items):
            if cat_filter and not self._record_in_category(r, category_path):
                continue
            if not q_tokens:
                tier_b.append(r)
                continue
            if r.display_name and text_search.match_prefilter(
                q_tokens, display_tok[i]
            ):
                tier_a.append(r)
                continue
            if text_search.match_prefilter(q_tokens, corpus_tok[i]):
                tier_b.append(r)
        return tier_a if tier_a else tier_b

    @staticmethod
    def _record_in_category(record: ItemCatalogRecord,
                            path: tuple[str, ...]) -> bool:
        chain = (record.top_category, record.category,
                 record.subcategory, record.subsubcategory)
        for i, want in enumerate(path):
            if i >= len(chain):
                return False
            actual = chain[i] or ROOT_LABEL_UNCAT
            if actual.lower() != want.lower():
                return False
        return True

    # ---- icon loading -------------------------------------------

    def _pixmap_for(self, icon_path: str) -> QPixmap:
        """Return the cached pixmap for ``icon_path``, kicking off a
        background decode if it isn't cached yet.

        Empty path -> "no icon" placeholder (the item genuinely has
        no inventory icon).
        Failed decode -> "no icon" placeholder (skip retries).
        Cached -> the real thumbnail.
        Otherwise -> "loading" placeholder + queue a decode.
        """
        if not icon_path:
            return self._missing_pixmap

        cache_key = f"catalog_icon::{icon_path}"
        cached = QPixmap()
        if QPixmapCache.find(cache_key, cached):
            return cached

        if icon_path in self._failed:
            return self._missing_pixmap

        # Resolve the entry once at model-construction time; if it
        # never made it into the index we treat the icon as missing
        # so the worker pool doesn't waste a slot decoding nothing.
        entry = self._entry_index.get(icon_path.replace("\\", "/").lower())
        if entry is None:
            self._failed.add(icon_path)
            return self._missing_pixmap

        if icon_path not in self._inflight and len(self._inflight) < MAX_INFLIGHT_DECODES:
            self._inflight.add(icon_path)
            worker = _IconLoadWorker(self._vfs, icon_path, entry)
            worker.signals.finished.connect(self._on_icon_ready)
            worker.signals.failed.connect(self._on_icon_failed)
            QThreadPool.globalInstance().start(worker)
        return self._loading_pixmap

    def _on_icon_ready(self, icon_path: str, image: QImage) -> None:
        self._inflight.discard(icon_path)
        pixmap = QPixmap.fromImage(image).scaled(
            THUMB_SIZE, THUMB_SIZE,
            Qt.KeepAspectRatio, Qt.SmoothTransformation,
        )
        QPixmapCache.insert(f"catalog_icon::{icon_path}", pixmap)

        # O(1) lookup of every visible row using this icon, then a
        # single dataChanged per row. Avoids the 19 k-record linear
        # scan that the previous implementation did per icon.
        for row in self._icon_to_rows.get(icon_path, ()):
            idx = self.index(row, 0)
            self.dataChanged.emit(idx, idx, [Qt.DecorationRole])

    def _on_icon_failed(self, icon_path: str, reason: str) -> None:
        self._inflight.discard(icon_path)
        self._failed.add(icon_path)
        logger.debug("catalog icon decode failed for %s: %s", icon_path, reason)
        for row in self._icon_to_rows.get(icon_path, ()):
            idx = self.index(row, 0)
            self.dataChanged.emit(idx, idx, [Qt.DecorationRole])

    @staticmethod
    def _build_placeholder(label: str, fill: QColor) -> QPixmap:
        """A flat dark placeholder so cells without ready icons still
        render at the same size as decoded thumbnails."""
        pix = QPixmap(THUMB_SIZE, THUMB_SIZE)
        pix.fill(fill)
        painter = QPainter(pix)
        painter.setPen(QColor(80, 80, 90))
        painter.drawRect(0, 0, THUMB_SIZE - 1, THUMB_SIZE - 1)
        painter.setPen(QColor(150, 150, 160))
        f = QFont(); f.setPointSize(8); painter.setFont(f)
        painter.drawText(pix.rect(), Qt.AlignCenter, label)
        painter.end()
        return pix

    @staticmethod
    def _tooltip_for(record: ItemCatalogRecord) -> str:
        category = " > ".join(
            x for x in (record.top_category, record.category,
                         record.subcategory, record.subsubcategory) if x
        )
        lines = [
            f"<b>{record.display_name or record.internal_name}</b>",
            f"<i>{record.internal_name}</i>",
            f"<small>{category}</small>",
        ]
        if record.pac_files:
            lines.append("<br>".join(f"PAC: {p}" for p in record.pac_files[:3]))
        if record.item_id:
            lines.append(f"item id: {record.item_id}")
        return "<br>".join(lines)


# ---------------------------------------------------------------------------
# Dialog
# ---------------------------------------------------------------------------

class CatalogBrowserDialog(QDialog):
    """Modal-by-default catalog browser.

    Emits :pyattr:`item_picked` on single-click selection (the
    Explorer can use this to populate a side panel without committing
    to a scope change yet) and :pyattr:`item_activated` on double-
    click / Enter / the "Open in Explorer" button — that signal is
    what the Explorer tab listens for to actually scope itself to
    the selected item.
    """

    item_picked = Signal(CatalogSelection)
    item_activated = Signal(CatalogSelection)

    def __init__(self, vfs: VfsManager,
                 catalog: ItemCatalogData,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Item Catalog Browser")
        self.resize(1180, 760)
        self._vfs = vfs
        self._catalog = catalog
        self._model: Optional[CatalogModel] = None
        self._selected_record: Optional[ItemCatalogRecord] = None

        self._build_ui()
        self._search_debounce = QTimer(self)
        self._search_debounce.setSingleShot(True)
        self._search_debounce.setInterval(SEARCH_DEBOUNCE_MS)
        self._search_debounce.timeout.connect(self._apply_filter)

        # Catalog is pre-built by the parent tab — wire the model
        # immediately so the dialog has data on first paint instead of
        # showing an empty grid for the duration of a 100-ms pickle
        # load (or a 20-second cold rebuild).
        self._install_catalog(self._dedupe_variants(catalog))

    # ---- ui assembly --------------------------------------------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)

        # --- Search row ---
        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("Search:"))
        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText(
            "type a display name fragment — 'canta plate armor', 'mace of "
            "ambition', 'cd_phw_*' — uses the same two-tier matcher as the "
            "Explorer search bar"
        )
        self._search_edit.textChanged.connect(self._on_search_changed)
        search_row.addWidget(self._search_edit, 1)
        self._count_label = QLabel("Loading catalog…")
        self._count_label.setStyleSheet("color: #888;")
        search_row.addWidget(self._count_label)
        outer.addLayout(search_row)

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setVisible(True)
        outer.addWidget(self._progress)

        # --- Splitter: [tree | grid + info] ---
        splitter = QSplitter(Qt.Horizontal)
        outer.addWidget(splitter, 1)

        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["Category"])
        self._tree.setMinimumWidth(240)
        self._tree.itemSelectionChanged.connect(self._apply_filter)
        splitter.addWidget(self._tree)

        right = QWidget()
        rlay = QVBoxLayout(right); rlay.setContentsMargins(0, 0, 0, 0); rlay.setSpacing(4)
        self._view = QListView()
        self._view.setViewMode(QListView.IconMode)
        self._view.setIconSize(QSize(THUMB_SIZE, THUMB_SIZE))
        self._view.setGridSize(QSize(GRID_CELL_WIDTH, GRID_CELL_HEIGHT))
        self._view.setResizeMode(QListView.Adjust)
        self._view.setMovement(QListView.Static)
        self._view.setUniformItemSizes(True)
        self._view.setSelectionMode(QListView.SingleSelection)
        self._view.setWordWrap(True)
        self._view.setSpacing(8)
        self._view.clicked.connect(self._on_clicked)
        self._view.doubleClicked.connect(self._on_double_clicked)
        rlay.addWidget(self._view, 1)

        self._info_label = QPlainTextEdit()
        self._info_label.setReadOnly(True)
        self._info_label.setMaximumHeight(140)
        self._info_label.setPlaceholderText(
            "Click an item to see its category, internal name, PAC paths, "
            "icon path, and prefab hashes. Double-click (or Open in Explorer "
            "below) to scope the Explorer file list to that item."
        )
        rlay.addWidget(self._info_label)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

        # --- Bottom buttons ---
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self._open_btn = QPushButton("Open in Explorer")
        self._open_btn.setEnabled(False)
        self._open_btn.clicked.connect(self._activate_selection)
        btn_row.addWidget(self._open_btn)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        btn_row.addWidget(close_btn)
        outer.addLayout(btn_row)

    # ---- catalog wiring -----------------------------------------

    @staticmethod
    def _dedupe_variants(data: ItemCatalogData) -> ItemCatalogData:
        """Return a copy of ``data`` with leveling-variant clones folded
        away.

        Pearl Abyss's iteminfo + multichange tables describe each item
        once as a base record (``variant_level is None``) and once per
        upgrade level — for a single Canta Plate Cloak the catalog
        carries the base plus +1 through +30, all sharing the same PAC
        and inventory icon and differing only in the ``(+N)`` suffix on
        their ``display_name``. Showing all of them in the browser is
        pure noise. We keep:

          * every record whose ``variant_level is None`` — the canonical
            base item, and
          * any record that does NOT share its ``variant_base_name``
            with a base record (rare orphan variants where iteminfo only
            ships level rows).

        ``ItemCatalogData.tables`` is forwarded unchanged.
        """
        bases = {
            r.internal_name for r in data.items if r.variant_level is None
        }
        seen_bases: set[str] = set()
        kept: list[ItemCatalogRecord] = []
        for r in data.items:
            if r.variant_level is None:
                kept.append(r)
                continue
            base_key = r.variant_base_name or r.internal_name
            if base_key in bases:
                continue
            if base_key in seen_bases:
                continue
            seen_bases.add(base_key)
            kept.append(r)
        return ItemCatalogData(items=kept, tables=data.tables)

    def _install_catalog(self, data: ItemCatalogData) -> None:
        """Wire a pre-built catalog into the model + tree."""
        self._catalog = data
        self._model = CatalogModel(self._vfs, data.items, parent=self)
        self._view.setModel(self._model)
        self._view.selectionModel().selectionChanged.connect(self._on_selection_changed)
        self._populate_tree()
        self._progress.setVisible(False)
        self._update_count_label()

    # ---- category tree ------------------------------------------

    def _populate_tree(self) -> None:
        """Build a 4-level tree from
        ``top_category > category > subcategory > subsubcategory``.

        Empty subcategory levels collapse — we never insert a child
        whose label would just be empty. The "All Items" pseudo-root
        is always present so users can clear the category filter
        without touching the search box.
        """
        if not self._catalog:
            return
        self._tree.clear()
        from collections import defaultdict
        # tree[top][cat][sub][subsub] = count
        tree: defaultdict = defaultdict(
            lambda: defaultdict(
                lambda: defaultdict(
                    lambda: defaultdict(int)
                )
            )
        )
        for r in self._catalog.items:
            tree[r.top_category or ROOT_LABEL_UNCAT][r.category or ""][r.subcategory or ""][r.subsubcategory or ""] += 1

        all_root = QTreeWidgetItem([f"{ROOT_LABEL_ALL} ({len(self._catalog.items):,})"])
        all_root.setData(0, Qt.UserRole, ())
        self._tree.addTopLevelItem(all_root)

        for top in sorted(tree.keys()):
            top_count = sum(
                cnt for cats in tree[top].values()
                    for subs in cats.values()
                        for cnt in subs.values()
            )
            top_item = QTreeWidgetItem([f"{top} ({top_count:,})"])
            top_item.setData(0, Qt.UserRole, (top,))
            self._tree.addTopLevelItem(top_item)
            for cat in sorted(tree[top].keys()):
                if not cat:
                    continue
                cat_count = sum(
                    cnt for subs in tree[top][cat].values()
                        for cnt in subs.values()
                )
                cat_item = QTreeWidgetItem([f"{cat} ({cat_count:,})"])
                cat_item.setData(0, Qt.UserRole, (top, cat))
                top_item.addChild(cat_item)
                for sub in sorted(tree[top][cat].keys()):
                    if not sub:
                        continue
                    sub_count = sum(tree[top][cat][sub].values())
                    sub_item = QTreeWidgetItem([f"{sub} ({sub_count:,})"])
                    sub_item.setData(0, Qt.UserRole, (top, cat, sub))
                    cat_item.addChild(sub_item)
                    for subsub in sorted(tree[top][cat][sub].keys()):
                        if not subsub:
                            continue
                        cnt = tree[top][cat][sub][subsub]
                        leaf = QTreeWidgetItem([f"{subsub} ({cnt:,})"])
                        leaf.setData(0, Qt.UserRole, (top, cat, sub, subsub))
                        sub_item.addChild(leaf)
        self._tree.setCurrentItem(all_root)

    # ---- search + filter ----------------------------------------

    def _on_search_changed(self) -> None:
        self._search_debounce.start()

    def _apply_filter(self) -> None:
        if not self._model:
            return
        path: tuple[str, ...] = ()
        items = self._tree.selectedItems()
        if items:
            path = items[0].data(0, Qt.UserRole) or ()
        query = self._search_edit.text().strip()
        self._model.set_filter(category_path=tuple(path), query=query)
        self._update_count_label()

    def _update_count_label(self) -> None:
        if not self._model:
            return
        total = len(self._catalog.items) if self._catalog else 0
        shown = self._model.rowCount()
        self._count_label.setText(f"{shown:,} / {total:,} items")

    # ---- selection ----------------------------------------------

    def _on_clicked(self, index: QModelIndex) -> None:
        record = self._record_at(index)
        if record is None:
            return
        self._selected_record = record
        self._open_btn.setEnabled(True)
        self._info_label.setPlainText(self._format_info(record))
        self.item_picked.emit(self._make_selection(record))

    def _on_double_clicked(self, index: QModelIndex) -> None:
        record = self._record_at(index)
        if record is None:
            return
        self._selected_record = record
        self.item_activated.emit(self._make_selection(record))
        self.accept()

    def _on_selection_changed(self, *_: object) -> None:
        idx = self._view.currentIndex()
        if idx.isValid():
            self._on_clicked(idx)

    def _activate_selection(self) -> None:
        if self._selected_record is None:
            return
        self.item_activated.emit(self._make_selection(self._selected_record))
        self.accept()

    def _record_at(self, index: QModelIndex) -> Optional[ItemCatalogRecord]:
        if not self._model or not index.isValid():
            return None
        return self._model.data(index, CatalogModel.RECORD_ROLE)

    @staticmethod
    def _make_selection(record: ItemCatalogRecord) -> CatalogSelection:
        return CatalogSelection(
            record=record,
            pac_files=list(record.pac_files),
            icon_paths=list(record.icon_paths),
        )

    @staticmethod
    def _format_info(record: ItemCatalogRecord) -> str:
        category = " > ".join(
            x for x in (record.top_category, record.category,
                         record.subcategory, record.subsubcategory) if x
        )
        lines = [
            f"Display:  {record.display_name or '(none)'}",
            f"Internal: {record.internal_name}",
            f"Category: {category}",
            f"Type:     {record.raw_type}",
            f"Item ID:  {record.item_id}",
        ]
        if record.pac_files:
            lines.append("")
            lines.append("PAC files:")
            for p in record.pac_files:
                lines.append(f"  {p}")
        if record.icon_paths:
            lines.append("")
            lines.append("Icons:")
            for p in record.icon_paths:
                lines.append(f"  {p}")
        if record.prefab_hashes:
            lines.append("")
            lines.append(f"Prefab hashes: {', '.join(str(h) for h in record.prefab_hashes)}")
        return "\n".join(lines)
