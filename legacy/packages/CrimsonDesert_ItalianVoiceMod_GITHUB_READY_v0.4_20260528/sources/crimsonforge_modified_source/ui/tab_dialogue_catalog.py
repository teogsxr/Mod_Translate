"""Enterprise dialogue browser for live Crimson Desert game dialogue."""

from __future__ import annotations

from collections import Counter, defaultdict
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

from core.dialogue_catalog import (
    DialogueCatalogData,
    DialogueConversationRecord,
    DialogueRecord,
    DialogueSpeakerRecord,
    build_dialogue_catalog,
    build_dialogue_catalog_cached,
    write_dialogue_exports,
)
from core.vfs_manager import VfsManager
from utils.thread_worker import FunctionWorker


_LINE_HEADERS = [
    "Story",
    "Chapter",
    "Conversation",
    "Scene",
    "Speaker",
    "Type",
    "Line",
    "Family",
    "Preview",
]

_CATEGORY_COLORS = {
    "Cutscene": "#f38ba8",
    "Quest Scene": "#fab387",
    "Quest Dialogue": "#f9e2af",
    "Memory Scene": "#94e2d5",
    "Scene Dialogue": "#89b4fa",
    "AI Dialogue": "#cba6f7",
}


class _DialogueLineModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._all_records: list[DialogueRecord] = []
        self._filtered: list[int] = []
        self._search = ""
        self._speaker_bucket = "All"
        self._confidence = "All"
        self._text_mode = "Non-empty only"
        self._scope: dict[str, str] = {}

    def set_records(self, records: list[DialogueRecord]) -> None:
        self.beginResetModel()
        self._all_records = records
        self._refilter()
        self.endResetModel()

    def set_filters(
        self,
        *,
        search: str,
        speaker_bucket: str,
        confidence: str,
        text_mode: str,
        scope: dict[str, str],
    ) -> None:
        self.beginResetModel()
        self._search = search.strip().lower()
        self._speaker_bucket = speaker_bucket
        self._confidence = confidence
        self._text_mode = text_mode
        self._scope = dict(scope)
        self._refilter()
        self.endResetModel()

    def _matches_scope(self, record: DialogueRecord) -> bool:
        if not self._scope:
            return True

        mode = self._scope.get("mode", "")
        if mode == "story":
            story_group = self._scope.get("story_group")
            chapter_label = self._scope.get("chapter_label")
            conversation_key = self._scope.get("conversation_key")
            scene_key = self._scope.get("scene_key")
            if story_group and record.story_group != story_group:
                return False
            if chapter_label and record.chapter_label != chapter_label:
                return False
            if conversation_key and record.conversation_key != conversation_key:
                return False
            if scene_key and record.scene_key != scene_key:
                return False
            return True

        if mode == "speaker":
            speaker_bucket = self._scope.get("speaker_bucket")
            speaker_key = self._scope.get("speaker_key")
            if speaker_bucket and record.speaker_bucket != speaker_bucket:
                return False
            if speaker_key and record.speaker_key != speaker_key:
                return False
            return True

        if mode == "family":
            category = self._scope.get("category")
            subcategory = self._scope.get("subcategory")
            family = self._scope.get("family")
            if category and record.category != category:
                return False
            if subcategory and record.subcategory != subcategory:
                return False
            if family and record.family != family:
                return False
            return True

        return True

    def _refilter(self) -> None:
        filtered: list[int] = []
        for idx, record in enumerate(self._all_records):
            if self._speaker_bucket != "All" and record.speaker_bucket != self._speaker_bucket:
                continue
            if self._confidence != "All" and record.speaker_confidence != self._confidence.lower():
                continue
            if self._text_mode == "Non-empty only" and not record.text_clean:
                continue
            if self._search and self._search not in record.search_text:
                continue
            if not self._matches_scope(record):
                continue
            filtered.append(idx)
        self._filtered = filtered

    def row_at(self, row: int) -> DialogueRecord | None:
        if 0 <= row < len(self._filtered):
            return self._all_records[self._filtered[row]]
        return None

    def rowCount(self, parent=QModelIndex()) -> int:
        return len(self._filtered)

    def columnCount(self, parent=QModelIndex()) -> int:
        return len(_LINE_HEADERS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return _LINE_HEADERS[section]
        return None

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        record = self.row_at(index.row())
        if record is None:
            return None

        if role == Qt.DisplayRole:
            if index.column() == 0:
                return record.story_group
            if index.column() == 1:
                return record.chapter_label
            if index.column() == 2:
                return record.conversation_label or record.conversation_key
            if index.column() == 3:
                return record.scene_label or record.scene_key
            if index.column() == 4:
                return record.speaker_display
            if index.column() == 5:
                return record.dialogue_type
            if index.column() == 6:
                return "" if record.line_index is None else str(record.line_index)
            if index.column() == 7:
                return record.family_display
            if index.column() == 8:
                return record.text_clean.replace("\n", " ")[:140]

        if role == Qt.ToolTipRole:
            text = record.text_clean or "[empty]"
            return (
                f"Key: {record.key}\n"
                f"Story: {record.story_group}\n"
                f"Chapter: {record.chapter_label}\n"
                f"Conversation: {record.conversation_key}\n"
                f"Scene: {record.scene_key}\n"
                f"Speaker: {record.speaker_display} ({record.speaker_confidence})\n\n"
                f"{text}"
            )

        if role == Qt.ForegroundRole:
            color = _CATEGORY_COLORS.get(record.category)
            if color:
                return QBrush(QColor(color))
            if record.speaker_confidence == "unknown":
                return QBrush(QColor("#9399b2"))

        if role == Qt.UserRole:
            return record
        return None

    def sort(self, column, order=Qt.AscendingOrder) -> None:
        reverse = order == Qt.DescendingOrder
        self.beginResetModel()
        key_funcs = {
            0: lambda record: record.story_group.lower(),
            1: lambda record: record.chapter_label.lower(),
            2: lambda record: record.conversation_label.lower(),
            3: lambda record: record.scene_label.lower(),
            4: lambda record: record.speaker_display.lower(),
            5: lambda record: record.dialogue_type.lower(),
            6: lambda record: record.line_index if record.line_index is not None else -1,
            7: lambda record: record.family_display.lower(),
            8: lambda record: record.text_clean.lower(),
        }
        key_fn = key_funcs.get(column, key_funcs[0])
        self._filtered.sort(key=lambda idx: key_fn(self._all_records[idx]), reverse=reverse)
        self.endResetModel()

    @property
    def filtered_count(self) -> int:
        return len(self._filtered)


class DialogueCatalogTab(QWidget):
    # Cross-thread signals for the lazy-init blocking path. When the
    # main window's lazy-init worker calls initialize_from_game from
    # a worker thread, we run the build inline on that thread and
    # emit one of these signals to marshal the result back to the
    # UI thread. Default Qt.QueuedConnection ensures the slots run
    # on whichever thread the receiver lives on (the UI thread).
    _lazy_init_finished = Signal(object)  # DialogueCatalogData
    _lazy_init_error = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._packages_path = ""
        self._worker: FunctionWorker | None = None
        self._data: DialogueCatalogData | None = None
        self._conversation_map: dict[str, DialogueConversationRecord] = {}
        self._speaker_map: dict[str, DialogueSpeakerRecord] = {}
        self._conversation_lines: dict[str, list[DialogueRecord]] = defaultdict(list)
        self._speaker_lines: dict[str, list[DialogueRecord]] = defaultdict(list)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(180)
        self._search_timer.timeout.connect(self._apply_filters)
        self._line_model = _DialogueLineModel(self)
        self._lazy_init_finished.connect(self._on_worker_finished)
        self._lazy_init_error.connect(self._on_worker_error)
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        toolbar = QHBoxLayout()
        self._summary_label = QLabel("Load a game to build the enterprise dialogue catalog.")
        toolbar.addWidget(self._summary_label, 1)

        toolbar.addWidget(QLabel("Search:"))
        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText(
            "Search text, keys, story groups, scenes, speakers, families, or StaticInfo mentions..."
        )
        self._search_input.textChanged.connect(lambda _: self._search_timer.start())
        toolbar.addWidget(self._search_input, 2)

        toolbar.addWidget(QLabel("Text:"))
        self._text_mode_combo = QComboBox()
        self._text_mode_combo.addItems(["Non-empty only", "All"])
        self._text_mode_combo.currentTextChanged.connect(lambda _: self._apply_filters())
        toolbar.addWidget(self._text_mode_combo)

        toolbar.addWidget(QLabel("Speaker Bucket:"))
        self._speaker_bucket_combo = QComboBox()
        self._speaker_bucket_combo.addItem("All")
        self._speaker_bucket_combo.currentTextChanged.connect(lambda _: self._apply_filters())
        toolbar.addWidget(self._speaker_bucket_combo)

        toolbar.addWidget(QLabel("Confidence:"))
        self._confidence_combo = QComboBox()
        self._confidence_combo.addItems(["All", "Scene Character", "Role", "Family", "Unknown"])
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

        splitter = QSplitter(Qt.Horizontal)
        layout.addWidget(splitter, 1)

        splitter.addWidget(self._build_navigation_panel())
        splitter.addWidget(self._build_main_panel())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([340, 1080])

        self._status = QStatusBar()
        self._status.showMessage("Idle")
        layout.addWidget(self._status)

    def _build_navigation_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)

        self._scope_label = QLabel("Browse all dialogue.")
        self._scope_label.setWordWrap(True)
        layout.addWidget(self._scope_label)

        self._nav_tabs = QTabWidget()
        self._nav_tabs.currentChanged.connect(lambda _: self._apply_filters())
        layout.addWidget(self._nav_tabs, 1)

        self._story_tree = QTreeWidget()
        self._story_tree.setHeaderLabels(["Story / Chapter / Conversation / Scene", "Count"])
        self._story_tree.setUniformRowHeights(True)
        self._story_tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self._story_tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self._story_tree.itemSelectionChanged.connect(self._apply_filters)
        self._nav_tabs.addTab(self._story_tree, "Story")

        self._speaker_tree = QTreeWidget()
        self._speaker_tree.setHeaderLabels(["Speaker / Bucket", "Count"])
        self._speaker_tree.setUniformRowHeights(True)
        self._speaker_tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self._speaker_tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self._speaker_tree.itemSelectionChanged.connect(self._apply_filters)
        self._nav_tabs.addTab(self._speaker_tree, "Speakers")

        self._family_tree = QTreeWidget()
        self._family_tree.setHeaderLabels(["Category / Family", "Count"])
        self._family_tree.setUniformRowHeights(True)
        self._family_tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self._family_tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self._family_tree.itemSelectionChanged.connect(self._apply_filters)
        self._nav_tabs.addTab(self._family_tree, "Families")

        return panel

    def _build_main_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)

        self._count_label = QLabel("0 dialogue lines")
        layout.addWidget(self._count_label)

        splitter = QSplitter(Qt.Vertical)
        layout.addWidget(splitter, 1)

        table_host = QWidget()
        table_layout = QVBoxLayout(table_host)
        table_layout.setContentsMargins(0, 0, 0, 0)

        self._line_table = QTableView()
        self._line_table.setModel(self._line_model)
        self._line_table.setSortingEnabled(True)
        self._line_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._line_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._line_table.setAlternatingRowColors(True)
        self._line_table.verticalHeader().setVisible(False)
        self._line_table.verticalHeader().setMinimumSectionSize(22)
        self._line_table.verticalHeader().setDefaultSectionSize(24)
        self._line_table.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self._line_table.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        self._line_table.horizontalHeader().setStretchLastSection(True)
        self._line_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self._line_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self._line_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self._line_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self._line_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self._line_table.horizontalHeader().setSectionResizeMode(8, QHeaderView.Stretch)
        self._line_table.selectionModel().selectionChanged.connect(self._update_details)
        table_layout.addWidget(self._line_table)
        splitter.addWidget(table_host)

        self._details_tabs = QTabWidget()
        splitter.addWidget(self._details_tabs)

        self._line_details = QTextEdit()
        self._line_details.setReadOnly(True)
        self._line_details.setPlaceholderText("Select a line to inspect its full game-backed metadata.")
        self._details_tabs.addTab(self._line_details, "Line Details")

        self._conversation_details = QTextEdit()
        self._conversation_details.setReadOnly(True)
        self._conversation_details.setPlaceholderText("Conversation transcript will appear here.")
        self._details_tabs.addTab(self._conversation_details, "Conversation")

        self._speaker_details = QTextEdit()
        self._speaker_details.setReadOnly(True)
        self._speaker_details.setPlaceholderText("Speaker overview will appear here.")
        self._details_tabs.addTab(self._speaker_details, "Speaker")

        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([540, 320])
        return panel

    def initialize_from_game(self, vfs: VfsManager) -> None:
        self.initialize_from_game_path(vfs.packages_path)

    def initialize_from_game_path(self, packages_path: str) -> None:
        if not packages_path:
            return
        if self._packages_path == packages_path and self._data is not None:
            return
        self._packages_path = packages_path

        # Two callers reach here:
        #   1. The "Refresh From Game" button on the UI thread.
        #      We must NOT block it — kick off an inner worker and
        #      let the UI keep painting.
        #   2. The MainWindow lazy-tab dispatcher running on its
        #      own worker thread. If we kick off an *inner* worker
        #      and return immediately, the dispatcher reports the
        #      tab "ready" while the build is still running for
        #      another 30-90 s — the user sees the loading overlay
        #      vanish into a half-empty tab. We instead run the
        #      build inline on that worker thread and only return
        #      once the data is ready.
        ui_thread = QApplication.instance().thread() if QApplication.instance() else None
        if ui_thread is not None and QThread.currentThread() is not ui_thread:
            self._build_catalog_inline(packages_path)
        else:
            self._refresh_from_game()

    def _build_catalog_inline(self, packages_path: str) -> None:
        """Run the catalog build SYNCHRONOUSLY on the calling worker
        thread (e.g. the lazy-tab dispatcher). After the build, marshal
        the result back to the UI thread via a queued signal so widget
        mutation happens on the right thread.
        """
        try:
            vfs = VfsManager(packages_path)
            data = build_dialogue_catalog_cached(vfs)
            out_dir = Path(__file__).resolve().parents[1] / "exports" / "dialogue_catalog"
            try:
                write_dialogue_exports(data, out_dir)
            except Exception:
                # Export-writing isn't required for the in-app view —
                # log but don't fail the whole tab init if disk
                # is full or the exports directory is read-only.
                import logging
                logging.getLogger(__name__).exception(
                    "dialogue catalog export write failed"
                )
            self._lazy_init_finished.emit(data)
        except Exception as e:
            import logging
            logging.getLogger(__name__).exception(
                "dialogue catalog inline build failed"
            )
            self._lazy_init_error.emit(f"{type(e).__name__}: {e}")

    def _refresh_from_game(self) -> None:
        if not self._packages_path or self._worker is not None:
            return
        self._refresh_button.setEnabled(False)
        self._export_button.setEnabled(False)
        self._progress.setVisible(True)
        self._summary_label.setText("Building dialogue catalog from live game data...")
        self._status.showMessage("Building dialogue catalog...")
        self._worker = FunctionWorker(self._build_catalog_worker, self._packages_path)
        self._worker.progress.connect(self._on_worker_progress)
        self._worker.finished_result.connect(self._on_worker_finished)
        self._worker.error_occurred.connect(self._on_worker_error)
        self._worker.start()

    def _build_catalog_worker(self, worker: FunctionWorker, packages_path: str):
        def progress(message: str) -> None:
            worker.report_progress(0, message)

        vfs = VfsManager(packages_path)
        # Manual Refresh uses the cached builder too — first run
        # on a freshly patched game pays the build cost and
        # repopulates the cache; identical reruns are ~100 ms.
        data = build_dialogue_catalog_cached(vfs, progress_fn=progress)
        out_dir = Path(__file__).resolve().parents[1] / "exports" / "dialogue_catalog"
        write_dialogue_exports(data, out_dir)
        return data

    def _on_worker_progress(self, _pct: int, message: str) -> None:
        self._summary_label.setText(message)
        self._status.showMessage(message)

    def _on_worker_finished(self, data: DialogueCatalogData) -> None:
        self._worker = None
        self._data = data
        self._progress.setVisible(False)
        self._refresh_button.setEnabled(True)
        self._export_button.setEnabled(True)

        self._conversation_map = {conv.conversation_key: conv for conv in data.conversations}
        self._speaker_map = {speaker.speaker_key: speaker for speaker in data.speakers}
        self._conversation_lines = defaultdict(list)
        self._speaker_lines = defaultdict(list)
        for record in data.records:
            self._conversation_lines[record.conversation_key].append(record)
            self._speaker_lines[record.speaker_key].append(record)

        self._line_model.set_records(data.records)
        self._populate_speaker_bucket_combo()
        self._populate_story_tree()
        self._populate_speaker_tree()
        self._populate_family_tree()
        self._apply_filters()

        self._summary_label.setText(
            f"Loaded {len(data.records):,} dialogue lines, {len(data.conversations):,} conversations, "
            f"and {len(data.speakers):,} speaker profiles."
        )
        self._status.showMessage("Dialogue catalog ready")

    def _on_worker_error(self, message: str) -> None:
        self._worker = None
        self._progress.setVisible(False)
        self._refresh_button.setEnabled(True)
        self._summary_label.setText("Failed to build dialogue catalog.")
        self._status.showMessage(message)

    def _populate_speaker_bucket_combo(self) -> None:
        existing = self._speaker_bucket_combo.currentText()
        self._speaker_bucket_combo.blockSignals(True)
        self._speaker_bucket_combo.clear()
        self._speaker_bucket_combo.addItem("All")
        if self._data is not None:
            for bucket in sorted({record.speaker_bucket for record in self._data.records if record.speaker_bucket}):
                self._speaker_bucket_combo.addItem(bucket)
        index = self._speaker_bucket_combo.findText(existing)
        self._speaker_bucket_combo.setCurrentIndex(index if index >= 0 else 0)
        self._speaker_bucket_combo.blockSignals(False)

    def _populate_story_tree(self) -> None:
        self._story_tree.setUpdatesEnabled(False)
        try:
            self._story_tree.clear()
            if self._data is None:
                return
            self.__populate_story_tree_inner()
        finally:
            self._story_tree.setUpdatesEnabled(True)

    def __populate_story_tree_inner(self) -> None:
        root = QTreeWidgetItem(["All Story", str(len(self._data.records))])
        root.setData(0, Qt.UserRole, {"mode": "story"})
        self._story_tree.addTopLevelItem(root)

        story_items: dict[str, QTreeWidgetItem] = {}
        chapter_items: dict[tuple[str, str], QTreeWidgetItem] = {}
        story_counts = Counter(conversation.story_group for conversation in self._data.conversations for _ in range(conversation.line_count))
        chapter_counts = Counter(
            (conversation.story_group, conversation.chapter_label)
            for conversation in self._data.conversations
            for _ in range(conversation.line_count)
        )

        for conversation in self._data.conversations:
            story_group = conversation.story_group
            chapter_label = conversation.chapter_label

            story_item = story_items.get(story_group)
            if story_item is None:
                story_item = QTreeWidgetItem([story_group, str(story_counts[story_group])])
                story_item.setData(0, Qt.UserRole, {"mode": "story", "story_group": story_group})
                root.addChild(story_item)
                story_items[story_group] = story_item

            chapter_key = (story_group, chapter_label)
            chapter_item = chapter_items.get(chapter_key)
            if chapter_item is None:
                chapter_item = QTreeWidgetItem([chapter_label, str(chapter_counts[chapter_key])])
                chapter_item.setData(
                    0,
                    Qt.UserRole,
                    {"mode": "story", "story_group": story_group, "chapter_label": chapter_label},
                )
                story_item.addChild(chapter_item)
                chapter_items[chapter_key] = chapter_item

            label = conversation.conversation_label or conversation.conversation_key
            conversation_item = QTreeWidgetItem([label, str(conversation.line_count)])
            conversation_item.setData(
                0,
                Qt.UserRole,
                {
                    "mode": "story",
                    "story_group": story_group,
                    "chapter_label": chapter_label,
                    "conversation_key": conversation.conversation_key,
                },
            )
            chapter_item.addChild(conversation_item)

            conversation_lines = self._conversation_lines.get(conversation.conversation_key, [])
            scene_counts = Counter(record.scene_key for record in conversation_lines)
            scene_labels: dict[str, str] = {}
            ordered_scene_keys = []
            for record in conversation_lines:
                scene_labels.setdefault(record.scene_key, record.scene_label or record.scene_key)
                if record.scene_key not in ordered_scene_keys:
                    ordered_scene_keys.append(record.scene_key)

            for scene_key in ordered_scene_keys:
                scene_item = QTreeWidgetItem([scene_labels.get(scene_key, scene_key), str(scene_counts[scene_key])])
                scene_item.setData(
                    0,
                    Qt.UserRole,
                    {
                        "mode": "story",
                        "story_group": story_group,
                        "chapter_label": chapter_label,
                        "conversation_key": conversation.conversation_key,
                        "scene_key": scene_key,
                    },
                )
                conversation_item.addChild(scene_item)

        self._story_tree.expandToDepth(1)
        self._story_tree.setCurrentItem(root)

    def _populate_speaker_tree(self) -> None:
        self._speaker_tree.setUpdatesEnabled(False)
        try:
            self._speaker_tree.clear()
            if self._data is None:
                return
            self.__populate_speaker_tree_inner()
        finally:
            self._speaker_tree.setUpdatesEnabled(True)

    def __populate_speaker_tree_inner(self) -> None:
        root = QTreeWidgetItem(["All Speakers", str(len(self._data.records))])
        root.setData(0, Qt.UserRole, {"mode": "speaker"})
        self._speaker_tree.addTopLevelItem(root)

        bucket_counts = Counter(record.speaker_bucket for record in self._data.records)
        for bucket in sorted(bucket_counts):
            bucket_item = QTreeWidgetItem([bucket, str(bucket_counts[bucket])])
            bucket_item.setData(0, Qt.UserRole, {"mode": "speaker", "speaker_bucket": bucket})
            root.addChild(bucket_item)
            for speaker in [speaker for speaker in self._data.speakers if speaker.speaker_bucket == bucket]:
                speaker_item = QTreeWidgetItem([speaker.speaker_display, str(speaker.line_count)])
                speaker_item.setData(
                    0,
                    Qt.UserRole,
                    {
                        "mode": "speaker",
                        "speaker_bucket": bucket,
                        "speaker_key": speaker.speaker_key,
                    },
                )
                bucket_item.addChild(speaker_item)

        self._speaker_tree.expandToDepth(1)
        self._speaker_tree.setCurrentItem(root)

    def _populate_family_tree(self) -> None:
        self._family_tree.setUpdatesEnabled(False)
        try:
            self._family_tree.clear()
            if self._data is None:
                return
            self.__populate_family_tree_inner()
        finally:
            self._family_tree.setUpdatesEnabled(True)

    def __populate_family_tree_inner(self) -> None:
        root = QTreeWidgetItem(["All Dialogue", str(len(self._data.records))])
        root.setData(0, Qt.UserRole, {"mode": "family"})
        self._family_tree.addTopLevelItem(root)

        category_counts = Counter(record.category for record in self._data.records)
        subcategory_counts = Counter((record.category, record.subcategory) for record in self._data.records)
        family_counts = Counter((record.category, record.subcategory, record.family) for record in self._data.records)

        for category in sorted(category_counts):
            category_item = QTreeWidgetItem([category, str(category_counts[category])])
            category_item.setData(0, Qt.UserRole, {"mode": "family", "category": category})
            root.addChild(category_item)

            subcategories = sorted(key[1] for key in subcategory_counts if key[0] == category)
            for subcategory in dict.fromkeys(subcategories):
                sub_item = QTreeWidgetItem([subcategory, str(subcategory_counts[(category, subcategory)])])
                sub_item.setData(
                    0,
                    Qt.UserRole,
                    {"mode": "family", "category": category, "subcategory": subcategory},
                )
                category_item.addChild(sub_item)

                families = sorted(key[2] for key in family_counts if key[0] == category and key[1] == subcategory)
                for family in dict.fromkeys(families):
                    family_item = QTreeWidgetItem([family, str(family_counts[(category, subcategory, family)])])
                    family_item.setData(
                        0,
                        Qt.UserRole,
                        {"mode": "family", "category": category, "subcategory": subcategory, "family": family},
                    )
                    sub_item.addChild(family_item)

        self._family_tree.expandToDepth(1)
        self._family_tree.setCurrentItem(root)

    def _active_scope(self) -> dict[str, str]:
        current = self._nav_tabs.currentWidget()
        if current is self._story_tree:
            item = self._story_tree.currentItem()
        elif current is self._speaker_tree:
            item = self._speaker_tree.currentItem()
        else:
            item = self._family_tree.currentItem()
        return dict(item.data(0, Qt.UserRole) or {}) if item else {}

    def _scope_description(self, scope: dict[str, str]) -> str:
        if not scope:
            return "Browse all dialogue."
        mode = scope.get("mode")
        if mode == "story":
            parts = [scope.get("story_group", "All Story")]
            if scope.get("chapter_label"):
                parts.append(scope["chapter_label"])
            if scope.get("conversation_key"):
                conversation = self._conversation_map.get(scope["conversation_key"])
                parts.append(conversation.conversation_label if conversation else scope["conversation_key"])
            if scope.get("scene_key"):
                parts.append(scope["scene_key"])
            return "Story scope: " + " > ".join(part for part in parts if part)
        if mode == "speaker":
            if scope.get("speaker_key"):
                return f"Speaker scope: {scope['speaker_key']}"
            if scope.get("speaker_bucket"):
                return f"Speaker scope: {scope['speaker_bucket']}"
            return "Speaker scope: All speakers"
        if mode == "family":
            parts = [scope.get("category", "All Dialogue")]
            if scope.get("subcategory"):
                parts.append(scope["subcategory"])
            if scope.get("family"):
                parts.append(scope["family"])
            return "Family scope: " + " > ".join(parts)
        return "Browse all dialogue."

    def _apply_filters(self) -> None:
        if not hasattr(self, "_count_label"):
            return
        scope = self._active_scope()
        self._scope_label.setText(self._scope_description(scope))
        self._line_model.set_filters(
            search=self._search_input.text(),
            speaker_bucket=self._speaker_bucket_combo.currentText(),
            confidence=self._confidence_combo.currentText(),
            text_mode=self._text_mode_combo.currentText(),
            scope=scope,
        )
        self._count_label.setText(f"{self._line_model.filtered_count:,} dialogue lines in current view")
        if self._line_model.filtered_count:
            self._line_table.selectRow(0)
        else:
            self._line_details.clear()
            self._conversation_details.clear()
            self._speaker_details.clear()

    def _update_details(self) -> None:
        indexes = self._line_table.selectionModel().selectedRows()
        if not indexes:
            self._line_details.clear()
            self._conversation_details.clear()
            self._speaker_details.clear()
            return

        record = self._line_model.row_at(indexes[0].row())
        if record is None:
            self._line_details.clear()
            self._conversation_details.clear()
            self._speaker_details.clear()
            return

        self._line_details.setPlainText(self._format_line_details(record))
        self._conversation_details.setPlainText(self._format_conversation_details(record))
        self._speaker_details.setPlainText(self._format_speaker_details(record))

    def _format_line_details(self, record: DialogueRecord) -> str:
        mention_lines = [f"  - {m.kind}: {m.token} -> {m.label}" for m in record.mentions] or ["  - none"]
        return "\n".join(
            [
                f"Key: {record.key}",
                f"Category: {record.category}",
                f"Subcategory: {record.subcategory}",
                f"Family: {record.family} ({record.family_display})",
                "",
                f"Story Group: {record.story_group}",
                f"Chapter: {record.chapter_label} [{record.chapter_code or '-'}]",
                f"Conversation: {record.conversation_label or record.conversation_key}",
                f"Conversation Key: {record.conversation_key}",
                f"Scene: {record.scene_label or record.scene_key}",
                f"Scene Key: {record.scene_key}",
                f"Scene Group: {record.scene_group or '-'}",
                f"Line Index: {record.line_index if record.line_index is not None else '-'}",
                "",
                f"Speaker Display: {record.speaker_display}",
                f"Speaker Name: {record.speaker_name or '-'}",
                f"Speaker Role: {record.speaker_role or '-'}",
                f"Speaker Bucket: {record.speaker_bucket}",
                f"Speaker Slot: {record.speaker_slot if record.speaker_slot is not None else '-'}",
                f"Speaker Confidence: {record.speaker_confidence}",
                "",
                "Mentions:",
                *mention_lines,
                "",
                "Text:",
                record.text_clean or "[empty]",
            ]
        )

    def _format_conversation_details(self, record: DialogueRecord) -> str:
        conversation = self._conversation_map.get(record.conversation_key)
        lines = self._conversation_lines.get(record.conversation_key, [])
        header = []
        if conversation is not None:
            header.extend(
                [
                    f"Conversation: {conversation.conversation_label or conversation.conversation_key}",
                    f"Story Group: {conversation.story_group}",
                    f"Chapter: {conversation.chapter_label}",
                    f"Family: {conversation.family_display}",
                    f"Line Count: {conversation.line_count}",
                    f"Scene Count: {conversation.scene_count}",
                    f"Speakers: {', '.join(conversation.speaker_labels) or '-'}",
                    "",
                    "Transcript:",
                ]
            )
        else:
            header.extend([f"Conversation: {record.conversation_key}", "", "Transcript:"])

        transcript = []
        for line in lines:
            index = "----" if line.line_index is None else f"{line.line_index:04d}"
            text = line.text_clean or "[empty]"
            transcript.append(f"{index}  {line.speaker_display}: {text}")
        return "\n".join(header + transcript)

    def _format_speaker_details(self, record: DialogueRecord) -> str:
        speaker = self._speaker_map.get(record.speaker_key)
        lines = self._speaker_lines.get(record.speaker_key, [])
        preview_lines = []
        for line in lines[:12]:
            preview = line.text_clean.replace("\n", " ")
            preview_lines.append(f"- {line.conversation_label or line.conversation_key}: {preview[:120]}")

        if speaker is None:
            return "\n".join(
                [
                    f"Speaker: {record.speaker_display}",
                    f"Confidence: {record.speaker_confidence}",
                    "",
                    "No speaker aggregate available.",
                ]
            )

        return "\n".join(
            [
                f"Speaker: {speaker.speaker_display}",
                f"Bucket: {speaker.speaker_bucket}",
                f"Confidence: {speaker.speaker_confidence}",
                f"Line Count: {speaker.line_count}",
                f"Conversation Count: {speaker.conversation_count}",
                f"Family Count: {speaker.family_count}",
                f"Story Groups: {', '.join(speaker.story_groups) or '-'}",
                "",
                "Sample Lines:",
                *(preview_lines or ["- none"]),
            ]
        )

    def _export_cache(self) -> None:
        if self._data is None:
            return
        out_dir = Path(__file__).resolve().parents[1] / "exports" / "dialogue_catalog"
        paths = write_dialogue_exports(self._data, out_dir)
        self._status.showMessage(f"Exported cache to {out_dir} ({len(paths)} outputs)")
