"""Item catalog browser for raw Crimson Desert game-data."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QProgressBar,
    QSplitter,
    QStatusBar,
    QTableView,
    QTabWidget,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.item_catalog import (
    ItemCatalogData,
    ItemCatalogRecord,
    GameDataTableRecord,
    build_item_catalog,
    build_item_catalog_cached,
    write_catalog_exports,
)
from core.vfs_manager import VfsManager
from utils.thread_worker import FunctionWorker


_ITEM_HEADERS = [
    "Internal Name",
    "Top",
    "Category",
    "Subcategory",
    "Sub-Sub",
    "Raw Type",
    "Variant",
    "Source",
    "PACs",
]
_TABLE_HEADERS = ["File", "Domain", "Subdomain", "Header Pair", "Path"]


class _ItemCatalogModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._all_items: list[ItemCatalogRecord] = []
        self._filtered: list[int] = []
        self._search = ""
        self._source = "All"
        self._confidence = "All"
        self._path_filter: tuple[str, ...] = ()

    def set_items(self, items: list[ItemCatalogRecord]) -> None:
        self.beginResetModel()
        self._all_items = items
        self._refilter()
        self.endResetModel()

    def set_filters(
        self,
        *,
        search: str,
        source: str,
        confidence: str,
        path_filter: tuple[str, ...],
    ) -> None:
        self.beginResetModel()
        self._search = search.strip().lower()
        self._source = source
        self._confidence = confidence
        self._path_filter = path_filter
        self._refilter()
        self.endResetModel()

    def _matches_path(self, item: ItemCatalogRecord) -> bool:
        if not self._path_filter:
            return True
        values = (item.top_category, item.category, item.subcategory, item.subsubcategory)
        return values[: len(self._path_filter)] == self._path_filter

    def _refilter(self) -> None:
        filtered: list[int] = []
        for idx, item in enumerate(self._all_items):
            if self._source == "Base Items" and item.source != "iteminfo":
                continue
            if self._source == "Variants" and item.source != "multichange":
                continue
            if self._confidence != "All" and item.classification_confidence != self._confidence.lower():
                continue
            if not self._matches_path(item):
                continue
            if self._search and self._search not in item.search_text:
                continue
            filtered.append(idx)
        self._filtered = filtered

    def row_at(self, row: int) -> ItemCatalogRecord | None:
        if 0 <= row < len(self._filtered):
            return self._all_items[self._filtered[row]]
        return None

    def rowCount(self, parent=QModelIndex()) -> int:
        return len(self._filtered)

    def columnCount(self, parent=QModelIndex()) -> int:
        return len(_ITEM_HEADERS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return _ITEM_HEADERS[section]
        return None

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        item = self.row_at(index.row())
        if item is None:
            return None

        if role == Qt.DisplayRole:
            if index.column() == 0:
                return item.internal_name
            if index.column() == 1:
                return item.top_category
            if index.column() == 2:
                return item.category
            if index.column() == 3:
                return item.subcategory
            if index.column() == 4:
                return item.subsubcategory
            if index.column() == 5:
                return item.raw_type
            if index.column() == 6:
                return f"+{item.variant_level}" if item.variant_level is not None else ""
            if index.column() == 7:
                return item.source
            if index.column() == 8:
                return len(item.pac_files)

        if role == Qt.ToolTipRole:
            return (
                f"{item.internal_name}\n"
                f"Top: {item.top_category}\n"
                f"Category: {item.category} > {item.subcategory} > {item.subsubcategory}\n"
                f"Raw type: {item.raw_type}\n"
                f"Source: {item.source}\n"
                f"Confidence: {item.classification_confidence}\n"
                f"Loc key: {item.loc_key or '-'}"
            )

        if role == Qt.ForegroundRole and item.classification_confidence == "low":
            return QBrush(QColor("#f9e2af"))
        if role == Qt.ForegroundRole and item.source == "multichange":
            return QBrush(QColor("#89b4fa"))

        if role == Qt.UserRole:
            return item
        return None

    def sort(self, column, order=Qt.AscendingOrder) -> None:
        reverse = order == Qt.DescendingOrder
        self.beginResetModel()
        key_funcs = {
            0: lambda item: item.internal_name.lower(),
            1: lambda item: item.top_category.lower(),
            2: lambda item: item.category.lower(),
            3: lambda item: item.subcategory.lower(),
            4: lambda item: item.subsubcategory.lower(),
            5: lambda item: item.raw_type.lower(),
            6: lambda item: item.variant_level if item.variant_level is not None else -1,
            7: lambda item: item.source.lower(),
            8: lambda item: len(item.pac_files),
        }
        key_fn = key_funcs.get(column, key_funcs[0])
        self._filtered.sort(key=lambda idx: key_fn(self._all_items[idx]), reverse=reverse)
        self.endResetModel()

    @property
    def filtered_count(self) -> int:
        return len(self._filtered)


class _GameDataTableModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._all_tables: list[GameDataTableRecord] = []
        self._filtered: list[int] = []
        self._search = ""
        self._path_filter: tuple[str, ...] = ()

    def set_tables(self, tables: list[GameDataTableRecord]) -> None:
        self.beginResetModel()
        self._all_tables = tables
        self._refilter()
        self.endResetModel()

    def set_filters(self, *, search: str, path_filter: tuple[str, ...]) -> None:
        self.beginResetModel()
        self._search = search.strip().lower()
        self._path_filter = path_filter
        self._refilter()
        self.endResetModel()

    def _refilter(self) -> None:
        filtered: list[int] = []
        for idx, table in enumerate(self._all_tables):
            if self._path_filter:
                values = (table.domain, table.subdomain)
                if values[: len(self._path_filter)] != self._path_filter:
                    continue
            if self._search:
                hay = f"{table.file_name.lower()} {table.path.lower()} {table.domain.lower()} {table.subdomain.lower()}"
                if self._search not in hay:
                    continue
            filtered.append(idx)
        self._filtered = filtered

    def row_at(self, row: int) -> GameDataTableRecord | None:
        if 0 <= row < len(self._filtered):
            return self._all_tables[self._filtered[row]]
        return None

    def rowCount(self, parent=QModelIndex()) -> int:
        return len(self._filtered)

    def columnCount(self, parent=QModelIndex()) -> int:
        return len(_TABLE_HEADERS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return _TABLE_HEADERS[section]
        return None

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        table = self.row_at(index.row())
        if table is None:
            return None

        if role == Qt.DisplayRole:
            if index.column() == 0:
                return table.file_name
            if index.column() == 1:
                return table.domain
            if index.column() == 2:
                return table.subdomain
            if index.column() == 3:
                return "Yes" if table.has_header_pair else "No"
            if index.column() == 4:
                return table.path
        if role == Qt.ToolTipRole:
            return f"{table.file_name}\n{table.path}\nDomain: {table.domain} / {table.subdomain}"
        if role == Qt.ForegroundRole and not table.has_header_pair:
            return QBrush(QColor("#f9e2af"))
        if role == Qt.UserRole:
            return table
        return None

    def sort(self, column, order=Qt.AscendingOrder) -> None:
        reverse = order == Qt.DescendingOrder
        self.beginResetModel()
        key_funcs = {
            0: lambda table: table.file_name.lower(),
            1: lambda table: table.domain.lower(),
            2: lambda table: table.subdomain.lower(),
            3: lambda table: table.has_header_pair,
            4: lambda table: table.path.lower(),
        }
        key_fn = key_funcs.get(column, key_funcs[0])
        self._filtered.sort(key=lambda idx: key_fn(self._all_tables[idx]), reverse=reverse)
        self.endResetModel()

    @property
    def filtered_count(self) -> int:
        return len(self._filtered)


class ItemCatalogTab(QWidget):
    # See DialogueCatalogTab for the rationale — these signals route
    # the result of an inline (worker-thread) build back to the UI
    # thread so widget mutations stay on the main thread.
    _lazy_init_finished = Signal(object)  # ItemCatalogData
    _lazy_init_error = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._packages_path = ""
        self._worker: FunctionWorker | None = None
        self._data: ItemCatalogData | None = None
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(180)
        self._search_timer.timeout.connect(self._apply_filters)
        self._item_model = _ItemCatalogModel(self)
        self._table_model = _GameDataTableModel(self)
        self._lazy_init_finished.connect(self._on_worker_finished)
        self._lazy_init_error.connect(self._on_worker_error)
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        toolbar = QHBoxLayout()
        self._summary_label = QLabel("Load a game to build the item catalog.")
        toolbar.addWidget(self._summary_label, 1)

        toolbar.addWidget(QLabel("Search:"))
        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("Search internal names, categories, PAC files, loc keys...")
        self._search_input.textChanged.connect(lambda _: self._search_timer.start())
        toolbar.addWidget(self._search_input, 2)

        toolbar.addWidget(QLabel("Source:"))
        self._source_combo = QComboBox()
        self._source_combo.addItems(["All", "Base Items", "Variants"])
        self._source_combo.currentTextChanged.connect(lambda _: self._apply_filters())
        toolbar.addWidget(self._source_combo)

        toolbar.addWidget(QLabel("Confidence:"))
        self._confidence_combo = QComboBox()
        self._confidence_combo.addItems(["All", "High", "Medium", "Low"])
        self._confidence_combo.currentTextChanged.connect(lambda _: self._apply_filters())
        toolbar.addWidget(self._confidence_combo)

        self._refresh_button = QPushButton("Refresh From Game")
        self._refresh_button.clicked.connect(self._refresh_from_game)
        toolbar.addWidget(self._refresh_button)

        self._export_button = QPushButton("Export Cache")
        self._export_button.setEnabled(False)
        self._export_button.clicked.connect(self._export_cache)
        toolbar.addWidget(self._export_button)

        layout.addLayout(toolbar)

        self._progress = QProgressBar()
        self._progress.setVisible(False)
        self._progress.setRange(0, 0)
        self._progress.setTextVisible(False)
        layout.addWidget(self._progress)

        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_items_page(), "Items")
        self._tabs.addTab(self._build_tables_page(), "Game Tables")
        layout.addWidget(self._tabs, 1)

        self._status = QStatusBar()
        self._status.showMessage("Idle")
        layout.addWidget(self._status)

    def _build_items_page(self) -> QWidget:
        page = QWidget()
        layout = QHBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)

        splitter = QSplitter(Qt.Horizontal)
        layout.addWidget(splitter)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(QLabel("Category Tree"))
        self._item_tree = QTreeWidget()
        self._item_tree.setHeaderLabels(["Branch", "Count"])
        self._item_tree.setUniformRowHeights(True)
        self._item_tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self._item_tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self._item_tree.itemSelectionChanged.connect(self._apply_filters)
        left_layout.addWidget(self._item_tree, 1)
        splitter.addWidget(left)

        right = QSplitter(Qt.Vertical)
        splitter.addWidget(right)

        top = QWidget()
        top_layout = QVBoxLayout(top)
        top_layout.setContentsMargins(0, 0, 0, 0)
        self._item_count_label = QLabel("0 items")
        top_layout.addWidget(self._item_count_label)
        self._item_table = QTableView()
        self._item_table.setModel(self._item_model)
        self._item_table.setSortingEnabled(True)
        self._item_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._item_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._item_table.setAlternatingRowColors(True)
        self._item_table.verticalHeader().setVisible(False)
        self._item_table.verticalHeader().setMinimumSectionSize(22)
        self._item_table.verticalHeader().setDefaultSectionSize(24)
        self._item_table.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self._item_table.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        self._item_table.horizontalHeader().setStretchLastSection(True)
        self._item_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self._item_table.selectionModel().selectionChanged.connect(self._update_item_details)
        top_layout.addWidget(self._item_table, 1)
        right.addWidget(top)

        self._item_details = QTextEdit()
        self._item_details.setReadOnly(True)
        self._item_details.setPlaceholderText("Select an item to inspect its raw game-data fields.")
        right.addWidget(self._item_details)

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        right.setStretchFactor(0, 3)
        right.setStretchFactor(1, 2)
        splitter.setSizes([300, 950])
        return page

    def _build_tables_page(self) -> QWidget:
        page = QWidget()
        layout = QHBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)

        splitter = QSplitter(Qt.Horizontal)
        layout.addWidget(splitter)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(QLabel("Raw Table Domains"))
        self._table_tree = QTreeWidget()
        self._table_tree.setHeaderLabels(["Branch", "Count"])
        self._table_tree.setUniformRowHeights(True)
        self._table_tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self._table_tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self._table_tree.itemSelectionChanged.connect(self._apply_filters)
        left_layout.addWidget(self._table_tree, 1)
        splitter.addWidget(left)

        right = QSplitter(Qt.Vertical)
        splitter.addWidget(right)

        top = QWidget()
        top_layout = QVBoxLayout(top)
        top_layout.setContentsMargins(0, 0, 0, 0)
        self._table_count_label = QLabel("0 tables")
        top_layout.addWidget(self._table_count_label)
        self._table_view = QTableView()
        self._table_view.setModel(self._table_model)
        self._table_view.setSortingEnabled(True)
        self._table_view.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table_view.setSelectionMode(QAbstractItemView.SingleSelection)
        self._table_view.setAlternatingRowColors(True)
        self._table_view.verticalHeader().setVisible(False)
        self._table_view.verticalHeader().setMinimumSectionSize(22)
        self._table_view.verticalHeader().setDefaultSectionSize(24)
        self._table_view.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self._table_view.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        self._table_view.horizontalHeader().setStretchLastSection(True)
        self._table_view.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self._table_view.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self._table_view.selectionModel().selectionChanged.connect(self._update_table_details)
        top_layout.addWidget(self._table_view, 1)
        right.addWidget(top)

        self._table_details = QTextEdit()
        self._table_details.setReadOnly(True)
        self._table_details.setPlaceholderText("Select a game-data table to inspect its raw path and grouping.")
        right.addWidget(self._table_details)

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        right.setStretchFactor(0, 3)
        right.setStretchFactor(1, 2)
        splitter.setSizes([280, 970])
        return page

    def initialize_from_game(self, vfs: VfsManager) -> None:
        self.initialize_from_game_path(vfs.packages_path)

    def initialize_from_game_path(self, packages_path: str) -> None:
        if not packages_path:
            return
        if self._packages_path == packages_path and self._data is not None:
            return
        self._packages_path = packages_path

        # See DialogueCatalogTab for the full rationale: when called
        # from MainWindow's lazy-tab worker thread, build inline so
        # the loading overlay stays up until the data is actually
        # ready. From the UI thread (Refresh button), use the inner
        # worker so we don't freeze the UI.
        ui_thread = QApplication.instance().thread() if QApplication.instance() else None
        if ui_thread is not None and QThread.currentThread() is not ui_thread:
            self._build_catalog_inline(packages_path)
        else:
            self._refresh_from_game()

    def _build_catalog_inline(self, packages_path: str) -> None:
        """Run the build SYNCHRONOUSLY on the calling worker thread.

        After the build, marshal the result back to the UI thread
        via a queued signal so widget population happens on the
        right thread.
        """
        try:
            vfs = VfsManager(packages_path)
            data = build_item_catalog_cached(vfs)
            out_dir = Path(__file__).resolve().parents[1] / "exports" / "item_catalog"
            try:
                write_catalog_exports(data, out_dir)
            except Exception:
                # Export-writing is a nice-to-have side effect — log
                # but don't kill the tab init if disk is full or
                # the exports dir is read-only.
                import logging
                logging.getLogger(__name__).exception(
                    "item catalog export write failed"
                )
            self._lazy_init_finished.emit(data)
        except Exception as e:
            import logging
            logging.getLogger(__name__).exception(
                "item catalog inline build failed"
            )
            self._lazy_init_error.emit(f"{type(e).__name__}: {e}")

    def _refresh_from_game(self) -> None:
        if not self._packages_path or self._worker is not None:
            return
        self._refresh_button.setEnabled(False)
        self._export_button.setEnabled(False)
        self._progress.setVisible(True)
        self._summary_label.setText("Building item catalog from live game data...")
        self._status.showMessage("Building item catalog...")
        self._worker = FunctionWorker(self._build_catalog_worker, self._packages_path)
        self._worker.progress.connect(self._on_worker_progress)
        self._worker.finished_result.connect(self._on_worker_finished)
        self._worker.error_occurred.connect(self._on_worker_error)
        self._worker.start()

    def _build_catalog_worker(self, worker: FunctionWorker, packages_path: str):
        def progress(message: str) -> None:
            worker.report_progress(0, message)

        vfs = VfsManager(packages_path)
        # Manual Refresh hits the same disk cache as the lazy-init
        # path — first run pays the build cost, subsequent runs are
        # ~100 ms.
        data = build_item_catalog_cached(vfs, progress_fn=progress)
        out_dir = Path(__file__).resolve().parents[1] / "exports" / "item_catalog"
        write_catalog_exports(data, out_dir)
        return data

    def _on_worker_progress(self, _pct: int, message: str) -> None:
        self._summary_label.setText(message)
        self._status.showMessage(message)

    def _on_worker_finished(self, data: ItemCatalogData) -> None:
        self._worker = None
        self._data = data
        self._progress.setVisible(False)
        self._refresh_button.setEnabled(True)
        self._export_button.setEnabled(True)
        self._populate_views()
        self._apply_filters()
        self._summary_label.setText(
            f"Loaded {len(data.items):,} item records and {len(data.tables):,} raw game-data tables."
        )
        self._status.showMessage("Item catalog ready")

    def _on_worker_error(self, message: str) -> None:
        self._worker = None
        self._progress.setVisible(False)
        self._refresh_button.setEnabled(True)
        self._summary_label.setText("Failed to build item catalog.")
        self._status.showMessage(message)

    def _populate_views(self) -> None:
        if self._data is None:
            return
        self._item_model.set_items(self._data.items)
        self._table_model.set_tables(self._data.tables)
        self._populate_item_tree()
        self._populate_table_tree()

    def _populate_item_tree(self) -> None:
        self._item_tree.setUpdatesEnabled(False)
        try:
            self._item_tree.clear()
            if self._data is None:
                return
            self.__populate_item_tree_inner()
        finally:
            self._item_tree.setUpdatesEnabled(True)

    def __populate_item_tree_inner(self) -> None:
        root = QTreeWidgetItem(["All Items", str(len(self._data.items))])
        root.setData(0, Qt.UserRole, ())
        self._item_tree.addTopLevelItem(root)

        top_counts = Counter(item.top_category for item in self._data.items)
        category_counts = Counter((item.top_category, item.category) for item in self._data.items)
        sub_counts = Counter((item.top_category, item.category, item.subcategory) for item in self._data.items)
        leaf_counts = Counter(
            (item.top_category, item.category, item.subcategory, item.subsubcategory)
            for item in self._data.items
        )

        for top_name, top_count in sorted(top_counts.items()):
            top_item = QTreeWidgetItem([top_name, str(top_count)])
            top_item.setData(0, Qt.UserRole, (top_name,))
            root.addChild(top_item)

            categories = sorted(key[1] for key in category_counts if key[0] == top_name)
            for category_name in dict.fromkeys(categories):
                category_item = QTreeWidgetItem([category_name, str(category_counts[(top_name, category_name)])])
                category_item.setData(0, Qt.UserRole, (top_name, category_name))
                top_item.addChild(category_item)

                subcategories = sorted(
                    key[2]
                    for key in sub_counts
                    if key[0] == top_name and key[1] == category_name
                )
                for sub_name in dict.fromkeys(subcategories):
                    sub_item = QTreeWidgetItem([sub_name, str(sub_counts[(top_name, category_name, sub_name)])])
                    sub_item.setData(0, Qt.UserRole, (top_name, category_name, sub_name))
                    category_item.addChild(sub_item)

                    leaves = sorted(
                        key[3]
                        for key in leaf_counts
                        if key[0] == top_name and key[1] == category_name and key[2] == sub_name
                    )
                    for leaf_name in dict.fromkeys(leaves):
                        leaf_item = QTreeWidgetItem(
                            [leaf_name, str(leaf_counts[(top_name, category_name, sub_name, leaf_name)])]
                        )
                        leaf_item.setData(0, Qt.UserRole, (top_name, category_name, sub_name, leaf_name))
                        sub_item.addChild(leaf_item)

        self._item_tree.expandToDepth(1)
        self._item_tree.setCurrentItem(root)

    def _populate_table_tree(self) -> None:
        self._table_tree.setUpdatesEnabled(False)
        try:
            self._table_tree.clear()
            if self._data is None:
                return
            self.__populate_table_tree_inner()
        finally:
            self._table_tree.setUpdatesEnabled(True)

    def __populate_table_tree_inner(self) -> None:
        root = QTreeWidgetItem(["All Tables", str(len(self._data.tables))])
        root.setData(0, Qt.UserRole, ())
        self._table_tree.addTopLevelItem(root)

        domain_counts = Counter(table.domain for table in self._data.tables)
        subdomain_counts = Counter((table.domain, table.subdomain) for table in self._data.tables)

        for domain_name, domain_count in sorted(domain_counts.items()):
            domain_item = QTreeWidgetItem([domain_name, str(domain_count)])
            domain_item.setData(0, Qt.UserRole, (domain_name,))
            root.addChild(domain_item)
            subdomains = sorted(key[1] for key in subdomain_counts if key[0] == domain_name)
            for subdomain_name in dict.fromkeys(subdomains):
                sub_item = QTreeWidgetItem([subdomain_name, str(subdomain_counts[(domain_name, subdomain_name)])])
                sub_item.setData(0, Qt.UserRole, (domain_name, subdomain_name))
                domain_item.addChild(sub_item)

        self._table_tree.expandToDepth(1)
        self._table_tree.setCurrentItem(root)

    def _selected_item_path(self) -> tuple[str, ...]:
        item = self._item_tree.currentItem()
        return tuple(item.data(0, Qt.UserRole)) if item else ()

    def _selected_table_path(self) -> tuple[str, ...]:
        item = self._table_tree.currentItem()
        return tuple(item.data(0, Qt.UserRole)) if item else ()

    def _apply_filters(self) -> None:
        search = self._search_input.text()
        self._item_model.set_filters(
            search=search,
            source=self._source_combo.currentText(),
            confidence=self._confidence_combo.currentText(),
            path_filter=self._selected_item_path(),
        )
        self._table_model.set_filters(search=search, path_filter=self._selected_table_path())

        self._item_count_label.setText(f"{self._item_model.filtered_count:,} item rows")
        self._table_count_label.setText(f"{self._table_model.filtered_count:,} game-data tables")

    def _update_item_details(self) -> None:
        indexes = self._item_table.selectionModel().selectedRows()
        if not indexes:
            self._item_details.clear()
            return
        item = self._item_model.row_at(indexes[0].row())
        if item is None:
            self._item_details.clear()
            return

        details = [
            f"Internal Name: {item.internal_name}",
            f"Source: {item.source}",
            f"Item ID: {item.item_id if item.item_id is not None else '-'}",
            f"Loc Key: {item.loc_key or '-'}",
            f"Variant Base: {item.variant_base_name}",
            f"Variant Level: {item.variant_level if item.variant_level is not None else '-'}",
            "",
            f"Top Category: {item.top_category}",
            f"Category: {item.category}",
            f"Subcategory: {item.subcategory}",
            f"Sub-Subcategory: {item.subsubcategory}",
            f"Raw Type: {item.raw_type}",
            f"Classification Source: {item.classification_source}",
            f"Confidence: {item.classification_confidence}",
            "",
            "PAC Files:",
        ]
        if item.pac_files:
            details.extend(f"  - {pac}" for pac in item.pac_files)
        else:
            details.append("  - none")
        details.append("")
        details.append("Prefab Hashes:")
        if item.prefab_hashes:
            details.extend(f"  - {value}" for value in item.prefab_hashes)
        else:
            details.append("  - none")
        self._item_details.setPlainText("\n".join(details))

    def _update_table_details(self) -> None:
        indexes = self._table_view.selectionModel().selectedRows()
        if not indexes:
            self._table_details.clear()
            return
        table = self._table_model.row_at(indexes[0].row())
        if table is None:
            self._table_details.clear()
            return
        details = [
            f"File: {table.file_name}",
            f"Path: {table.path}",
            f"Domain: {table.domain}",
            f"Subdomain: {table.subdomain}",
            f"Package Group: {table.package_group}",
            f"Header Pair (.pabgh): {'Yes' if table.has_header_pair else 'No'}",
        ]
        self._table_details.setPlainText("\n".join(details))

    def _export_cache(self) -> None:
        if self._data is None:
            return
        out_dir = Path(__file__).resolve().parents[1] / "exports" / "item_catalog"
        paths = write_catalog_exports(self._data, out_dir)
        self._status.showMessage(f"Exported cache to {out_dir} ({len(paths)} files)")
