"""Enterprise Explorer tab - unified Unpack + Browse + Edit.

Three-panel layout:
  Left:   Archive file list (QTableView + QAbstractTableModel = virtual rows)
  Center: Preview pane (click archive file = instant preview from PAZ)
  Right:  Text editor (for editable files)

Performance: Archive list uses QAbstractTableModel — only visible rows
are rendered. Filtering 1.45M files is instant (data stays in Python
lists, Qt only asks for visible rows). No QTreeWidgetItem objects.
"""

import os
import tempfile
import time
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QComboBox, QSplitter, QTableView, QHeaderView,
    QAbstractItemView, QApplication, QCheckBox, QMenu,
)
from PySide6.QtCore import (
    Qt, Signal, QTimer, QAbstractTableModel, QModelIndex,
)
from PySide6.QtGui import QColor, QBrush, QKeySequence, QShortcut

from core.vfs_manager import VfsManager
from core.pamt_parser import PamtData, PamtFileEntry
from core.file_detector import detect_file_type, get_syntax_type, is_text_file
from core.item_index import build_item_index
from ui.widgets.preview_pane import PreviewPane
from ui.widgets.search_history_line_edit import SearchHistoryLineEdit
from ui.widgets.syntax_editor import SyntaxEditor
from ui.widgets.progress_widget import ProgressWidget
from ui.dialogs.file_picker import pick_directory, pick_file, pick_save_file
from ui.dialogs.explorer_workbench_dialog import ExplorerWorkbenchDialog
from ui.dialogs.confirmation import show_error, show_info, confirm_action
from utils.thread_worker import FunctionWorker
from utils.platform_utils import format_file_size
from utils.logger import get_logger
from utils import text_search

logger = get_logger("ui.tab_explorer")

ALL_PACKAGES = "All Packages"

FILE_TYPE_FILTERS = {
    "All Files": set(),
    "3D Meshes": {".pam", ".pamlod", ".pac", ".pab", ".pabc", ".pami", ".meshinfo"},
    "Textures": {".dds", ".png", ".jpg", ".jpeg", ".bmp", ".tga", ".webp", ".gif"},
    "Audio": {".wav", ".ogg", ".mp3", ".wem", ".bnk", ".pasound", ".flac", ".aac"},
    "Video": {".mp4", ".webm", ".avi", ".mkv", ".bk2", ".bik", ".usm"},
    "Animation": {".paa", ".paa_metabin", ".hkx", ".motionblending"},
    "Localization": {".paloc"},
    "UI / Web": {".css", ".html", ".thtml", ".xml", ".json", ".uianiminit"},
    "Materials": {".mi", ".material", ".technique", ".impostor"},
    "Fonts": {".ttf", ".otf", ".woff", ".woff2"},
    "Effects": {".pae", ".paem"},
    "Level / World": {".palevel", ".levelinfo", ".prefab", ".nav", ".road", ".roadsector", ".roadidx"},
    "Game Data": {".pabgb", ".pabgh", ".binarygimmick", ".binarystring", ".questgaugecount"},
    "Sequencer": {".paseq", ".paseqc", ".paseqh", ".seqmt", ".pastage", ".paschedule", ".paschedulepath"},
    "Physics": {".pbd", ".pat"},
    "Shaders": {".padxil"},
    "Splines": {".spline", ".spline2d"},
}

_COL_FILE = 0
_COL_SIZE = 1
_COL_TYPE = 2
_COL_PKG = 3
_COL_COUNT = 4
_HEADERS = ["File", "Size", "Type", "Pkg"]
_ITEM_MODEL_SUFFIXES = (
    "_l", "_r", "_u", "_s", "_t",
    "_index01", "_index02", "_index03",
)
_ITEM_MODEL_PREFIXES = (
    "itemicon_prefab_",
    "itemicon_",
    "prefab_",
)


class _ArchiveRow:
    """Lightweight data holder for one archive file entry. No Qt objects.

    Extension and path_lower are pre-computed for instant filtering.
    type_desc is lazy-computed on first access (only visible rows need it).
    """
    __slots__ = ("entry", "group", "ext", "path_lower", "stem_lower",
                 "name_lower", "size_raw",
                 "_size_str", "_type_desc", "checked", "search_extra",
                 "search_display", "_display_tokens")

    def __init__(self, entry: PamtFileEntry, group: str):
        # ── PERF (2026-05-07) ──
        # Cold load constructs ~1.5 M of these rows. ``os.path.splitext``
        # and ``os.path.basename`` each go through ``os.path`` machinery
        # (sep detection, normpath bits) that's overkill for the canonical
        # forward-slash paths we get out of PAMTs. Inlining the splits via
        # ``rsplit`` runs ~3-4× faster on Python 3.12 and trims a couple
        # of seconds off the explorer's "All Packages" first-paint.
        self.entry = entry
        self.group = group
        path_lower = entry.path.lower()
        self.path_lower = path_lower
        # Basename: strip everything up to and including the last '/'.
        slash = path_lower.rfind("/")
        basename_lower = path_lower if slash < 0 else path_lower[slash + 1:]
        # Cache the basename — every complex-query evaluation reads it
        # against name_lower, and computing it ~1.5 M times per
        # keystroke via os.path.basename was a measurable cost.
        self.name_lower = basename_lower
        # Extension: include the leading '.' so callers that compare
        # against ``.pac`` etc. keep working unchanged.
        dot = basename_lower.rfind(".")
        self.ext = "" if dot <= 0 else basename_lower[dot:]
        self.stem_lower = basename_lower if dot <= 0 else basename_lower[:dot]
        self.size_raw = entry.orig_size
        self._size_str = None
        self._type_desc = None
        self.checked = True
        # Joined alias terms — display name + internal name + path tokens.
        # Used by Tier B's plain substring filter (no tokenization needed
        # because the alias builder includes both CamelCase and lowercase
        # forms of every term — see ``core.item_index.build_item_index``).
        self.search_extra = ""
        # Display-only alias terms. Used by Tier A token matching so the
        # filter can suppress same-set sibling pieces (cloak/hand/foot)
        # whose internal name shares the 'PlateArmor' CamelCase chain.
        self.search_display = ""
        # Lazy-built tokenized cache for ``search_display``. Populated on
        # first Tier A match check, reused on every subsequent keystroke
        # without re-running the tokenizer. Only the few thousand rows
        # with item aliases ever build this cache; the bulk of the
        # 1.4 M-row corpus uses Tier B's substring path which never
        # tokenizes.
        self._display_tokens: set[str] | None = None

    def _get_display_tokens(self) -> set[str]:
        if self._display_tokens is None:
            self._display_tokens = text_search.tokens_for(self.search_display)
        return self._display_tokens

    @property
    def size_str(self) -> str:
        if self._size_str is None:
            self._size_str = format_file_size(self.size_raw)
        return self._size_str

    @property
    def type_desc(self) -> str:
        if self._type_desc is None:
            self._type_desc = detect_file_type(self.entry.path).description
        return self._type_desc


def _match_complex_query(parsed, row, content_loader=None) -> bool:
    """Evaluate a multi-clause / boolean / field / wildcard query against ``row``.

    Hot-path fast: tokens use substring (not tokenization), so each
    keystroke stays C-fast even for the full 1.4 M-row corpus. Field
    qualifiers, wildcards, and phrases delegate to ``evaluate_clause``
    which already does the right thing without per-row tokenization.
    """
    name_lower = row.name_lower
    path_lower = row.path_lower
    extra_lower = row.search_extra
    ext_lower = row.ext
    size = row.size_raw

    for group in parsed.groups:
        if not group:
            continue
        ok = True
        for c in group:
            val = c.value
            kind = c.kind
            fld = c.field

            if kind == "token":
                # Substring on path/extra/name — no tokenization.
                hit = (val in path_lower
                       or (extra_lower and val in extra_lower)
                       or val in name_lower)
            elif kind == "phrase":
                hit = (val in path_lower
                       or (extra_lower and val in extra_lower)
                       or val in name_lower)
            elif kind == "wildcard":
                # Use the parse-time compiled regex — fnmatch.fnmatch
                # internally calls translate + re.match on EVERY
                # invocation, so doing 1.5 M of those per keystroke
                # cost ~6.6 s. The pre-compiled regex (cached on the
                # Clause via fnmatch.translate at parse_query time)
                # brings it down to ~400 ms — within the keystroke
                # budget for instant filtering.
                cre = c.compiled
                if cre is not None:
                    hit = (cre.match(path_lower) is not None
                           or cre.match(name_lower) is not None)
                else:
                    import fnmatch as _fn
                    hit = (_fn.fnmatch(path_lower, val)
                           or _fn.fnmatch(name_lower, val))
            elif kind == "field":
                if fld == "ext":
                    wanted = val if val.startswith(".") else f".{val}"
                    hit = ext_lower == wanted
                elif fld == "name":
                    hit = val in name_lower
                elif fld == "path":
                    hit = val in path_lower
                elif fld == "type":
                    hit = val in (row.type_desc.lower() if row._type_desc is not None else "")
                elif fld == "size":
                    op = ">"
                    num_str = val
                    if val.startswith((">=", "<=")):
                        op = val[:2]; num_str = val[2:]
                    elif val[:1] in "><":
                        op = val[0]; num_str = val[1:]
                    limit = text_search._size_to_bytes(num_str)
                    if limit is None:
                        hit = True
                    else:
                        hit = ((op == ">"  and size >  limit)
                               or (op == "<"  and size <  limit)
                               or (op == ">=" and size >= limit)
                               or (op == "<=" and size <= limit))
                elif fld == "content":
                    if content_loader is None:
                        hit = False
                    else:
                        try:
                            data = content_loader()
                            hit = val.encode("utf-8", "ignore") in data
                        except Exception:
                            hit = False
                else:
                    hit = True
            else:
                hit = True

            if c.negated:
                hit = not hit

            if not hit:
                ok = False
                break

        if ok:
            return True
    return False


class _ArchiveModel(QAbstractTableModel):
    """Virtual model for archive file list. Only visible rows are rendered.

    Holds ALL entries in _all_rows. Filtering produces _filtered which
    is just a list of indices into _all_rows. Zero copying, instant.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._all_rows: list[_ArchiveRow] = []
        self._filtered: list[int] = []
        self._ext_set: set = set()
        self._search_text = ""
        self._scoped_paths: set[str] | None = None
        # VFS handle — only needed when the user runs a ``content:`` query.
        # The parent tab calls ``set_vfs`` after archive load completes.
        self._vfs = None

    def set_vfs(self, vfs) -> None:
        self._vfs = vfs

    def set_data(self, rows: list[_ArchiveRow]):
        self.beginResetModel()
        self._all_rows = rows
        self._refilter()
        self.endResetModel()

    def set_filter(self, ext_set: set, search: str):
        self.beginResetModel()
        self._ext_set = ext_set
        self._search_text = search.strip().lower()
        self._refilter()
        self.endResetModel()

    def set_scope_paths(self, paths: list[str] | set[str] | None):
        self.beginResetModel()
        if paths:
            self._scoped_paths = {path.replace("\\", "/").lower() for path in paths}
        else:
            self._scoped_paths = None
        self._refilter()
        self.endResetModel()

    def _refilter(self):
        ext_set = self._ext_set
        search = self._search_text
        scoped_paths = self._scoped_paths
        if not ext_set and not search and not scoped_paths:
            self._filtered = list(range(len(self._all_rows)))
            return

        # Bare-extension shortcut: ``.dds`` (no space) or ``ext:.dds``
        # (no space after value) → super-fast path. Anything more complex
        # ("ext:.dds canta", ".dds canta") falls through to the parser.
        search_ext = ""
        if search.startswith("ext:") and " " not in search:
            search_ext = search[4:].strip()
            if search_ext and not search_ext.startswith("."):
                search_ext = "." + search_ext
            search = ""
        elif search.startswith(".") and " " not in search and len(search) < 15:
            search_ext = search
            search = ""

        # Parse remaining query into the enterprise form.
        parsed = text_search.parse_query(search)
        needs_content = parsed.needs_content()

        # Detect the "simple single token" case so we keep the v1.24.0
        # Tier A / Tier B fast path. Anything richer (booleans, fields,
        # wildcards, phrases, multiple tokens) goes through the parsed
        # evaluator — but with substring semantics, no per-row tokenization
        # of the 1.4 M-path corpus.
        is_simple_prefix = (
            len(parsed.groups) == 1
            and len(parsed.groups[0]) == 1
            and parsed.groups[0][0].kind == "token"
            and not parsed.groups[0][0].negated
        )

        tier_a: list[int] = []
        tier_b: list[int] = []
        all_hits: list[int] = []
        simple_token = (
            parsed.groups[0][0].value if is_simple_prefix else ""
        )
        simple_q_tokens = [simple_token] if simple_token else []

        # Pre-compute the cheap stuff the complex-query evaluator needs.
        # ``content_loader`` is built lazily per row only when the query
        # actually contains a content: clause, so the hot path stays
        # O(n) in row count, not O(n×m) where m is path length.
        vfs = self._vfs

        for i, row in enumerate(self._all_rows):
            if scoped_paths is not None and row.path_lower not in scoped_paths:
                continue
            if ext_set and row.ext not in ext_set:
                continue
            if search_ext and row.ext != search_ext:
                continue
            if parsed.is_empty():
                tier_b.append(i)
                continue

            if is_simple_prefix:
                # Tier A — display alias prefix match (only ~1% of rows
                # have a display alias, so this is mostly a no-op skip).
                if row.search_display and text_search.match_prefilter(
                    simple_q_tokens, row._get_display_tokens()
                ):
                    tier_a.append(i)
                    continue
                # Tier B — same C-fast substring as pre-1.24.0.
                if simple_token in row.path_lower or (
                    row.search_extra and simple_token in row.search_extra
                ):
                    tier_b.append(i)
                continue

            # Complex query path — substring semantics on tokens (no
            # per-row tokenization), full match for fields/wildcards/phrases.
            content_loader = None
            if needs_content and vfs is not None:
                content_loader = lambda r=row: vfs.read_entry_data(r.entry)
            if _match_complex_query(parsed, row, content_loader):
                all_hits.append(i)

        if all_hits:
            self._filtered = all_hits
        elif is_simple_prefix and tier_a:
            self._filtered = tier_a
        else:
            self._filtered = tier_a + tier_b

    def row_at(self, view_row: int) -> _ArchiveRow:
        if 0 <= view_row < len(self._filtered):
            return self._all_rows[self._filtered[view_row]]
        return None

    def get_checked_entries(self) -> list[PamtFileEntry]:
        return [self._all_rows[i].entry for i in self._filtered
                if self._all_rows[i].checked]

    def check_all(self, checked: bool):
        for i in self._filtered:
            self._all_rows[i].checked = checked
        if self._filtered:
            tl = self.index(0, 0)
            br = self.index(len(self._filtered) - 1, 0)
            self.dataChanged.emit(tl, br, [Qt.CheckStateRole])

    def rowCount(self, parent=QModelIndex()):
        return len(self._filtered)

    def columnCount(self, parent=QModelIndex()):
        return _COL_COUNT

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return _HEADERS[section]
        return None

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        row_idx = index.row()
        if row_idx >= len(self._filtered):
            return None
        row = self._all_rows[self._filtered[row_idx]]
        col = index.column()

        if role == Qt.DisplayRole:
            if col == _COL_FILE:
                return row.entry.path
            elif col == _COL_SIZE:
                return row.size_str
            elif col == _COL_TYPE:
                return row.type_desc
            elif col == _COL_PKG:
                return row.group

        elif role == Qt.CheckStateRole and col == _COL_FILE:
            return Qt.Checked if row.checked else Qt.Unchecked

        elif role == Qt.ToolTipRole and col == _COL_FILE:
            comp = "LZ4" if row.entry.compression_type == 2 else "None"
            enc = " + ChaCha20" if row.entry.encrypted else ""
            return (f"{row.entry.path}\n"
                    f"Size: {row.size_str} (orig: {row.entry.orig_size:,})\n"
                    f"Compression: {comp}{enc}\n"
                    f"Package: {row.group}")

        elif role == Qt.UserRole:
            return row

        elif role == Qt.UserRole + 1:
            return row.size_raw

        return None

    def setData(self, index, value, role=Qt.CheckStateRole):
        if role == Qt.CheckStateRole and index.column() == _COL_FILE:
            row = self._all_rows[self._filtered[index.row()]]
            row.checked = (value == Qt.Checked)
            self.dataChanged.emit(index, index, [Qt.CheckStateRole])
            return True
        return False

    def flags(self, index):
        base = Qt.ItemIsSelectable | Qt.ItemIsEnabled
        if index.column() == _COL_FILE:
            base |= Qt.ItemIsUserCheckable
        return base

    def sort(self, column, order=Qt.AscendingOrder):
        self.beginResetModel()
        reverse = (order == Qt.DescendingOrder)
        all_rows = self._all_rows
        if column == _COL_FILE:
            self._filtered.sort(key=lambda i: all_rows[i].entry.path.lower(), reverse=reverse)
        elif column == _COL_SIZE:
            self._filtered.sort(key=lambda i: all_rows[i].size_raw, reverse=reverse)
        elif column == _COL_TYPE:
            self._filtered.sort(key=lambda i: all_rows[i].type_desc, reverse=reverse)
        elif column == _COL_PKG:
            self._filtered.sort(key=lambda i: all_rows[i].group, reverse=reverse)
        self.endResetModel()

    @property
    def filtered_count(self) -> int:
        return len(self._filtered)

    @property
    def total_count(self) -> int:
        return len(self._all_rows)


class ExplorerTab(QWidget):
    """Unified Unpack + Browse + Edit enterprise explorer.

    Archive list uses QAbstractTableModel — instant filtering of 1M+ files.
    Click any file to preview from PAZ (no extraction needed).
    """

    files_extracted = Signal(str)

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self._config = config
        self._vfs: VfsManager = None
        self._all_groups: list[str] = []
        self._worker: FunctionWorker = None
        self._item_index = None
        # Pre-built catalog with display names + icon paths populated,
        # owned by the Explorer tab so the Catalog Browser dialog can
        # open instantly without re-running ``build_item_catalog_cached``.
        self._item_catalog = None
        self._catalog_loading = False
        self._catalog_worker: FunctionWorker | None = None
        self._current_edit_file = ""
        self._pending_mesh_data: dict[str, dict] = {}
        self._temp_dir = tempfile.mkdtemp(prefix="crimsonforge_preview_")
        # Baseline manager — snapshots the original PAC bytes on
        # first patch so every subsequent rebuild (preview, build-
        # to-folder, patch-to-game, restore) starts from a stable,
        # pre-modification source. This is what breaks the
        # "patch twice = corrupted mesh" feedback loop described in
        # the v1.22.9 bug report.
        from core.mesh_baseline_manager import MeshBaselineManager
        self._mesh_baseline = MeshBaselineManager()
        self._last_preview_path = ""
        self._last_preview_time = 0.0
        self._active_scope_title = ""
        self._pending_scope_request: tuple[list[str], str, str] | None = None
        self._workbench_dialog: ExplorerWorkbenchDialog | None = None
        # Lazy-built reverse index: PAC archive path -> prefabs that
        # reference it. Cached per tab instance so 'Open Matching
        # Prefab' is instant after first build.
        self._prefab_ref_index = None
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(250)
        self._search_timer.timeout.connect(self._apply_filter)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("Package:"))
        self._group_combo = QComboBox()
        self._group_combo.setToolTip("Select a package group to browse, or 'All Packages' to see everything.")
        self._group_combo.setMinimumWidth(160)
        self._group_combo.currentTextChanged.connect(self._on_group_changed)
        toolbar.addWidget(self._group_combo)

        toolbar.addWidget(QLabel("Type:"))
        self._type_filter = QComboBox()
        self._type_filter.setToolTip(
            "Filter files by type:\n"
            "  All Files — show everything\n"
            "  Localization — .paloc translation files\n"
            "  Stylesheets — .css theme files\n"
            "  HTML/Templates — .html, .thtml UI templates\n"
            "  XML/Config — .xml configuration files\n"
            "  Fonts — .ttf, .otf font files"
        )
        for name in FILE_TYPE_FILTERS:
            self._type_filter.addItem(name)
        self._type_filter.currentTextChanged.connect(lambda _: self._apply_filter())
        toolbar.addWidget(self._type_filter)

        toolbar.addWidget(QLabel("Search:"))
        self._search_input = SearchHistoryLineEdit(self._config, "explorer")
        self._search_input.setPlaceholderText(
            'e.g. canta plate armor   "exact phrase"   *.dds   ext:.pam   '
            'name:hel_0363   -eccanta   canta OR mace   size:>1mb'
        )
        self._search_input.setMinimumWidth(560)
        self._search_input.setToolTip(self._build_search_tooltip())
        self._search_input.textChanged.connect(lambda _: self._search_timer.start())
        toolbar.addWidget(self._search_input, 1)

        # Catalog browser button — opens a popup with the categorised
        # image grid of every iteminfo / multichange record (~7 k
        # canonical items pre-classified into Weapon -> Sword/Bow/Mace/...,
        # Armor -> Helm/Body/Hands/Feet/Back/..., Mount & Pet Gear,
        # Materials, etc.). Click an item to scope the file list below
        # to that item's PAC + sidecar files (same effect as typing the
        # item's stem into the search bar).
        from PySide6.QtWidgets import QStyle
        self._catalog_btn = QPushButton("Catalog")
        self._catalog_btn.setIcon(self.style().standardIcon(
            QStyle.StandardPixmap.SP_FileDialogContentsView
        ))
        self._catalog_btn.setToolTip(
            "Browse the full item catalog as an image grid.\n"
            "All armor, weapons, mount & pet gear and materials, "
            "categorised by Weapon -> Sword/Bow/Mace/...,\n"
            "Armor -> Helm/Body/Hands/Feet/Back/Face. "
            "Click an item to scope the file list below to its files."
        )
        self._catalog_btn.clicked.connect(self._open_catalog_browser)
        toolbar.addWidget(self._catalog_btn)

        # Search-syntax help button — small icon button next to Catalog.
        # Click opens a popup with the full enterprise search syntax
        # cheatsheet so users discover boolean operators, field
        # qualifiers, wildcards, and content-search without staring
        # at a tooltip.
        self._search_help_btn = QPushButton()
        self._search_help_btn.setIcon(self.style().standardIcon(
            QStyle.StandardPixmap.SP_FileDialogDetailedView
        ))
        self._search_help_btn.setFixedWidth(32)
        self._search_help_btn.setToolTip(
            "Search syntax cheatsheet — click to view all operators "
            "(AND/OR/NOT, phrases, wildcards, field filters, content search)."
        )
        self._search_help_btn.clicked.connect(self._show_search_syntax_help)
        toolbar.addWidget(self._search_help_btn)

        self._navigator_btn = QPushButton("Navigator")
        self._navigator_btn.setObjectName("primary")
        self._navigator_btn.setToolTip(
            "Open the live character/item/family navigator in a popup.\n"
            "Use it to filter Explorer to related files while keeping all normal Explorer actions."
        )
        self._navigator_btn.clicked.connect(self._open_workbench_dialog)
        toolbar.addWidget(self._navigator_btn)

        toolbar.addWidget(QLabel("Output:"))
        self._output_path = QLineEdit(self._config.get("general.last_output_path", ""))
        self._output_path.setPlaceholderText("Extraction output...")
        self._output_path.setToolTip("Directory where extracted files will be saved.\nFiles are placed in subdirectories matching the game's folder structure.")
        toolbar.addWidget(self._output_path, 1)
        out_browse = QPushButton("...")
        out_browse.setFixedWidth(30)
        out_browse.setToolTip("Browse for an output directory.")
        out_browse.clicked.connect(self._browse_output)
        toolbar.addWidget(out_browse)

        self._extract_sel_btn = QPushButton("Extract Selected")
        self._extract_sel_btn.setObjectName("primary")
        self._extract_sel_btn.setToolTip("Extract only the checked files to the output directory.\nFiles are automatically decrypted and decompressed.")
        self._extract_sel_btn.clicked.connect(self._extract_selected)
        toolbar.addWidget(self._extract_sel_btn)
        self._extract_all_btn = QPushButton("Extract All")
        self._extract_all_btn.setToolTip("Extract all visible (filtered) files to the output directory.")
        self._extract_all_btn.clicked.connect(self._extract_all)
        toolbar.addWidget(self._extract_all_btn)
        self._ship_mesh_btn = QPushButton("Ship to App")
        self._ship_mesh_btn.setObjectName("primary")
        self._ship_mesh_btn.setToolTip(
            "Generate a standalone ZIP installer for selected mesh mods.\n"
            "Select one or more .pac, .pam, or .pamlod rows, assign edited OBJ files,\n"
            "and package the patched PAZ/PAMT/PAPGT files for end users."
        )
        self._ship_mesh_btn.clicked.connect(self._ship_selected_meshes)
        toolbar.addWidget(self._ship_mesh_btn)
        layout.addLayout(toolbar)

        main_splitter = QSplitter(Qt.Horizontal)

        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(2)

        arch_header = QHBoxLayout()
        arch_label = QLabel("Archive Contents")
        arch_label.setStyleSheet("font-weight: bold; font-size: 12px; padding: 2px;")
        arch_header.addWidget(arch_label)
        self._scope_label = QLabel("")
        self._scope_label.setStyleSheet("color: #f9e2af; font-weight: 600; padding: 0 4px;")
        self._scope_label.setVisible(False)
        arch_header.addWidget(self._scope_label)
        arch_header.addStretch()
        self._clear_scope_btn = QPushButton("Clear Scope")
        self._clear_scope_btn.setToolTip("Clear the current character/item/family scope and show all matching files again.")
        self._clear_scope_btn.setVisible(False)
        self._clear_scope_btn.clicked.connect(self._clear_workbench_scope)
        arch_header.addWidget(self._clear_scope_btn)
        sel_all_btn = QPushButton("Select All")
        sel_all_btn.setObjectName("primary")
        sel_all_btn.setToolTip("Check all visible files for extraction.")
        sel_all_btn.clicked.connect(lambda: self._model.check_all(True))
        arch_header.addWidget(sel_all_btn)
        desel_btn = QPushButton("Deselect All")
        desel_btn.setToolTip("Uncheck all files.")
        desel_btn.clicked.connect(lambda: self._model.check_all(False))
        arch_header.addWidget(desel_btn)
        self._archive_count = QLabel("0 files")
        self._archive_count.setStyleSheet("color: #89b4fa; font-weight: 600; padding: 0 4px;")
        arch_header.addWidget(self._archive_count)
        left_layout.addLayout(arch_header)

        self._model = _ArchiveModel(self)
        self._view = QTableView()
        self._view.setModel(self._model)
        self._view.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._view.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._view.setAlternatingRowColors(True)
        self._view.setSortingEnabled(True)
        self._view.setShowGrid(False)
        self._view.verticalHeader().setVisible(False)
        self._view.verticalHeader().setDefaultSectionSize(24)
        self._view.verticalHeader().setMinimumSectionSize(24)
        self._view.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self._view.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        self._view.horizontalHeader().setSectionResizeMode(_COL_FILE, QHeaderView.Stretch)
        self._view.horizontalHeader().setSectionResizeMode(_COL_SIZE, QHeaderView.Fixed)
        self._view.horizontalHeader().setSectionResizeMode(_COL_TYPE, QHeaderView.Fixed)
        self._view.horizontalHeader().setSectionResizeMode(_COL_PKG, QHeaderView.Fixed)
        self._view.setColumnWidth(_COL_SIZE, 70)
        self._view.setColumnWidth(_COL_TYPE, 100)
        self._view.setColumnWidth(_COL_PKG, 50)
        self._view.clicked.connect(self._on_archive_clicked)
        self._view.doubleClicked.connect(self._on_archive_double_clicked)
        self._view.selectionModel().currentRowChanged.connect(self._on_archive_row_changed)
        self._view.setContextMenuPolicy(Qt.CustomContextMenu)
        self._view.customContextMenuRequested.connect(self._show_context_menu)
        # Space bar toggles check state of all selected rows
        space_shortcut = QShortcut(QKeySequence(Qt.Key_Space), self._view)
        space_shortcut.activated.connect(self._toggle_selected_checks)
        # Ctrl+C copies the filename (basename + extension) of every
        # selected row to the system clipboard. Scoped to the archive
        # view's focus context so it doesn't shadow Ctrl+C in the
        # preview pane, the editor, or the text fields above.
        copy_shortcut = QShortcut(QKeySequence.Copy, self._view)
        copy_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        copy_shortcut.activated.connect(self._copy_selected_filenames)
        left_layout.addWidget(self._view, 1)
        main_splitter.addWidget(left_panel)

        center_panel = QWidget()
        center_layout = QVBoxLayout(center_panel)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(2)

        preview_label = QLabel("Preview")
        preview_label.setStyleSheet("font-weight: bold; font-size: 12px; padding: 2px;")
        center_layout.addWidget(preview_label)

        self._preview = PreviewPane()
        center_layout.addWidget(self._preview, 1)
        main_splitter.addWidget(center_panel)

        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(2)

        edit_header = QHBoxLayout()
        self._edit_file_label = QLabel("Editor")
        self._edit_file_label.setStyleSheet("font-weight: bold; font-size: 12px; padding: 2px;")
        edit_header.addWidget(self._edit_file_label, 1)
        self._save_btn = QPushButton("Save")
        self._save_btn.setObjectName("primary")
        self._save_btn.setToolTip("Save the edited file back to its extracted location on disk.")
        self._save_btn.clicked.connect(self._save_file)
        edit_header.addWidget(self._save_btn)
        save_as_btn = QPushButton("Save As...")
        save_as_btn.setToolTip("Save the edited file with a new name or location.")
        save_as_btn.clicked.connect(self._save_as)
        edit_header.addWidget(save_as_btn)
        right_layout.addLayout(edit_header)

        self._editor = SyntaxEditor()
        right_layout.addWidget(self._editor, 1)

        edit_footer = QHBoxLayout()
        edit_footer.addWidget(QLabel("Syntax:"))
        self._syntax_combo = QComboBox()
        self._syntax_combo.setToolTip("Select syntax highlighting mode for the editor.")
        self._syntax_combo.addItems(["plain", "css", "html", "xml", "json", "paloc"])
        self._syntax_combo.currentTextChanged.connect(lambda s: self._editor.set_syntax(s))
        edit_footer.addWidget(self._syntax_combo)
        edit_footer.addWidget(QLabel("Enc:"))
        self._encoding_combo = QComboBox()
        self._encoding_combo.setToolTip("Character encoding used to read and write the file.\nMost game files use UTF-8. Use UTF-16 for some Asian language files.")
        self._encoding_combo.addItems(["utf-8", "utf-16", "latin-1", "ascii"])
        edit_footer.addWidget(self._encoding_combo)
        self._edit_status = QLabel("Ready")
        edit_footer.addStretch()
        edit_footer.addWidget(self._edit_status)
        right_layout.addLayout(edit_footer)
        main_splitter.addWidget(right_panel)

        main_splitter.setSizes([420, 400, 380])
        layout.addWidget(main_splitter, 1)

        self._progress = ProgressWidget()
        layout.addWidget(self._progress)

    def initialize_from_game(self, vfs: VfsManager, groups: list[str]) -> None:
        self._vfs = vfs
        self._all_groups = groups
        self._item_index = None
        self._pending_scope_request = None
        self._active_scope_title = ""
        self._model.set_vfs(vfs)
        self._model.set_scope_paths(None)
        self._update_scope_ui()
        self._load_item_index()
        self._group_combo.blockSignals(True)
        self._group_combo.clear()
        self._group_combo.addItem(ALL_PACKAGES)
        self._group_combo.addItems(groups)
        self._group_combo.blockSignals(False)
        self._group_combo.setCurrentText(ALL_PACKAGES)
        self._on_group_changed(ALL_PACKAGES)
        self._ensure_workbench_dialog().workbench.set_vfs(self._vfs)
        self._progress.set_status(f"Game loaded: {len(groups)} package groups")

    def reload_from_game(self, payload) -> None:
        """Refresh cached game state without dropping user selection.

        Called by :class:`core.game_reload_service.GameReloadService`
        when the user hits the Reload Game button. The key
        difference from :meth:`initialize_from_game` is that we
        try to KEEP the user's current package selection + any
        active scope/filter/search — only the underlying VFS and
        group list are replaced.

        ``payload`` is a :class:`ReloadPayload` but we accept it
        duck-typed so unit tests can pass a lightweight stand-in.
        """
        previous_group = self._group_combo.currentText()
        self._vfs = payload.vfs
        self._all_groups = list(payload.groups)
        self._model.set_vfs(payload.vfs)
        # Reset only the caches that derive from game state —
        # the user's filter / search / scope stay in place.
        self._item_index = None
        self._prefab_ref_index = None
        # Refresh the group combo, trying to restore the previous
        # selection if it still exists in the new group set.
        self._group_combo.blockSignals(True)
        self._group_combo.clear()
        self._group_combo.addItem(ALL_PACKAGES)
        self._group_combo.addItems(self._all_groups)
        restore = (
            previous_group
            if previous_group == ALL_PACKAGES
            or previous_group in self._all_groups
            else ALL_PACKAGES
        )
        self._group_combo.setCurrentText(restore)
        self._group_combo.blockSignals(False)
        # Rebuild the item-name index against the refreshed VFS.
        self._load_item_index()
        # Trigger a table refresh for the (possibly-restored) group.
        self._on_group_changed(self._group_combo.currentText())
        # Workbench dialog (if open) needs the fresh VFS too.
        self._ensure_workbench_dialog().workbench.set_vfs(self._vfs)
        self._progress.set_status(
            f"Game reloaded: {len(self._all_groups)} package groups"
        )

    def _load_item_index(self) -> None:
        """Build the item-name search index from live game data.

        ── PERF (2026-05-07) ──
        Profiling showed this step adds ~7.7 s to the cold-load on
        a real install (1.5 M file entries to scan). Previously it
        ran synchronously on the UI thread, freezing the Explorer
        before the user could see anything. Now the index is built
        on a background worker; the Explorer is interactive
        immediately and the search box reports "still building..."
        if the user types into it before the index is ready.

        Also kicks off the heavier ``build_item_catalog_cached``
        async — same rationale, used by the Catalog Browser dialog.
        """
        if not self._vfs:
            return

        # Run the index build on a worker thread. We capture the VFS
        # reference at scheduling time so subsequent VFS reloads
        # don't race with an in-flight build (the worker either
        # completes against the old VFS — fine, will be replaced on
        # the next reload — or it gets cancelled when a new build
        # supersedes it).
        target_vfs = self._vfs

        def _bg_build(worker, vfs=target_vfs):
            def _progress(message: str) -> None:
                if worker.is_cancelled():
                    raise RuntimeError("item-index build cancelled")
                worker.report_progress(0, message)
            try:
                idx = build_item_index(vfs, progress_fn=_progress)
                return {"ok": True, "index": idx}
            except Exception as e:
                logger.warning("Item search index unavailable: %s", e)
                return {"ok": False, "error": str(e)}

        def _on_done(result):
            # Only adopt the result if we still have the same VFS we
            # built against — avoids stomping on a fresher reload.
            if self._vfs is not target_vfs:
                return
            if result.get("ok"):
                self._item_index = result["index"]
                if self._item_index:
                    self._progress.set_status(
                        f"Item search ready: "
                        f"{len(self._item_index.items):,} items"
                    )
                    # ── Strict re-enrichment of already-built rows ──
                    # _load_all_packages and _load_item_index run in
                    # parallel. Packages load faster (~3s) than the
                    # index (~7s scanning 1.5M paths), so rows get
                    # built with self._item_index == None and end up
                    # with EMPTY ``search_extra`` / ``search_display``.
                    # Without this re-enrichment pass, the Catalog
                    # Browser finds items by alias ("canta") but the
                    # Explorer search returns 0 — same install, same
                    # query, two different answers (just4u's report).
                    # We rebuild aliases for every row now that the
                    # index is live; the lazy ``_display_tokens``
                    # cache is invalidated so the next keystroke
                    # re-tokenizes against fresh aliases.
                    self._reenrich_rows_with_item_index()
            else:
                self._item_index = None
                self._progress.set_status(
                    f"Item search unavailable: {result.get('error')}"
                )

        # Reuse the explorer's existing worker plumbing.
        worker = FunctionWorker(_bg_build)
        worker.progress.connect(
            lambda _pct, msg: self._progress.set_status(
                f"Building item search index... {msg}" if msg else ""
            ),
        )
        worker.finished_result.connect(_on_done)
        worker.error_occurred.connect(lambda err: logger.warning(
            "Item search index worker failed: %s", err))
        # Hold a reference so it isn't GC'd mid-flight.
        self._item_index_worker = worker
        worker.start()
        self._progress.set_status(
            "Item search index building in background...")

        # Kick the catalog build off in the background. The catalog
        # browser dialog needs the icon-paths-enriched catalog the
        # heavier `core.item_catalog.build_item_catalog_cached` builds;
        # doing it now (rather than at first dialog open) means the
        # user never sees a blank dialog while we crunch ~7,500 PAMT
        # icon entries against ~19,700 item records.
        self._kick_off_catalog_build()

    def _kick_off_catalog_build(self) -> None:
        """Schedule an async catalog rebuild and update button state."""
        self._item_catalog = None
        self._catalog_loading = True
        if hasattr(self, "_catalog_btn"):
            self._catalog_btn.setEnabled(False)
            self._catalog_btn.setToolTip("Catalog loading…")

        def _bg(_worker: FunctionWorker, vfs):
            from core.item_catalog import build_item_catalog_cached
            return build_item_catalog_cached(vfs)

        def _done(data):
            self._item_catalog = data
            self._catalog_loading = False
            if hasattr(self, "_catalog_btn"):
                self._catalog_btn.setEnabled(True)
                self._catalog_btn.setToolTip(
                    "Browse the full item catalog as an image grid.\n"
                    "All armor, weapons, mount & pet gear and materials, "
                    "categorised by Weapon -> Sword/Bow/Mace/...,\n"
                    "Armor -> Helm/Body/Hands/Feet/Back/Face. "
                    "Same two-tier search as the file list above."
                )

        def _failed(message: str):
            self._item_catalog = None
            self._catalog_loading = False
            if hasattr(self, "_catalog_btn"):
                self._catalog_btn.setEnabled(True)
                self._catalog_btn.setToolTip(f"Catalog unavailable: {message}")
            logger.warning("Catalog build failed: %s", message)

        # Hold a reference on the tab so the QThread isn't GC'd mid-run.
        self._catalog_worker = FunctionWorker(_bg, self._vfs)
        self._catalog_worker.finished_result.connect(_done)
        self._catalog_worker.error_occurred.connect(_failed)
        self._catalog_worker.start()

    def _build_row(self, entry: PamtFileEntry, group: str) -> _ArchiveRow:
        """Create one archive row and attach item-name aliases when available."""
        row = _ArchiveRow(entry, group)
        self._populate_row_aliases(row)
        return row

    def _populate_row_aliases(self, row) -> None:
        """Fill in ``row.search_extra`` / ``row.search_display`` from
        the current item index. Safe to call multiple times — overwrites
        previous values and resets the lazy ``_display_tokens`` cache.

        Pulled out of ``_build_row`` so the post-index re-enrichment
        pass and the per-row build path share one strict definition of
        "alias lookup". Without this both paths drift over time.
        """
        if not self._item_index or not self._item_index.model_base_aliases:
            return

        aliases = []
        display_aliases = []
        seen = set()
        seen_display = set()
        display_map = getattr(self._item_index, "model_display_aliases", None) or {}
        for key in self._candidate_item_alias_keys(row.stem_lower):
            alias = self._item_index.model_base_aliases.get(key)
            if alias and alias not in seen:
                seen.add(alias)
                aliases.append(alias)
            disp = display_map.get(key)
            if disp and disp not in seen_display:
                seen_display.add(disp)
                display_aliases.append(disp)
        row.search_extra = " ".join(aliases) if aliases else ""
        row.search_display = " ".join(display_aliases) if display_aliases else ""
        # Invalidate the lazy tokenized cache so the next match check
        # re-tokenizes against the freshly-populated search_display.
        row._display_tokens = None

    def _reenrich_rows_with_item_index(self) -> None:
        """Refresh search aliases on every row in the model.

        Runs when ``_load_item_index`` completes AFTER
        ``_load_all_packages`` (the common case on cold start because
        the packages worker finishes in ~3 s and the index worker in
        ~7 s). Without this pass the Explorer's search box stays
        keyed on path-only matching even after the index is live —
        the Catalog Browser would find items by display alias while
        the Explorer returned zero, on the same install.

        Iterates the model's ``_all_rows`` directly: re-enriching
        ~1.5 M rows in-place is ~120 ms vs. ~3 s to rebuild rows from
        scratch, and we keep stable row identity (no model reset, no
        flicker, no loss of check-state).
        """
        if not self._model:
            return
        rows = getattr(self._model, "_all_rows", None) or []
        if not rows:
            return
        # ``set_filter`` already records the most-recent search text;
        # re-running the filter post-enrichment surfaces any rows whose
        # newly-attached aliases now satisfy the active query.
        for row in rows:
            self._populate_row_aliases(row)
        self._apply_filter()

    def _candidate_item_alias_keys(self, stem_lower: str) -> list[str]:
        """Return normalized model keys that may map this row to an item name."""
        keys = []
        seen = set()

        def _add(value: str) -> None:
            if value and value not in seen:
                seen.add(value)
                keys.append(value)

        _add(stem_lower)

        for prefix in _ITEM_MODEL_PREFIXES:
            if stem_lower.startswith(prefix):
                _add(stem_lower[len(prefix):])

        snapshot = list(keys)
        for key in snapshot:
            for suffix in _ITEM_MODEL_SUFFIXES:
                if key.endswith(suffix) and len(key) > len(suffix) + 4:
                    _add(key[:-len(suffix)])

        return keys

    def _browse_output(self):
        path = pick_directory(self, "Select Output Directory")
        if path:
            self._output_path.setText(path)

    def _on_group_changed(self, group: str):
        if not self._vfs or not group:
            return
        try:
            if group == ALL_PACKAGES:
                # Async load — filter + scope applied in _on_all_packages_loaded
                self._load_all_packages()
            else:
                pamt = self._vfs.load_pamt(group)
                rows = [self._build_row(e, group) for e in pamt.file_entries]
                self._model.set_data(rows)
                self._apply_filter()
        except Exception as e:
            show_error(self, "Load Error", str(e))

    def _load_all_packages(self):
        """Load all packages in a background thread to keep UI responsive."""
        self._progress.set_progress(0, "Loading all packages...")
        self._view.setUpdatesEnabled(False)

        def _bg_load(worker: FunctionWorker, vfs, groups, build_row):
            all_rows = []
            total = len(groups)
            for i, group in enumerate(groups):
                if worker.is_cancelled():
                    return all_rows
                try:
                    pamt = vfs.load_pamt(group)
                    for entry in pamt.file_entries:
                        all_rows.append(build_row(entry, group))
                except Exception as e:
                    logger.warning("Error loading group %s: %s", group, e)
                if (i + 1) % 3 == 0 or (i + 1) == total:
                    pct = int(((i + 1) / total) * 100)
                    worker.report_progress(pct, f"Loading group {group} ({i+1}/{total})...")
            return all_rows

        w = FunctionWorker(_bg_load, self._vfs, self._all_groups, self._build_row)
        w.progress.connect(lambda pct, msg: self._progress.set_progress(pct, msg))
        w.finished_result.connect(self._on_all_packages_loaded)
        w.error_occurred.connect(lambda err: (
            self._view.setUpdatesEnabled(True),
            show_error(self, "Load Error", err),
        ))
        self._worker = w
        w.start()

    def _on_all_packages_loaded(self, all_rows):
        self._model.set_data(all_rows)
        self._view.setUpdatesEnabled(True)
        self._apply_filter()
        self._progress.set_progress(100, f"Loaded {len(all_rows):,} files from {len(self._all_groups)} packages")
        if self._pending_scope_request:
            paths, preferred_path, title = self._pending_scope_request
            self._pending_scope_request = None
            self._apply_workbench_scope(paths, preferred_path, title)

    def _apply_filter(self):
        filter_name = self._type_filter.currentText()
        ext_set = FILE_TYPE_FILTERS.get(filter_name, set())
        search = self._search_input.text()
        self._model.set_filter(ext_set, search)
        fc = self._model.filtered_count
        tc = self._model.total_count
        self._archive_count.setText(f"{fc:,} / {tc:,} files")
        self._update_scope_ui()

    def _update_scope_ui(self):
        scoped_paths = self._model._scoped_paths
        if scoped_paths:
            title = self._active_scope_title or "Workbench Scope"
            self._scope_label.setText(f"Scope: {title} ({len(scoped_paths):,})")
            self._scope_label.setVisible(True)
            self._clear_scope_btn.setVisible(True)
        else:
            self._scope_label.clear()
            self._scope_label.setVisible(False)
            self._clear_scope_btn.setVisible(False)

    def _ensure_workbench_dialog(self) -> ExplorerWorkbenchDialog:
        if self._workbench_dialog is None:
            self._workbench_dialog = ExplorerWorkbenchDialog(self._config, self)
            self._workbench_dialog.workbench.scope_requested.connect(self._apply_workbench_scope)
            self._workbench_dialog.workbench.clear_scope_requested.connect(self._clear_workbench_scope)
            if self._vfs is not None:
                self._workbench_dialog.workbench.set_vfs(self._vfs)
        return self._workbench_dialog

    def _open_workbench_dialog(self):
        if not self._vfs:
            show_error(self, "Explorer Navigator", "Load the game data first.")
            return
        dialog = self._ensure_workbench_dialog()
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _build_search_tooltip(self) -> str:
        """Compact tooltip for the search bar — full help is in the dialog."""
        return (
            "Type to search file names + item aliases.\n"
            "  canta plate armor       all tokens (AND)\n"
            "  \"exact phrase\"          quoted = exact substring\n"
            "  canta OR mace           either matches\n"
            "  -eccanta / NOT eccanta  exclude\n"
            "  *.dds, cd_phm_*         wildcards\n"
            "  ext:.dds                filter by extension\n"
            "  name:hel_0363           filename only\n"
            "  path:character          path only\n"
            "  size:>1mb / size:<500kb size filter\n"
            "  content:CD_PHM_00       search inside file bytes (slow)\n"
            "Click the syntax-help button (next to Catalog) for examples."
        )

    def _show_search_syntax_help(self) -> None:
        """Open a popup with the full enterprise search syntax cheatsheet."""
        from PySide6.QtWidgets import QDialog, QVBoxLayout, QTextBrowser, QPushButton, QHBoxLayout
        dialog = QDialog(self)
        dialog.setWindowTitle("Search syntax — Explorer")
        dialog.resize(720, 560)
        layout = QVBoxLayout(dialog)
        browser = QTextBrowser()
        browser.setOpenExternalLinks(False)
        browser.setHtml("""
        <h2>Explorer search syntax</h2>
        <p>The search bar accepts plain text plus a small enterprise query language.
        Everything is case-insensitive. Multiple terms separated by spaces are
        AND-ed together by default.</p>

        <h3>Plain tokens</h3>
        <table cellpadding="4">
          <tr><td><code>canta</code></td><td>Files whose path or item alias contains a token starting with <code>canta</code>.</td></tr>
          <tr><td><code>canta plate armor</code></td><td>All three tokens must match (default AND).</td></tr>
          <tr><td><code>canta OR mace</code></td><td>Either matches.</td></tr>
          <tr><td><code>-eccanta</code> &nbsp; or &nbsp; <code>NOT eccanta</code></td><td>Exclude rows containing <code>eccanta</code>.</td></tr>
          <tr><td><code>canta NOT eccanta</code></td><td>Combine: must contain canta AND must not contain eccanta.</td></tr>
        </table>

        <h3>Phrases &amp; wildcards</h3>
        <table cellpadding="4">
          <tr><td><code>"canta plate armor"</code></td><td>Exact substring match (whitespace preserved).</td></tr>
          <tr><td><code>*.dds</code></td><td>Glob: ends with <code>.dds</code>.</td></tr>
          <tr><td><code>cd_phm_*</code></td><td>Glob: starts with <code>cd_phm_</code>.</td></tr>
          <tr><td><code>*hel_0363*</code></td><td>Glob: contains <code>hel_0363</code> anywhere.</td></tr>
          <tr><td><code>*_mg.dds</code></td><td>Glob: ends with <code>_mg.dds</code>.</td></tr>
          <tr><td><code>?</code></td><td>Wildcard for a single char (fnmatch).</td></tr>
        </table>

        <h3>Field qualifiers</h3>
        <table cellpadding="4">
          <tr><td><code>ext:.dds</code></td><td>Files with the given extension. <code>.</code> optional.</td></tr>
          <tr><td><code>name:hel_0363</code></td><td>Substring match in the file's basename only (not the path).</td></tr>
          <tr><td><code>path:character/texture</code></td><td>Substring match in the directory path only.</td></tr>
          <tr><td><code>type:image</code></td><td>Substring match in the file-type description (DDS Texture, PAC Mesh…).</td></tr>
          <tr><td><code>size:&gt;1mb</code> &nbsp; or &nbsp; <code>size:&lt;500kb</code></td><td>Size filter. Units: <code>b</code>, <code>kb</code>, <code>mb</code>, <code>gb</code>. <code>&gt;= &lt;=</code> also accepted.</td></tr>
          <tr><td><code>content:CD_PHM_00_Hel_0363</code></td><td>Search inside the file's <i>raw bytes</i>. Slow — only runs after the other filters narrow the corpus.</td></tr>
        </table>

        <h3>Combinations</h3>
        <table cellpadding="4">
          <tr><td><code>ext:.dds canta</code></td><td>All DDS files whose path/alias matches <code>canta</code>.</td></tr>
          <tr><td><code>*.pac AND content:0x47</code></td><td>PAC files whose bytes contain <code>0x47</code>.</td></tr>
          <tr><td><code>name:hel_0363 -inside</code></td><td>Files named <code>hel_0363*</code> excluding any with <code>inside</code> anywhere.</td></tr>
          <tr><td><code>(canta OR mace) ambition</code></td><td>Parentheses are <i>not</i> grouped yet — but <code>canta ambition OR mace ambition</code> works.</td></tr>
        </table>

        <h3>Tips</h3>
        <ul>
          <li>Empty search shows everything (subject to other filters).</li>
          <li>Tokens use prefix match: <code>canta</code> matches <code>cantarts</code> but not <code>eccanta</code> (token boundary required).</li>
          <li>Item aliases (display names) are searched by token; raw paths are searched by substring — both happen automatically.</li>
          <li>The <b>Catalog</b> button is for browsing items by category — same end result as typing the item's stem.</li>
          <li><b>Content search</b> is on a per-file basis: each matching file is read, decompressed, and grep'd. Use it sparingly.</li>
        </ul>
        """)
        layout.addWidget(browser)
        button_row = QHBoxLayout()
        button_row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(dialog.accept)
        button_row.addWidget(close_btn)
        layout.addLayout(button_row)
        dialog.exec()

    def _open_catalog_browser(self):
        """Open the categorised image-grid item browser dialog.

        Uses the catalog that was pre-built when the game data
        loaded — no I/O on the GUI thread, no blank-dialog wait.
        Lazy-imported so launching the Explorer tab itself never pays
        the QListView / QPixmapCache / threadpool startup cost.
        """
        if not self._vfs:
            show_error(self, "Catalog Browser", "Load the game data first.")
            return
        if self._catalog_loading or self._item_catalog is None:
            show_error(self, "Catalog Browser",
                        "Catalog is still loading — try again in a moment.")
            return
        from ui.dialogs.catalog_browser_dialog import CatalogBrowserDialog
        dialog = CatalogBrowserDialog(
            self._vfs, catalog=self._item_catalog, parent=self,
        )
        # Single click in the catalog scopes the main file list live —
        # same behavior the user expects from "search like main search".
        # Double click does the same thing AND closes the dialog (a
        # commit-and-leave shortcut).
        dialog.item_picked.connect(self._scope_to_catalog_selection)
        dialog.item_activated.connect(self._scope_to_catalog_selection)
        dialog.exec()

    def _scope_to_catalog_selection(self, selection):
        """Filter Explorer to every file connected to the picked item.

        Builds the scope from the record's PAC files plus its icon
        paths plus any sister `.pam` / `.pamlod` / `.pac_xml` /
        `.app_xml` / `.prefabdata_xml` / DDS texture file that lives
        under the same stem in the loaded VFS. The Explorer's existing
        ``_apply_workbench_scope`` then takes care of switching to All
        Packages and surfacing only those rows.

        Any leftover text in the main search bar is cleared first —
        otherwise the scope's path list is intersected with the search
        substring and the user sees an empty file list whenever their
        previous search query doesn't happen to match any of the
        scoped files.
        """
        if not selection or not self._vfs:
            return
        # Clear the active search so the scope is the sole filter.
        # ``blockSignals`` keeps the text-change handler from firing an
        # extra refilter — ``_apply_workbench_scope`` triggers one
        # explicitly below.
        if self._search_input.text():
            self._search_input.blockSignals(True)
            try:
                self._search_input.setText("")
            finally:
                self._search_input.blockSignals(False)
            self._search_timer.stop()
        record = selection.record
        scope_paths: list[str] = []
        # PAC files first — exact paths from the catalog.
        scope_paths.extend(record.pac_files)
        # Icon DDS paths next — exact PAMT paths populated by the catalog
        # builder.
        scope_paths.extend(record.icon_paths)
        # Sister files — every entry in any loaded PAMT whose path
        # contains the bare stem of any of the record's pac files.
        # Conservative: only entries that share the full pre-extension
        # stem so we don't pull in 100 unrelated regional variants.
        stems = []
        for pac in record.pac_files:
            stem = pac.replace("\\", "/").rsplit("/", 1)[-1]
            if stem.lower().endswith(".pac"):
                stem = stem[:-4]
            if stem and stem not in stems:
                stems.append(stem.lower())
        # Walk the model's prebuilt rows (one per archive entry) for sister
        # files whose path contains any of the record's pac stems. The model
        # owns the row list — accessing it directly is the same pattern the
        # tab uses elsewhere for scope operations.
        if self._model is not None and stems:
            for row in self._model._all_rows:
                for stem in stems:
                    if stem in row.path_lower:
                        if row.entry.path not in scope_paths:
                            scope_paths.append(row.entry.path)
                        break

        # ── Strict fallback: item has no PAC and no icon ─────────────
        # The catalog carries items that ship without a 3D model OR an
        # inventory icon (lore items, currency, abstract entries from
        # gamedata tables). For those records both ``pac_files`` AND
        # ``icon_paths`` are empty — the previous code silently
        # returned, leaving the Explorer unchanged and giving the user
        # no feedback (just4u's "click any item no image, doesn't
        # return it in explorer" report).
        #
        # Strict 1+1: when the canonical paths are empty, scope by
        # the item's internal name as a substring across every loaded
        # PAMT. That surfaces gamedata-table entries / loc-string
        # files / xml records that reference the item by name — at
        # minimum the user sees SOMETHING (the .gd file the item
        # came from) instead of an unchanged Explorer.
        if not scope_paths and self._model is not None:
            iname = (record.internal_name or "").lower().strip()
            if iname and len(iname) >= 3:
                # Match any row whose path contains the internal name
                # as a substring. Capped at a generous limit so a very
                # generic name (e.g. ``armor``) doesn't produce a 9 k
                # scope that's useless to the user; we surface the
                # top hits and the search bar can refine further.
                MAX_FALLBACK_HITS = 500
                for row in self._model._all_rows:
                    if iname in row.path_lower:
                        scope_paths.append(row.entry.path)
                        if len(scope_paths) >= MAX_FALLBACK_HITS:
                            break

        if not scope_paths:
            # Genuinely no associated files anywhere in the VFS for
            # this item. Don't fail silently — tell the user via the
            # status bar so they don't think the click was ignored.
            display = (record.display_name or record.internal_name
                       or "this item")
            self._progress.set_status(
                f"No PAC / icon / gamedata files reference "
                f"{display!r} in the loaded VFS."
            )
            return
        title = record.display_name or record.internal_name or "Catalog selection"
        self._apply_workbench_scope(scope_paths, preferred_path="", title=title)

    def _apply_workbench_scope(self, paths: list[str], preferred_path: str = "", title: str = ""):
        normalized = []
        seen = set()
        for path in paths:
            norm = path.replace("\\", "/")
            lower = norm.lower()
            if lower in seen:
                continue
            seen.add(lower)
            normalized.append(norm)
        if not normalized:
            self._clear_workbench_scope()
            return

        self._active_scope_title = title or "Workbench Scope"
        if self._group_combo.currentText() != ALL_PACKAGES:
            self._pending_scope_request = (normalized, preferred_path, self._active_scope_title)
            self._group_combo.setCurrentText(ALL_PACKAGES)
            return

        self._pending_scope_request = None
        self._model.set_scope_paths(normalized)
        self._apply_filter()
        self._select_entry_path(preferred_path)

    def _clear_workbench_scope(self):
        self._active_scope_title = ""
        self._pending_scope_request = None
        self._model.set_scope_paths(None)
        self._apply_filter()

    def _select_entry_path(self, preferred_path: str):
        if not preferred_path:
            return
        preferred_lower = preferred_path.replace("\\", "/").lower()
        for row in range(self._model.rowCount()):
            row_data = self._model.row_at(row)
            if row_data and row_data.entry.path.lower() == preferred_lower:
                index = self._model.index(row, _COL_FILE)
                self._view.setCurrentIndex(index)
                self._view.selectRow(row)
                self._view.scrollTo(index, QTableView.PositionAtCenter)
                return

    def _get_selected_rows(self) -> list[int]:
        """Return sorted list of unique selected view row indices."""
        return sorted({idx.row() for idx in self._view.selectedIndexes()})

    def _toggle_selected_checks(self):
        """Space: toggle check state of all currently selected rows."""
        rows = self._get_selected_rows()
        if not rows:
            return
        # Determine new state: if any selected row is unchecked, check all; else uncheck all
        any_unchecked = any(
            not (self._model.row_at(r).checked) for r in rows if self._model.row_at(r)
        )
        new_state = Qt.Checked if any_unchecked else Qt.Unchecked
        for r in rows:
            self._model.setData(self._model.index(r, _COL_FILE), new_state, Qt.CheckStateRole)

    def _copy_selected_filenames(self):
        """Ctrl+C: copy filename (basename + extension) of every
        selected row to the system clipboard.

        Multiple selections are joined with newlines so the clipboard
        round-trips into anywhere that accepts a one-per-line list
        (text editors, terminals, the search bars in other tabs).
        Falls back to the current row when nothing is explicitly
        selected — Qt's default selection model treats the focused
        cell as "current" but not "selected", and users expect Ctrl+C
        to work either way.
        """
        rows = self._get_selected_rows()
        if not rows:
            current = self._view.currentIndex()
            if current.isValid():
                rows = [current.row()]
        if not rows:
            return
        names: list[str] = []
        for r in rows:
            row_data = self._model.row_at(r)
            if not row_data:
                continue
            names.append(os.path.basename(row_data.entry.path))
        if not names:
            return
        QApplication.clipboard().setText("\n".join(names))

    def _show_context_menu(self, pos):
        rows = self._get_selected_rows()
        menu = QMenu(self)
        if rows:
            check_act = menu.addAction(f"Check {len(rows)} selected")
            check_act.triggered.connect(lambda: [
                self._model.setData(self._model.index(r, _COL_FILE), Qt.Checked, Qt.CheckStateRole)
                for r in rows
            ])
            uncheck_act = menu.addAction(f"Uncheck {len(rows)} selected")
            uncheck_act.triggered.connect(lambda: [
                self._model.setData(self._model.index(r, _COL_FILE), Qt.Unchecked, Qt.CheckStateRole)
                for r in rows
            ])
            menu.addSeparator()
        menu.addAction("Check All").triggered.connect(lambda: self._model.check_all(True))
        menu.addAction("Uncheck All").triggered.connect(lambda: self._model.check_all(False))

        # Mesh export options for .pam/.pamlod/.pac files
        # Use the row at the right-click position, not the selection
        click_index = self._view.indexAt(pos)
        if click_index.isValid():
            click_row_data = self._model.row_at(click_index.row())
            if click_row_data:
                from core.mesh_parser import is_mesh_file
                if is_mesh_file(click_row_data.entry.path):
                    menu.addSeparator()
                    entry = click_row_data.entry
                    export_obj_act = menu.addAction("Export as OBJ")
                    export_obj_act.triggered.connect(lambda _=False, e=entry: self._export_mesh(e, "obj"))
                    export_fbx_act = menu.addAction("Export as FBX")
                    export_fbx_act.triggered.connect(lambda _=False, e=entry: self._export_mesh(e, "fbx"))
                    # Combined "Export Full Character FBX" — mesh + skeleton +
                    # optional animation in a single FBX. The per-submesh
                    # slot-to-bone palette resolution that previously kept
                    # this hidden is now resolved geometrically in
                    # ``core.mesh_parser.derive_skin_slot_to_pab_geometric``
                    # (v1.25.2: 158/158 clusters anatomically correct on
                    # Damian's full character).
                    export_full_act = menu.addAction(
                        "Export Full Character FBX (Mesh + Bones + Animation)"
                    )
                    export_full_act.triggered.connect(
                        lambda _=False, e=entry: self._export_full_character(e)
                    )
                    # NEW: Export Complete Character resolves the
                    # appearance manifest from the clicked body PAC
                    # (via the cached <Nude> Prefab → .app_xml index)
                    # and merges every PAC the manifest names — body,
                    # face/eyes/teeth/brows, hair, armor — into ONE
                    # FBX. Always shown for .pac entries; the
                    # handler shows a clear error when the clicked
                    # PAC isn't a body PAC referenced by any .app_xml.
                    export_complete_act = menu.addAction(
                        "Export Complete Character (FBX)"
                    )
                    export_complete_act.setToolTip(
                        "Resolve this PAC's matching .app_xml and "
                        "export every part it lists (body + face + "
                        "hair + armor) as a single skinned + textured "
                        "FBX. Strict 1+1: refuses if the PAC isn't "
                        "referenced as <Nude> in any appearance "
                        "manifest."
                    )
                    export_complete_act.triggered.connect(
                        lambda _=False, e=entry:
                            self._export_complete_character_from_pac(e)
                    )
                    menu.addAction("Diagnose dye / tint system").triggered.connect(
                        lambda _=False, e=entry: self._diagnose_dye(e))
                    menu.addSeparator()
                    import_act = menu.addAction("Import OBJ (preview rebuilt mesh)")
                    import_act.triggered.connect(lambda _=False, e=entry: self._import_mesh(e))
                    import_fbx_act = menu.addAction("Import FBX (preview rebuilt mesh)")
                    import_fbx_act.triggered.connect(lambda _=False, e=entry: self._import_mesh(e, fmt="fbx"))
                    # NEW in v1.22.9 — build the rebuilt PAC to a
                    # user folder without touching the live game
                    # archives. Fast iteration loop for mesh work.
                    build_act = menu.addAction("Build PAC to Folder... (no patch)")
                    build_act.setToolTip(
                        "Convert an OBJ to a .pac / .pam / .pamlod file on "
                        "disk without modifying game archives. Ideal for "
                        "iterating on mesh edits before committing."
                    )
                    build_act.triggered.connect(
                        lambda _=False, e=entry: self._build_pac_to_folder(e)
                    )
                    build_fbx_act = menu.addAction("Build PAC from FBX to Folder... (no patch)")
                    build_fbx_act.triggered.connect(
                        lambda _=False, e=entry: self._build_pac_to_folder(e, fmt="fbx")
                    )
                    patch_act = menu.addAction("Import OBJ + Patch to Game")
                    patch_act.triggered.connect(lambda _=False, e=entry: self._import_and_patch_mesh(e))
                    patch_fbx_act = menu.addAction("Import FBX + Patch to Game")
                    patch_fbx_act.triggered.connect(lambda _=False, e=entry: self._import_and_patch_mesh(e, fmt="fbx"))
                    ship_act = menu.addAction("Import OBJ + Ship to App")
                    ship_act.triggered.connect(lambda _=False, e=entry: self._ship_single_mesh(e))
                    ship_fbx_act = menu.addAction("Import FBX + Ship to App")
                    ship_fbx_act.triggered.connect(lambda _=False, e=entry: self._ship_single_mesh(e, fmt="fbx"))
                    # NEW in v1.22.9 — one-click undo, no Steam
                    # Verify needed. Only enabled when a baseline
                    # snapshot exists for this PAC.
                    if self._mesh_baseline.has_baseline(entry.path):
                        menu.addSeparator()
                        restore_act = menu.addAction(
                            "Restore from Baseline (undo all edits)"
                        )
                        restore_act.setToolTip(
                            "Patch the pristine, pre-edit bytes back into "
                            "the game. No Steam Verify required."
                        )
                        restore_act.triggered.connect(
                            lambda _=False, e=entry: self._restore_from_baseline(e)
                        )

        # Animation (PAA) exports — FBX keyframe export for modders.
        if click_index.isValid():
            click_row_data = self._model.row_at(click_index.row())
            if click_row_data and click_row_data.entry.path.lower().endswith(".paa"):
                menu.addSeparator()
                entry = click_row_data.entry
                menu.addAction("Export animation as FBX").triggered.connect(
                    lambda _=False, e=entry: self._export_paa_fbx(e))

        # Havok (HKX) — JSON dump + risk diagnostic from the Layer 1-5 stack.
        if click_index.isValid():
            click_row_data = self._model.row_at(click_index.row())
            if click_row_data and click_row_data.entry.path.lower().endswith(".hkx"):
                menu.addSeparator()
                entry = click_row_data.entry
                menu.addAction("Dump HKX as JSON").triggered.connect(
                    lambda _=False, e=entry: self._dump_hkx_json(e))
                menu.addAction("Physics edit-risk report").triggered.connect(
                    lambda _=False, e=entry: self._hkx_risk_report(e))

        # Audio export/import for audio files
        if click_index.isValid():
            click_row_data = self._model.row_at(click_index.row())
            if click_row_data:
                audio_exts = {".wem", ".bnk", ".wav", ".ogg", ".mp3", ".pasound"}
                file_ext = os.path.splitext(click_row_data.entry.path.lower())[1]
                if file_ext in audio_exts:
                    menu.addSeparator()
                    entry = click_row_data.entry
                    exp_wav = menu.addAction("Export as WAV")
                    exp_wav.triggered.connect(lambda _=False, e=entry: self._export_audio_wav(e))
                    imp_wav = menu.addAction("Import WAV + Patch to Game")
                    imp_wav.triggered.connect(lambda _=False, e=entry: self._import_audio_patch(e))

        # Quick Mods submenu — always available when game is loaded
        if self._vfs:
            menu.addSeparator()
            quick_menu = menu.addMenu("Quick Mods")
            quick_menu.addAction("Mercenary Info (mercenaryinfo)").triggered.connect(
                lambda: self._open_quick_mod("mercenaryinfo.pabgb"))
            quick_menu.addAction("Ally Groups (allygroupinfo)").triggered.connect(
                lambda: self._open_quick_mod("allygroupinfo.pabgb"))
            quick_menu.addAction("Formations (formationinfo)").triggered.connect(
                lambda: self._open_quick_mod("formationinfo.pabgb"))
            quick_menu.addAction("Dye Colors (dyecolorgroupinfo)").triggered.connect(
                lambda: self._open_quick_mod("dyecolorgroupinfo.pabgb"))
            quick_menu.addAction("Crafting Tools (crafttoolinfo)").triggered.connect(
                lambda: self._open_quick_mod("crafttoolinfo.pabgb"))
            quick_menu.addAction("Characters (characterinfo)").triggered.connect(
                lambda: self._open_quick_mod("characterinfo.pabgb"))
            quick_menu.addAction("Items (iteminfo)").triggered.connect(
                lambda: self._open_quick_mod("iteminfo.pabgb"))
            quick_menu.addAction("NPCs (npcinfo)").triggered.connect(
                lambda: self._open_quick_mod("npcinfo.pabgb"))
            quick_menu.addAction("Skills (skill)").triggered.connect(
                lambda: self._open_quick_mod("skill.pabgb"))
            quick_menu.addAction("Status Effects (statusinfo)").triggered.connect(
                lambda: self._open_quick_mod("statusinfo.pabgb"))
            quick_menu.addSeparator()
            quick_menu.addAction("State-Machine Browser…").triggered.connect(
                self._open_state_machine_browser)
            quick_menu.addAction("Face-Part Browser…").triggered.connect(
                self._open_face_parts_browser)

        # Game data table editor for .pabgb files
        if click_index.isValid():
            click_row_data = self._model.row_at(click_index.row())
            if click_row_data:
                file_ext = os.path.splitext(click_row_data.entry.path.lower())[1]
                if file_ext == ".pabgb":
                    menu.addSeparator()
                    entry = click_row_data.entry
                    edit_act = menu.addAction("Edit Game Data Table")
                    edit_act.triggered.connect(lambda _=False, e=entry: self._edit_pabgb(e, patch=False))
                    patch_act = menu.addAction("Edit Table + Patch to Game")
                    patch_act.triggered.connect(lambda _=False, e=entry: self._edit_pabgb(e, patch=True))
                elif file_ext == ".pabgh":
                    # Header-only editor — pairs with .pabgb body editor
                    # but lets you edit row hashes / offsets / count
                    # directly (useful for swapping which body row a
                    # given index points to).
                    menu.addSeparator()
                    entry = click_row_data.entry
                    edit_act = menu.addAction("Edit Game Data Header")
                    edit_act.triggered.connect(
                        lambda _=False, e=entry: self._edit_pabgh(e, patch=False)
                    )
                    patch_act = menu.addAction("Edit Header + Patch to Game")
                    patch_act.triggered.connect(
                        lambda _=False, e=entry: self._edit_pabgh(e, patch=True)
                    )
                elif file_ext in (".paseq", ".paseqc", ".pastage"):
                    # Sequencer / timeline scripts — drives boss intros,
                    # cutscenes, BGM swaps, etc. The editor surfaces every
                    # length-prefixed string (audio events, animation
                    # paths, Timeline.* commands) for editing.
                    menu.addSeparator()
                    entry = click_row_data.entry
                    edit_act = menu.addAction("Edit Sequencer")
                    edit_act.triggered.connect(
                        lambda _=False, e=entry: self._edit_paseq(e, patch=False)
                    )
                    patch_act = menu.addAction("Edit Sequencer + Patch to Game")
                    patch_act.triggered.connect(
                        lambda _=False, e=entry: self._edit_paseq(e, patch=True)
                    )
                elif file_ext == ".prefab":
                    menu.addSeparator()
                    entry = click_row_data.entry
                    edit_act = menu.addAction("Edit Prefab")
                    edit_act.triggered.connect(lambda _=False, e=entry: self._edit_prefab(e))
                elif file_ext in (".pac_xml", ".app_xml", ".prefabdata_xml"):
                    # Post-April-2026 renamed encrypted XML sidecars.
                    # All three share a common structure (multi-root
                    # XML with BOM + CRLF + tab indent) so they route
                    # through the same editor dialog.
                    menu.addSeparator()
                    entry = click_row_data.entry
                    kind_label = {
                        ".pac_xml":        "Edit PAC XML (mesh properties)",
                        ".app_xml":        "Edit App XML (appearance)",
                        ".prefabdata_xml": "Edit Prefab Data XML",
                    }[file_ext]
                    edit_act = menu.addAction(kind_label)
                    edit_act.triggered.connect(
                        lambda _=False, e=entry: self._edit_pac_xml(e)
                    )
                    # ── Export Complete Character (only for .app_xml) ──
                    # The .app_xml is the per-character appearance
                    # manifest — it lists every prefab (and through
                    # them, every PAC) needed to render a complete
                    # character. The Export Complete Character action
                    # follows that chain end-to-end and writes ONE
                    # merged FBX with body + face + hair + armor +
                    # textures, all skinned to the shared rig.
                    if file_ext == ".app_xml":
                        export_act = menu.addAction(
                            "Export Complete Character (FBX)"
                        )
                        export_act.triggered.connect(
                            lambda _=False, e=entry:
                                self._export_complete_character_from_app_xml(e)
                        )

        menu.exec(self._view.viewport().mapToGlobal(pos))

    def _edit_pabgb(self, entry: PamtFileEntry, patch: bool = False, search: str = ""):
        """Open the game-data table editor dialog."""
        try:
            from ui.dialogs.pabgb_editor_dialog import PabgbEditorDialog
            from core.pabgb_parser import parse_pabgb

            self._progress.set_status(f"Parsing {os.path.basename(entry.path)}...")
            QApplication.processEvents()

            data = self._vfs.read_entry_data(entry)

            # Try to find the matching .pabgh header
            header_data = None
            header_path = entry.path[:-1] + "h"
            pamt = self._vfs.load_pamt(
                os.path.basename(os.path.dirname(entry.paz_file))
            )
            for he in pamt.file_entries:
                if he.path.lower() == header_path.lower():
                    header_data = self._vfs.read_entry_data(he)
                    break

            table = parse_pabgb(data, header_data, os.path.basename(entry.path))

            dlg = PabgbEditorDialog(
                table, entry, self._vfs,
                patch_mode=patch, initial_search=search, parent=self,
            )
            self._progress.set_status(f"Opened editor: {os.path.basename(entry.path)}")
            dlg.exec()
        except Exception as e:
            show_error(self, "Table Editor Error", str(e))

    # ------------------------------------------------------------------
    # .pabgh — header editor. Pairs with the .pabgb body editor above
    # but lets the user edit the row-hash → byte-offset table directly
    # (useful for swapping which body row a given character/quest ID
    # resolves to).
    # ------------------------------------------------------------------
    def _edit_pabgh(self, entry: PamtFileEntry, patch: bool = False):
        """Open the game-data header editor dialog."""
        try:
            from ui.dialogs.pabgh_editor_dialog import PabghEditorDialog

            self._progress.set_status(f"Reading {os.path.basename(entry.path)}...")
            QApplication.processEvents()
            data = self._vfs.read_entry_data(entry)
            dlg = PabghEditorDialog(
                data, entry, self._vfs,
                patch_mode=patch, parent=self,
            )
            self._progress.set_status(
                f"Opened header editor: {os.path.basename(entry.path)}"
            )
            dlg.exec()
        except Exception as e:
            show_error(self, "Header Editor Error", str(e))

    # ------------------------------------------------------------------
    # .paseq / .paseqc / .pastage — sequencer / timeline editor.
    # Surfaces every length-prefixed string (Wwise event names,
    # animation paths, Timeline.* commands, type identifiers) for
    # safe in-place editing via core.paseq_parser.
    # ------------------------------------------------------------------
    def _edit_paseq(self, entry: PamtFileEntry, patch: bool = False):
        """Open the sequencer / timeline editor dialog."""
        try:
            from ui.dialogs.paseq_editor_dialog import PaseqEditorDialog
            from core.paseq_parser import parse_paseq

            self._progress.set_status(f"Parsing {os.path.basename(entry.path)}...")
            QApplication.processEvents()
            data = self._vfs.read_entry_data(entry)
            parsed = parse_paseq(data, file_name=os.path.basename(entry.path))
            dlg = PaseqEditorDialog(
                parsed, entry, self._vfs,
                patch_mode=patch, parent=self,
            )
            self._progress.set_status(
                f"Opened sequencer editor: {os.path.basename(entry.path)} "
                f"({len(parsed.strings)} strings)"
            )
            dlg.exec()
        except Exception as e:
            show_error(self, "Sequencer Editor Error", str(e))

    def _open_face_parts_browser(self):
        """Build the face-part catalog from the VFS and open the
        Face-Part Browser dialog.

        Face customisation in Crimson Desert is submesh-swapping: each
        facial region is a discrete PAC variant (cd_ptm_00_head_0001,
        cd_ppdm_00_eyeleft_00_0001, ...). Rather than trying to sculpt
        non-existent blendshapes, we surface the catalog of available
        variants so modders can pick which one to load.
        """
        try:
            from core.face_parts import build_catalog, classify_face_part
            from ui.dialogs.face_parts_dialog import FacePartsDialog

            self._progress.set_status("Scanning archives for face parts…")
            QApplication.processEvents()

            # Walk EVERY available package group via the public
            # list_package_groups() + load_pamt() API. Character
            # appearance can live in any group (Kliff + NPCs are in
            # 0000 / 0009 typically, but accessories scatter).
            archive_paths: dict[str, str] = {}
            filenames: list[str] = []

            try:
                groups = self._vfs.list_package_groups()
            except Exception as ex:
                show_error(self, "Face-Part Browser",
                           f"Game not loaded — cannot scan archives.\n\n{ex}")
                return

            for grp in groups:
                try:
                    pamt = self._vfs.load_pamt(grp)
                except Exception:
                    continue
                for entry in pamt.file_entries:
                    path = entry.path.lower()
                    if not path.endswith(".pac"):
                        continue
                    base = os.path.basename(path)
                    if classify_face_part(base) is None:
                        continue
                    # First occurrence wins for duplicate basenames
                    if base not in archive_paths:
                        archive_paths[base] = entry.path
                        filenames.append(base)

            catalog = build_catalog(filenames, archive_paths=archive_paths)
            if catalog.count() == 0:
                show_error(
                    self, "Face-Part Browser",
                    "No face-part PACs found in any loaded archive.\n\n"
                    "Make sure the game is loaded and the character "
                    "package groups have been scanned.",
                )
                return
            self._progress.set_status(
                f"Face parts: {catalog.count():,} across "
                f"{len(catalog.categories())} categories"
            )
            dlg = FacePartsDialog(catalog, vfs=self._vfs, parent=self)
            # If the user clicks 'Open Matching Prefab', resolve via
            # the reverse-reference index rather than basename guess.
            dlg.prefab_edit_requested.connect(
                self._open_prefab_via_pac
            )
            dlg.exec()
        except Exception as e:
            logger.exception("face-part browser failed")
            show_error(self, "Face-Part Browser Error", str(e))

    def _open_prefab_via_pac(self, pac_archive_path: str):
        """Open the prefab(s) that REFERENCE ``pac_archive_path``.

        Prefabs carry internal .pac path references that don't
        necessarily match their own basename — e.g.
        ``cd_phm_00_cloak_00_0208_t.prefab`` references
        ``cd_phm_00_cloak_00_0054_01.pac``. A reverse index over
        every prefab in the VFS gives the authoritative mapping.

        Index is built lazily on first call and cached on the tab so
        subsequent 'Open Matching Prefab' clicks are instant.
        """
        try:
            from core.prefab_reference_index import (
                build_reference_index_from_vfs,
            )
            if self._prefab_ref_index is None:
                self._progress.set_status(
                    "Building prefab-reference index (one-time scan)…"
                )
                QApplication.processEvents()
                self._prefab_ref_index = build_reference_index_from_vfs(self._vfs)
            idx = self._prefab_ref_index

            hits = idx.prefabs_referencing(pac_archive_path)
            if not hits:
                # Fallback: try basename-only
                hits = idx.prefabs_referencing(
                    os.path.basename(pac_archive_path)
                )
            if not hits:
                show_error(
                    self, "No prefab found",
                    f"No prefab in the loaded archives references "
                    f"{pac_archive_path}.\n\n"
                    f"(The reverse-reference index covers "
                    f"{idx.prefab_count():,} prefabs and "
                    f"{idx.pac_count():,} unique PAC paths.)",
                )
                return

            # If multiple prefabs reference the same PAC, let the
            # user pick. Usually there's only one.
            if len(hits) > 1:
                from PySide6.QtWidgets import QInputDialog
                choice, ok = QInputDialog.getItem(
                    self, "Multiple matches",
                    f"{len(hits)} prefabs reference this PAC. Pick one to open:",
                    hits, 0, False,
                )
                if not ok:
                    return
                target = choice
            else:
                target = hits[0]

            # Find the PAMT entry for `target`
            needle = target.lower()
            for grp in self._vfs.list_package_groups():
                try:
                    pamt = self._vfs.load_pamt(grp)
                except Exception:
                    continue
                for entry in pamt.file_entries:
                    if entry.path.lower() == needle:
                        self._edit_prefab(entry)
                        return
            show_error(
                self, "Prefab not found",
                f"Reverse index pointed at {target!r} but no PAMT "
                f"entry was found for it.",
            )
        except Exception as e:
            logger.exception("open prefab by pac ref failed")
            show_error(self, "Prefab Error", str(e))

    def _open_state_machine_browser(self):
        """Build the cross-.pabgb state-machine index and open the browser."""
        try:
            from core.state_machine import build_state_index
            from core.pabgb_parser import parse_pabgb
            from ui.dialogs.state_machine_dialog import StateMachineDialog

            # State machine data lives primarily in package group 0008
            # (game data tables). We parse the tables that carry
            # condition expressions; low-priority tables are skipped
            # so the initial scan stays under a few seconds.
            STATE_TABLES = (
                "gamedata/conditioninfo.pabgb",
                "gamedata/stageinfo.pabgb",
                "gamedata/gimmickinfo.pabgb",
                "gamedata/gimmickgroupinfo.pabgb",
                "gamedata/characterinfo.pabgb",
                "gamedata/actionpointinfo.pabgb",
                "gamedata/actionrestrictionorderinfo.pabgb",
                "gamedata/skillgroupinfo.pabgb",
                "gamedata/aidialogstringinfo.pabgb",
            )
            self._progress.set_status("Loading state-machine tables…")
            QApplication.processEvents()

            try:
                pamt = self._vfs.load_pamt("0008")
            except Exception:
                show_error(self, "State Machine",
                           "Package group 0008 not available — is the "
                           "game loaded?")
                return

            entries_by_path = {e.path.lower(): e for e in pamt.file_entries}
            tables = []
            for target in STATE_TABLES:
                entry = entries_by_path.get(target)
                if entry is None:
                    continue
                data = self._vfs.read_entry_data(entry)
                header_path = target[:-1] + "h"
                header_entry = entries_by_path.get(header_path)
                header_data = (
                    self._vfs.read_entry_data(header_entry)
                    if header_entry else None
                )
                try:
                    tbl = parse_pabgb(data, header_data, os.path.basename(target))
                    tables.append(tbl)
                except Exception as ex:
                    logger.warning("state-machine: skipped %s: %s", target, ex)

            if not tables:
                show_error(self, "State Machine",
                           "No state-machine tables could be loaded. "
                           "Check that package group 0008 has been extracted.")
                return

            self._progress.set_status(
                f"Indexing {sum(len(t.rows) for t in tables):,} rows…"
            )
            QApplication.processEvents()
            index = build_state_index(tables)

            self._progress.set_status(
                f"State machine: {len(index.tokens):,} tokens across "
                f"{len(index.table_rows)} tables"
            )
            dlg = StateMachineDialog(index, vfs=self._vfs, parent=self)
            dlg.exec()
        except Exception as e:
            logger.exception("state-machine browser failed")
            show_error(self, "State Machine Error", str(e))

    def _edit_prefab(self, entry: PamtFileEntry):
        """Open the .prefab editor dialog (strings, file refs, tag values)."""
        try:
            from ui.dialogs.prefab_editor_dialog import PrefabEditorDialog
            from core.prefab_parser import parse_prefab

            self._progress.set_status(f"Parsing {os.path.basename(entry.path)}...")
            QApplication.processEvents()

            data = self._vfs.read_entry_data(entry)
            prefab = parse_prefab(data, entry.path)

            dlg = PrefabEditorDialog(prefab, entry, self._vfs, parent=self)
            self._progress.set_status(f"Opened prefab editor: {os.path.basename(entry.path)}")
            dlg.exec()
        except Exception as e:
            show_error(self, "Prefab Editor Error", str(e))

    def _edit_pac_xml(self, entry: PamtFileEntry):
        """Open the PAC XML editor dialog for one of the three
        post-April-2026 renamed encrypted XML sidecars:

          * .pac_xml         — per-mesh material + submesh data
          * .app_xml         — character appearance metadata
          * .prefabdata_xml  — supplementary prefab data

        They share the same file format (UTF-8 BOM, multi-root XML,
        CRLF line endings, tab indentation) so a single editor
        handles all three. The VFS decrypts + decompresses on read
        and the RepackEngine re-applies both on Patch-to-Game.
        """
        try:
            from ui.dialogs.pac_xml_editor_dialog import PacXmlEditorDialog
            from core.pac_xml_parser import parse_pac_xml

            self._progress.set_status(
                f"Parsing {os.path.basename(entry.path)}..."
            )
            QApplication.processEvents()

            data = self._vfs.read_entry_data(entry)
            parsed = parse_pac_xml(data, entry.path)

            dlg = PacXmlEditorDialog(parsed, entry, self._vfs, parent=self)
            self._progress.set_status(
                f"Opened PAC XML editor: {os.path.basename(entry.path)}"
            )
            dlg.exec()
        except Exception as e:
            show_error(self, "PAC XML Editor Error", str(e))

    def _open_quick_mod(self, table_name: str, patch: bool = True, search: str = ""):
        """Open a specific game data table by name from package 0008."""
        try:
            pamt = self._vfs.load_pamt("0008")
            target = f"gamedata/{table_name}"
            for entry in pamt.file_entries:
                if entry.path.lower() == target.lower():
                    self._edit_pabgb(entry, patch=patch, search=search)
                    return
            show_error(self, "Not Found", f"Table {table_name} not found in game data.")
        except Exception as e:
            show_error(self, "Quick Mod Error", str(e))

    def _export_audio_wav(self, entry: PamtFileEntry):
        """Export an audio file as WAV."""
        try:
            from core.audio_converter import wem_to_wav
            data = self._vfs.read_entry_data(entry)
            basename = os.path.splitext(os.path.basename(entry.path))[0]
            temp = os.path.join(self._temp_dir, os.path.basename(entry.path))
            with open(temp, "wb") as f:
                f.write(data)

            save_path = pick_save_file(self, "Export as WAV", f"{basename}.wav",
                                       filters="WAV Files (*.wav)")
            if not save_path:
                return

            ext = os.path.splitext(entry.path)[1].lower()
            if ext in (".wem", ".bnk"):
                result = wem_to_wav(temp, save_path)
                if not result:
                    show_error(self, "Export Error", "WEM to WAV conversion failed")
                    return
            else:
                import shutil
                shutil.copy2(temp, save_path)

            self._progress.set_status(f"Exported WAV: {save_path}")
            show_info(self, "Export Complete", f"Exported to:\n{save_path}")
        except Exception as e:
            show_error(self, "Export Error", str(e))

    def _import_audio_patch(self, entry: PamtFileEntry):
        """Import a WAV and patch to game."""
        wav_path = pick_file(self, "Select WAV File",
                             filters="Audio Files (*.wav *.ogg *.mp3);;All Files (*.*)")
        if not wav_path:
            return
        try:
            from core.audio_importer import import_audio
            original_data = self._vfs.read_entry_data(entry)
            new_data = import_audio(wav_path, entry, original_data)

            if not confirm_action(self, "Patch Audio",
                                  f"Replace {entry.path}?\n\n"
                                  f"Original: {format_file_size(len(original_data))}\n"
                                  f"New: {format_file_size(len(new_data))}"):
                return

            from core.repack_engine import RepackEngine, ModifiedFile
            game_path = os.path.dirname(os.path.dirname(entry.paz_file))
            papgt_path = os.path.join(game_path, "meta", "0.papgt")
            paz_dir = os.path.basename(os.path.dirname(entry.paz_file))
            pamt_data = self._vfs.load_pamt(paz_dir)

            mod_file = ModifiedFile(
                data=new_data, entry=entry,
                pamt_data=pamt_data, package_group=paz_dir,
            )
            engine = RepackEngine(game_path)
            result = engine.repack([mod_file], papgt_path=papgt_path)

            if result.success:
                self._progress.set_status(f"Audio patched: {entry.path}")
                show_info(self, "Patch Complete", f"Patched {entry.path}")
            else:
                show_error(self, "Patch Error", f"Failed: {result.error}")
        except Exception as e:
            show_error(self, "Patch Error", str(e))

    def _on_archive_row_changed(self, current: QModelIndex, _previous: QModelIndex):
        """Preview when arrow keys change the current row."""
        if not current.isValid():
            return
        row = self._model.row_at(current.row())
        if row:
            self._preview_from_archive(row.entry)

    def _on_archive_clicked(self, index: QModelIndex):
        row = self._model.row_at(index.row())
        if not row:
            return
        self._preview_from_archive(row.entry)

    def _on_archive_double_clicked(self, index: QModelIndex):
        row = self._model.row_at(index.row())
        if not row:
            return
        if is_text_file(row.entry.path):
            self._open_archive_in_editor(row.entry)
        else:
            self._preview_from_archive(row.entry)

    def _preview_from_archive(self, entry: PamtFileEntry):
        try:
            # Dedup: when the user clicks a row, BOTH ``clicked`` and
            # ``currentRowChanged`` fire and call this method back-to-
            # back. Previously we set ``_last_preview_time`` only at
            # the END of the function, so the second call's
            # ``now - _last_preview_time`` measured against the PREVIOUS
            # file (often well past 0.25 s) and the slow preview ran
            # twice. We now stamp the path + time at the START so the
            # second signal short-circuits even if the first preview
            # is still in flight or crashes mid-way.
            now = time.monotonic()
            if entry.path == self._last_preview_path and (now - self._last_preview_time) < 0.25:
                return
            self._last_preview_path = entry.path
            self._last_preview_time = now

            self._progress.set_status(f"Loading {os.path.basename(entry.path)}...")
            data = self._vfs.read_entry_data(entry)
            basename = os.path.basename(entry.path)
            temp_path = os.path.join(self._temp_dir, basename)
            with open(temp_path, "wb") as f:
                f.write(data)

            # For .pabgb files, also extract the paired .pabgh header if it exists
            if entry.path.lower().endswith(".pabgb") and self._vfs:
                header_path = entry.path[:-1] + "h"
                # Derive group from the entry's PAZ file path, not the combo box
                grp = os.path.basename(os.path.dirname(entry.paz_file))
                if grp:
                    try:
                        pamt = self._vfs.load_pamt(grp)
                        for he in pamt.file_entries:
                            if he.path.lower() == header_path.lower():
                                hdata = self._vfs.read_entry_data(he)
                                h_temp = os.path.join(self._temp_dir, os.path.basename(header_path))
                                with open(h_temp, "wb") as hf:
                                    hf.write(hdata)
                                break
                    except Exception:
                        pass

            # Pass the VFS + original archive path through so the preview can
            # discover the mesh's paired .dds texture (core.mesh_texture_service).
            self._preview.preview_file(
                temp_path,
                vfs=self._vfs,
                vfs_path=entry.path.replace("\\", "/"),
            )
            # Refresh the dedup timestamp on success so the 0.25 s
            # window starts AFTER the preview actually completes.
            # (Path is already set at the top.)
            self._last_preview_time = time.monotonic()
            self._progress.set_status(f"Preview: {basename} ({format_file_size(len(data))})")
        except Exception as e:
            self._progress.set_status(f"Preview error: {e}")
            logger.error("Preview error for %s: %s", entry.path, e)

    def _export_mesh(self, entry: PamtFileEntry, fmt: str):
        """Export a mesh file to OBJ or FBX."""
        from ui.dialogs.file_picker import pick_directory
        output_dir = pick_directory(self, "Select Export Directory")
        if not output_dir:
            return

        try:
            self._progress.set_status(f"Exporting {os.path.basename(entry.path)} as {fmt.upper()}...")
            data = self._vfs.read_entry_data(entry)

            from core.mesh_parser import parse_mesh
            mesh = parse_mesh(data, entry.path)

            if not mesh.submeshes:
                from ui.dialogs.confirmation import show_error
                show_error(self, "Export Error", "No geometry found in this file.")
                return

            # Build unique output name: include parent dirs to avoid collisions
            # e.g. "character/warrior/body.pac" → "character_warrior_body"
            clean_path = entry.path.replace("\\", "/")
            basename = os.path.splitext(clean_path)[0].replace("/", "_")

            # Resolve the rig via the shared skeleton resolver. Pearl
            # Abyss character meshes share a class-level skeleton
            # (cd_phm_* → phm_01.pab, cd_phw_* → phw_01.pab, ...).
            # The resolver handles every prefix family; manual override
            # via config lets the user override the auto-pick.
            skeleton = None
            bone_count = 0
            pab_path_used = ""
            pab_search_attempted = False
            pab_search_reason = ""
            resolution_source = ""
            rig_prefix = None
            if entry.path.lower().endswith(".pac") and fmt == "fbx":
                pab_search_attempted = True
                from core.skeleton_resolver import (
                    VfsManagerAdapter,
                    detect_rig_prefix,
                    resolve_skeleton,
                )
                rig_prefix = detect_rig_prefix(entry.path)
                # Honour any per-rig-class override the user saved from
                # a previous "Browse for .pab..." click.
                manual_override = ""
                if rig_prefix:
                    manual_override = self._config.get(
                        f"explorer.skeleton_override.{rig_prefix}", "",
                    )
                adapter = VfsManagerAdapter(self._vfs)
                # Pass PAC bytes for palette-match validation. This is
                # what fixes the redriverhog/animal case: the prefix
                # detector returns None for cd_m0002_00_* names so the
                # ranker falls back to "shortest basename" and picks
                # phm_01.pab (palette match = 0). Palette-match
                # validation rejects that and picks the actual hog rig.
                resolution = resolve_skeleton(
                    entry.path, adapter,
                    manual_override=manual_override or None,
                    pac_bytes=data,
                )
                if resolution.skeleton is not None:
                    skeleton = resolution.skeleton
                    bone_count = len(skeleton.bones)
                    pab_path_used = resolution.pab_path
                    resolution_source = resolution.source
                else:
                    pab_search_reason = resolution.reason

            if fmt == "obj":
                from core.mesh_exporter import export_obj
                export_obj(mesh, output_dir, basename)
                self._progress.set_status(
                    f"Exported OBJ: {mesh.total_vertices:,} verts, {mesh.total_faces:,} faces"
                )
            elif skeleton and skeleton.bones:
                from core.mesh_exporter import export_fbx_with_skeleton
                export_fbx_with_skeleton(mesh, skeleton, output_dir, basename)
                pab_label = os.path.basename(pab_path_used) if pab_path_used else "?"
                self._progress.set_status(
                    f"Exported FBX: {mesh.total_vertices:,} verts, "
                    f"{mesh.total_faces:,} faces, {bone_count} bones "
                    f"[rig={pab_label}, source={resolution_source}]"
                )
            else:
                # Mesh-only path. For PAC inputs, surface a three-choice
                # dialog: Browse for .pab / Continue mesh-only / Cancel.
                # The browse button lets the user pick any .pab in the
                # VFS and remembers that choice per rig prefix so they
                # only pick once per character class.
                if pab_search_attempted:
                    action = self._prompt_skeleton_missing(
                        entry.path, rig_prefix or "", pab_search_reason,
                    )
                    if action == "cancel":
                        self._progress.set_status(
                            "Export cancelled — skeleton missing."
                        )
                        return
                    if action == "browse":
                        # Re-run resolution with the user's picked path.
                        override_path = self._pick_skeleton_from_vfs(rig_prefix or "")
                        if override_path:
                            from core.skeleton_resolver import (
                                VfsManagerAdapter,
                                resolve_skeleton,
                            )
                            adapter = VfsManagerAdapter(self._vfs)
                            resolution = resolve_skeleton(
                                entry.path, adapter,
                                manual_override=override_path,
                                pac_bytes=data,
                            )
                            if resolution.skeleton is not None:
                                # Remember the choice per rig class.
                                if rig_prefix:
                                    self._config.set(
                                        f"explorer.skeleton_override.{rig_prefix}",
                                        override_path,
                                    )
                                    self._config.save()
                                skeleton = resolution.skeleton
                                bone_count = len(skeleton.bones)
                                from core.mesh_exporter import export_fbx_with_skeleton
                                export_fbx_with_skeleton(
                                    mesh, skeleton, output_dir, basename,
                                )
                                self._progress.set_status(
                                    f"Exported FBX: {mesh.total_vertices:,} verts, "
                                    f"{mesh.total_faces:,} faces, {bone_count} bones "
                                    f"[rig={os.path.basename(override_path)}, source=manual]"
                                )
                                from ui.dialogs.confirmation import show_info
                                show_info(
                                    self, "Export Complete",
                                    f"Exported {basename}.{fmt} to:\n{output_dir}\n\n"
                                    f"Vertices: {mesh.total_vertices:,}\n"
                                    f"Faces: {mesh.total_faces:,}\n"
                                    f"Bones: {bone_count}\n"
                                    f"Rig: {os.path.basename(override_path)} (manual)"
                                )
                                return
                            else:
                                from ui.dialogs.confirmation import show_error
                                show_error(
                                    self, "Skeleton load failed",
                                    f"Could not load {override_path}:\n{resolution.reason}",
                                )
                                return
                        # User cancelled the picker — treat as cancel.
                        self._progress.set_status(
                            "Export cancelled — skeleton missing."
                        )
                        return
                    # action == "continue" — fall through to mesh-only.
                from core.mesh_exporter import export_fbx
                export_fbx(mesh, output_dir, basename)
                self._progress.set_status(
                    f"Exported FBX (mesh-only): {mesh.total_vertices:,} verts, {mesh.total_faces:,} faces"
                )

            from ui.dialogs.confirmation import show_info
            bone_msg = f"\nBones: {bone_count}" if bone_count > 0 else ""
            show_info(self, "Export Complete",
                      f"Exported {basename}.{fmt} to:\n{output_dir}\n\n"
                      f"Vertices: {mesh.total_vertices:,}\n"
                      f"Faces: {mesh.total_faces:,}\n"
                      f"Submeshes: {len(mesh.submeshes)}\n"
                      f"UVs: {'Yes' if mesh.has_uvs else 'No'}{bone_msg}")

        except Exception as e:
            self._progress.set_status(f"Export error: {e}")
            logger.error("Mesh export error for %s: %s", entry.path, e)
            from ui.dialogs.confirmation import show_error
            show_error(self, "Export Error", str(e))

    def _export_paa_fbx(self, entry: PamtFileEntry):
        """Export a PAA animation to FBX (with the rig pulled from the paired PAB).

        The PAB file that names the bones is resolved by taking the
        PAA's basename, stripping any trailing animation tag, and
        walking the same directory for a ``.pab`` of matching name.
        When no PAB is found, placeholder names (Bone_0, Bone_1, ...)
        are used so the exported FBX still imports cleanly.
        """
        from ui.dialogs.file_picker import pick_directory
        from ui.dialogs.confirmation import show_error, show_info
        from core.animation_parser import parse_paa
        from core.animation_fbx_exporter import export_animation_fbx
        from core.skeleton_parser import parse_pab, Skeleton

        try:
            data = self._vfs.read_entry_data(entry)
            anim = parse_paa(data, os.path.basename(entry.path))
        except Exception as exc:
            show_error(self, "PAA parse failed", str(exc))
            return

        # Delegate to the shared skeleton resolver. Character skeletons
        # are shared at the class level (cd_phm_* → phm_01.pab etc.)
        # and the resolver handles all 16 known rig prefixes plus
        # manual per-class overrides saved in config.
        from core.skeleton_resolver import (
            VfsManagerAdapter,
            detect_rig_prefix,
            resolve_skeleton,
        )

        skeleton: Skeleton | None = None
        rig_prefix = detect_rig_prefix(entry.path)
        manual_override = ""
        if rig_prefix:
            manual_override = self._config.get(
                f"explorer.skeleton_override.{rig_prefix}", "",
            )
        adapter = VfsManagerAdapter(self._vfs)
        try:
            resolution = resolve_skeleton(
                entry.path, adapter,
                manual_override=manual_override or None,
            )
            if resolution.skeleton is not None:
                skeleton = resolution.skeleton
                logger.info(
                    "PAA->FBX skeleton matched: rig=%s, file=%s, source=%s",
                    rig_prefix or "?",
                    os.path.basename(resolution.pab_path),
                    resolution.source,
                )
        except Exception as exc:
            logger.debug("PAA->FBX skeleton lookup failed: %s", exc)

        if skeleton is None:
            skeleton = Skeleton(path="", bones=[], bone_count=anim.bone_count or 0)

        out_dir = pick_directory(self, "Choose FBX output directory")
        if not out_dir:
            return

        try:
            fbx_path = export_animation_fbx(
                anim, skeleton, out_dir,
                name=os.path.splitext(os.path.basename(entry.path))[0],
            )
            show_info(
                self, "Animation exported",
                f"Wrote {fbx_path}\n\n"
                f"Duration: {anim.duration:.2f}s   "
                f"Frames: {anim.frame_count}   "
                f"Bones: {anim.bone_count}\n"
                f"Skeleton names: "
                f"{'resolved from ' + os.path.basename(skeleton.path) if skeleton.bones else 'placeholders (Bone_N)'}",
            )
        except Exception as exc:
            show_error(self, "FBX export failed", str(exc))

    def _export_full_character(self, entry: PamtFileEntry):
        """Unified FBX export: mesh + skeleton + optional animation in ONE file.

        Pipeline:
          1. Parse the selected mesh PAC → ParsedMesh
          2. Resolve the matching PAB skeleton (same auto-detect as
             ``_export_mesh`` and ``_export_paa_fbx``)
          3. Prompt the user to optionally pick a PAA animation file
             from the game VFS (browse in a list of available PAAs
             matching the rig prefix)
          4. Pick output directory
          5. Call ``export_fbx_with_skeleton(animation=...)`` so the
             single FBX carries skinned mesh + armature + animation
             curves
          6. Show a result dialog with what was exported
        """
        from ui.dialogs.file_picker import pick_directory
        from ui.dialogs.confirmation import show_error, show_info

        # ── Step 1: parse the mesh ──
        try:
            mesh_data = self._vfs.read_entry_data(entry)
            from core.mesh_parser import parse_mesh
            mesh = parse_mesh(mesh_data, entry.path)
        except Exception as exc:
            show_error(self, "Mesh parse failed", str(exc))
            return
        if not mesh.submeshes:
            show_error(self, "Export Error",
                       "No geometry found in this file.")
            return

        # ── Step 2: resolve skeleton ──
        from core.skeleton_resolver import (
            VfsManagerAdapter, detect_rig_prefix, resolve_skeleton,
        )
        rig_prefix = detect_rig_prefix(entry.path)
        manual_override = ""
        if rig_prefix:
            manual_override = self._config.get(
                f"explorer.skeleton_override.{rig_prefix}", "",
            )
        adapter = VfsManagerAdapter(self._vfs)
        try:
            # Pass PAC bytes for palette-match validation. Without
            # this, animal/monster meshes (cd_m0002_00_redriverhog,
            # cd_m0002_00_battlewarthog, etc.) get the wrong rig
            # because detect_rig_prefix returns None for non-player
            # asset names and the candidate ranker falls back to
            # "shortest basename" -> phm_01.pab (player human, 0
            # palette match) instead of cd_m0002_00_pig.pab (the
            # actual hog rig with 92 palette matches).
            resolution = resolve_skeleton(
                entry.path, adapter,
                manual_override=manual_override or None,
                pac_bytes=mesh_data,
            )
        except Exception as exc:
            show_error(self, "Skeleton lookup failed", str(exc))
            return
        skeleton = resolution.skeleton
        if skeleton is None or not skeleton.bones:
            show_error(
                self, "No Skeleton",
                f"No matching .pab skeleton found for rig prefix "
                f"'{rig_prefix}'.\n\n"
                f"Reason: {resolution.reason or 'unknown'}\n\n"
                f"Use the regular 'Export as FBX' action which will "
                f"prompt you to browse for a .pab manually."
            )
            return

        # NOTE: PABC palette remap was attempted but found insufficient.
        # The body PABC has 437 records but vertex slots in the body
        # submesh only reach 0-47 — those records reference torso/leg
        # bones, NOT the upper-body deformation bones. A per-submesh
        # offset/sub-palette must exist somewhere in the PAC format
        # that we haven't decoded yet. Until then, the parser's
        # direct-index fallback is left in place (its output partially
        # works for legs because slots 17,18,27,31 happen to coincide
        # with PAB[17,18,27,31] = R/L Thigh, R/L Calf).

        # ── Step 3: auto-discover PAA animations matching this rig ──
        # Strict 1+1 token-match resolver (replaces the legacy
        # substring search that surfaced sequencer cinematics like
        # ``cd_seq_10_damiandinner_phm1_ing_00.paa`` as a damian
        # animation just because the substring was present).
        # Returns two buckets: character-specific (charname appears
        # as a complete underscore-delimited segment of the PAA's
        # basename) and rig-shared (cd_<rig>_*.paa with no charname).
        from core.character_animation_resolver import (
            find_animations_for_character,
        )
        anim_result = find_animations_for_character(
            entry.path, self._vfs,
            explicit_rig_token=(rig_prefix or "").lower() or None,
        )
        candidate_paas = list(anim_result.character_specific) + list(
            anim_result.rig_shared
        )
        logger.info(
            "Export Full Character: animation resolver — char_token=%r, "
            "rig_token=%r, character-specific=%d, rig-shared=%d, "
            "reason=%r",
            anim_result.char_token,
            anim_result.rig_token,
            len(anim_result.character_specific),
            len(anim_result.rig_shared),
            anim_result.failure_reason,
        )

        animation = None
        anim_label = ""
        picked_paa_path: str | None = None
        from PySide6.QtWidgets import QMessageBox

        if not candidate_paas:
            cont = QMessageBox.question(
                self,
                "No Animations Found",
                f"No .paa animation files were found that match this "
                f"character / rig (prefix: '{rig_prefix or 'unknown'}').\n\n"
                f"Continue with mesh + skeleton only (no animation)?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if cont != QMessageBox.Yes:
                return
        else:
            picked_paa_path = self._prompt_pick_paa(
                candidate_paas, rig_prefix or "")
            logger.info("Export Full Character: picker returned %r",
                        picked_paa_path)
            if picked_paa_path is None:
                # User cancelled — abort the whole export.
                logger.info("Export Full Character: user cancelled, aborting")
                return
            if picked_paa_path == "":
                # User explicitly chose "None — no animation"
                logger.info("Export Full Character: user picked No Animation")
                # Make ABSOLUTELY sure the user knows — easy to click
                # "No Animation" by mistake when "Use Selected PAA" is
                # right next to it.
                cont = QMessageBox.question(
                    self,
                    "Confirm: No Animation",
                    "You're about to export the character WITHOUT any "
                    "animation curves.\n\n"
                    "The FBX will contain only the mesh + skeleton in "
                    "T-pose. Pressing Spacebar in Blender will do nothing.\n\n"
                    "Are you sure?",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                if cont != QMessageBox.Yes:
                    return
            else:
                try:
                    logger.info("Export Full Character: reading PAA %s",
                                picked_paa_path)
                    # Look up the PamtFileEntry, then read via VFS.
                    paa_entry = self._lookup_vfs_entry(picked_paa_path)
                    if paa_entry is None:
                        raise FileNotFoundError(
                            f"PAA path {picked_paa_path!r} not found in VFS"
                        )
                    paa_data = self._vfs.read_entry_data(paa_entry)
                    # Use parse_paa_with_resolution so link-variant PAAs
                    # (≈ 19% of corpus, e.g. cd_damian_*walk*.paa is a
                    # link to a base file) get auto-followed to the real
                    # animation data. Plain parse_paa returns 1 frame /
                    # 1 bone shells for those.
                    #
                    # Pull the PAB's per-bone hash list and pass it in.
                    # The parser uses these hashes to deterministically
                    # map each PAA track to its exact skeleton bone via
                    # the 24-bit hash field in the inter-track gap.
                    # No heuristics — exact 1+1=2 mapping.
                    pab_hashes = self._extract_pab_bone_hashes(
                        resolution.pab_path)
                    from core.animation_parser import parse_paa_with_resolution
                    animation = parse_paa_with_resolution(
                        paa_data, picked_paa_path,
                        vfs=self._vfs, max_hops=5,
                        pab_bone_hashes=pab_hashes,
                        pab_bone_count=len(skeleton.bones),
                    )
                    logger.info(
                        "Export Full Character: parsed PAA — "
                        "%d frames, %.3fs, %d animated bones",
                        animation.frame_count, animation.duration,
                        animation.bone_count,
                    )
                    # No heuristic mapping needed — parse_paa_with_resolution
                    # already placed each track at its exact skeleton
                    # bone index via PAB-hash matching. Tracks for
                    # bones we couldn't match get identity rotation
                    # (no movement) so unmatched bones stay at bind.
                    # Loud warning if it's STILL a link/empty after
                    # resolution — we silently exported empty before.
                    if animation.frame_count <= 1 or animation.bone_count <= 1:
                        from PySide6.QtWidgets import QMessageBox as _QMB
                        cont = _QMB.question(
                            self,
                            "Animation Looks Empty",
                            f"The selected PAA resolved to only "
                            f"{animation.frame_count} frame(s) with "
                            f"{animation.bone_count} bone(s).\n\n"
                            f"That usually means it's a stub / unresolved "
                            f"link reference. The exported FBX will play "
                            f"a static T-pose, not an animation.\n\n"
                            f"Continue anyway?",
                            _QMB.Yes | _QMB.No, _QMB.No,
                        )
                        if cont != _QMB.Yes:
                            return
                    anim_label = (
                        f" + {os.path.basename(picked_paa_path)} "
                        f"({animation.frame_count} frames, "
                        f"{animation.duration:.2f}s)"
                    )
                except Exception as exc:
                    logger.exception(
                        "Export Full Character: failed to read/parse %s",
                        picked_paa_path,
                    )
                    cont = QMessageBox.question(
                        self,
                        "Animation Read Failed",
                        f"Could not read or parse:\n  {picked_paa_path}\n\n"
                        f"Error: {exc}\n\n"
                        f"Continue without animation?",
                        QMessageBox.Yes | QMessageBox.No,
                        QMessageBox.No,
                    )
                    if cont != QMessageBox.Yes:
                        return
                    animation = None

        # ── Step 4: pick output dir ──
        output_dir = pick_directory(self, "Choose FBX output directory")
        if not output_dir:
            return

        # ── Step 5: export ──
        clean_path = entry.path.replace("\\", "/")
        basename = os.path.splitext(clean_path)[0].replace("/", "_")
        if animation is not None:
            basename += "_anim"

        # ── Step 4b: resolve textures from the .pac_xml companion ──
        # Strict, no-fallback chain (see
        # test_only/research/2026-05-08_fbx_export_pipeline/
        # 14_texture_resolution_chain.md). Returns one record per
        # submesh naming the canonical VFS path of every bound
        # texture. When a Material has no _baseColorTexture and no
        # _overlayColorTexture (procedural mask-driven shaders,
        # ~23% of real Materials) the corresponding base_color is
        # None — the FBX exporter then leaves that submesh's
        # Material with no diffuse binding rather than guessing.
        try:
            from core.pac_xml_texture_resolver import (
                resolve_pac_textures, vfs_manager_texture_view,
            )
            tex_view = vfs_manager_texture_view(self._vfs)
            tex_manifest = resolve_pac_textures(
                entry.path, tex_view,
                [sm.name for sm in mesh.submeshes],
            )
            n_with_base = sum(1 for r in tex_manifest.records
                              if r.base_color)
            logger.info(
                "Full Character textures: has_xml=%s, "
                "submeshes=%d, base_color=%d/%d, reason=%r",
                tex_manifest.has_xml,
                len(tex_manifest.records),
                n_with_base, len(tex_manifest.records),
                tex_manifest.failure_reason or '',
            )
        except Exception as exc:
            # Texture resolution must NEVER block the export. If the
            # XML companion is missing or unparseable we still ship
            # the mesh+skeleton FBX without textures.
            logger.warning(
                "Full Character: texture resolver raised %s — "
                "exporting without textures", exc,
            )
            tex_manifest = None
            tex_view = None

        try:
            self._progress.set_status(
                f"Exporting full character FBX to {output_dir}..."
            )
            from core.mesh_exporter import export_fbx_with_skeleton
            fbx_path = export_fbx_with_skeleton(
                mesh, skeleton, output_dir,
                name=basename,
                scale=1.0,
                # Filter OFF by default — the heuristic was deleting real
                # mesh (foot soles, rigid extremities). 1:1 vertex export
                # keeps every vertex; the round-trip is lossless without
                # the filter.
                filter_unskinned_outliers=False,
                animation=animation,
                fps=30.0,
                textures=tex_manifest,
                texture_vfs=tex_view,
            )
        except Exception as exc:
            show_error(self, "FBX export failed", str(exc))
            return

        # ── Step 6: show summary ──
        rig_label = os.path.basename(resolution.pab_path) if resolution.pab_path else "?"
        # Texture summary lines — only when the resolver produced
        # records, so the dialog stays clean for legacy paths.
        tex_lines = ""
        if tex_manifest is not None and tex_manifest.records:
            n_base = sum(1 for r in tex_manifest.records
                         if r.base_color)
            from core.pac_xml_texture_resolver import collect_unique_dds_paths
            n_files = len(collect_unique_dds_paths(tex_manifest))
            tex_lines = (
                f"Textures: {n_base}/{len(tex_manifest.records)} "
                f"submeshes have a base color, {n_files} unique DDS "
                f"saved to {basename}_textures/\n"
            )
        elif tex_manifest is not None and tex_manifest.failure_reason:
            tex_lines = (
                f"Textures: not exported "
                f"({tex_manifest.failure_reason})\n"
            )
        show_info(
            self,
            "Full Character Exported",
            f"Wrote {fbx_path}\n\n"
            f"Mesh: {mesh.total_vertices:,} verts, "
            f"{mesh.total_faces:,} faces, "
            f"{len(mesh.submeshes)} submeshes\n"
            f"Skeleton: {len(skeleton.bones)} bones (rig {rig_label}, "
            f"source {resolution.source})\n"
            f"Animation:{anim_label or ' none'}\n"
            f"{tex_lines}"
            f"\nSidecar: {os.path.basename(fbx_path)}.cfmeta.json (preserves "
            f"spike-filter for round-trip)\n"
            f"Debug log: {os.path.basename(fbx_path)}.debug.txt"
        )

    def _export_complete_character_from_app_xml(
        self, entry: PamtFileEntry,
    ):
        """Right-click handler for ``.app_xml`` entries. Delegates
        to the path-based exporter using the entry's verbatim path
        — no name guessing, no companion search.
        """
        self._export_complete_character_from_path(entry.path)

    def _export_complete_character_from_pac(
        self, entry: PamtFileEntry,
    ):
        """Right-click handler for ``.pac`` entries.

        Strict reverse lookup:
          * Treat the clicked PAC as the body PAC of a character.
          * Search every ``character/*.app_xml`` whose ``<Nude>``
            ``<Prefab Name="...">`` equals the clicked PAC's
            basename stem (cached after first scan).
          * Exactly one hit → call the path-based exporter on it.
          * Multiple hits → show a picker so the user resolves
            which appearance variant they want.
          * Zero hits → strict refusal with an error dialog
            naming the body-PAC stem we looked for.
        """
        from ui.dialogs.confirmation import show_error
        from core.character_appearance_resolver import (
            find_app_xmls_for_body_pac,
        )

        try:
            hits = find_app_xmls_for_body_pac(entry.path, self._vfs)
        except Exception as exc:
            show_error(
                self, "Export Complete Character — lookup failed",
                f"Couldn't scan appearance manifests:\n\n{exc}",
            )
            return

        if not hits:
            stem = os.path.splitext(
                os.path.basename(entry.path.replace("\\", "/"))
            )[0]
            show_error(
                self, "No appearance manifest found",
                f"None of the .app_xml files in character/ list "
                f"\"{stem}\" as their <Nude> Prefab.\n\n"
                f"This action only works for body PACs (the ones "
                f"named in <Nude>). For accessory PACs (head, hair, "
                f"armor pieces) right-click the matching "
                f".app_xml directly."
            )
            return

        if len(hits) == 1:
            self._export_complete_character_from_path(hits[0])
            return

        # Multiple appearance variants share this body PAC — let
        # the user pick which one.
        from PySide6.QtWidgets import QInputDialog
        choices = [os.path.basename(h) for h in hits]
        picked, ok = QInputDialog.getItem(
            self,
            "Choose Appearance",
            f"This body PAC is used by {len(hits)} appearance "
            f"variants. Which one should I export?",
            choices, 0, False,
        )
        if not ok or not picked:
            return
        # Map back from basename to full path (basenames are
        # unique because they're per-character VFS paths).
        for h in hits:
            if os.path.basename(h) == picked:
                self._export_complete_character_from_path(h)
                return

    def _export_complete_character_from_path(
        self, app_xml_path: str,
    ):
        """Export every PAC named by ``app_xml_path`` as one merged FBX.

        Pipeline (strict 1+1, no fallback):
          1. Resolve the appearance manifest from the .app_xml.
          2. Walk every <Prefab Name="..."> in <Nude>/<Head>/<Hair>/
             <Armor>; locate each prefab in the VFS.
          3. Extract every PAC reference from each prefab.
          4. Read+parse every PAC; resolve a SHARED skeleton from
             the body PAC's palette match; per-PAC palette resolve;
             per-PAC texture resolve.
          5. Merge all submeshes + texture manifests; export ONE
             FBX with all parts skinned to the shared rig.
        """
        from ui.dialogs.file_picker import pick_directory
        from ui.dialogs.confirmation import show_error, show_info

        # ── Step 1: pick output dir ──
        output_dir = pick_directory(
            self, "Choose FBX output directory",
        )
        if not output_dir:
            return

        # ── Step 2: derive a clean basename from the .app_xml ──
        # cd_phw_damian_00000.app_xml -> cd_phw_damian_00000_complete
        clean_path = app_xml_path.replace("\\", "/")
        stem = os.path.splitext(os.path.basename(clean_path))[0]
        basename = f"{stem}_complete"

        # ── Step 3: invoke the strict orchestrator ──
        try:
            self._progress.set_status(
                f"Exporting complete character from "
                f"{os.path.basename(clean_path)} to {output_dir}..."
            )
            from core.character_complete_exporter import (
                export_complete_character,
            )
            result = export_complete_character(
                app_xml_path, output_dir, basename, self._vfs,
            )
        except Exception as exc:
            logger.exception(
                "export_complete_character raised: %s", exc,
            )
            show_error(
                self, "Export Complete Character — failed",
                f"The export pipeline raised an exception:\n\n{exc}",
            )
            return

        # ── Step 4: surface the result ──
        if result.failure_reason:
            show_error(
                self, "Export Complete Character — failed",
                f"{result.failure_reason}\n\n"
                f"PACs requested: {len(result.pacs_requested)}\n"
                f"PACs loaded   : {len(result.pacs_loaded)}\n"
                f"PACs skipped  : {len(result.pacs_skipped)}",
            )
            return

        # Build a per-prefab summary line so the user sees what
        # came in — useful when a prefab silently produced 0 PACs.
        skipped_lines = ""
        if result.pacs_skipped:
            skipped_lines = "\nPACs skipped:\n" + "\n".join(
                f"  {p}: {r}" for p, r in result.pacs_skipped[:10]
            )
            if len(result.pacs_skipped) > 10:
                skipped_lines += (
                    f"\n  ... +{len(result.pacs_skipped) - 10} more"
                )

        show_info(
            self, "Complete Character Exported",
            f"Wrote {result.fbx_path}\n\n"
            f"Manifest: {result.app_xml_path}\n"
            f"PACs    : {len(result.pacs_loaded)}/"
            f"{len(result.pacs_requested)} loaded "
            f"({len(result.pacs_skipped)} skipped)\n"
            f"Skeleton: {result.skeleton_bone_count} bones "
            f"({os.path.basename(result.skeleton_pab_path)})\n"
            f"Mesh    : {result.total_vertices:,} verts, "
            f"{result.total_faces:,} faces, "
            f"{result.total_submeshes} submeshes\n"
            f"Textures: {result.unique_textures} unique DDS, "
            f"{result.base_color_count}/{result.total_submeshes} "
            f"submeshes have a base color\n"
            f"{skipped_lines}\n"
            f"\nDDS folder: {basename}_textures/\n"
            f"Sidecar   : {basename}.fbx.cfmeta.json\n"
            f"Debug log : {basename}.fbx.debug.txt"
        )

    def _extract_pab_bone_hashes(self, pab_path: str) -> list[int]:
        """Read raw PAB bytes and pull the 24-bit per-bone hash that
        Pearl Abyss stores at the start of each bone record.

        Returns one hash per bone, in PAB-order. Empty list if the
        PAB cannot be loaded.

        These hashes are the EXACT identifier the PAA file uses to
        reference bones in its inter-track gaps. Matching by hash
        gives a deterministic 1+1=2 track-to-bone mapping with
        zero ambiguity.
        """
        import struct as _struct
        pab_entry = self._lookup_vfs_entry(pab_path) if pab_path else None
        if pab_entry is None:
            return []
        try:
            pab_data = self._vfs.read_entry_data(pab_entry)
        except Exception:
            return []
        if len(pab_data) < 0x18 or pab_data[:4] != b"PAR ":
            return []
        try:
            bone_count = _struct.unpack_from('<H', pab_data, 0x14)[0]
        except _struct.error:
            return []
        hashes: list[int] = []
        off = 0x17
        for i in range(bone_count):
            if off + 4 > len(pab_data):
                break
            hash_lo24 = _struct.unpack_from('<I', pab_data, off)[0] & 0x00FFFFFF
            name_len = pab_data[off + 3]
            hashes.append(hash_lo24)
            # Per-bone record stride = 4 (hash+name_len) + name + 4 (parent)
            #                        + 256 (4 matrices) + 40 (SRT) + 1 (align)
            off += 4 + name_len + 4 + 256 + 40 + 1
        return hashes

    def _map_paa_to_deformation_bones(self, animation, skeleton):
        """Map PAA tracks (bone-major, identity-padded) onto the
        skeleton's DEFORMATION bones in hierarchy order.

        Why: PAA files (especially the link-with-embedded-tracks
        layout used by Damian/phw walks) carry one track per bone
        in a specific order — but the skeleton has bones we know
        the animation never targets:
          - ``B_TL_*``       IK helper / control bones (climb target,
                             foot/hand IK, position trackers)
          - ``B_face_*``     facial rig
          - ``B_Eyeball_*``  eye direction
          - ``B_Eyeside_*``  eye corner sliders
          - ``B_Forehead_*`` brow
          - ``B_Chin_*``     chin / jaw shape
          - ``B_Lip_*``      lip / mouth
          - ``B_Cheek_*``    cheek shape
          - ``B_Tongue_*``   tongue
          - ``B_Ear_*``      ear shape
          - ``Bip_Weapon_*`` weapon attach points
          - bones whose bind matrix is at world origin (helpers)

        Filtering these out leaves the BODY DEFORMATION bones
        (Bip01, Pelvis, Spine, Thighs, Calves, Feet, Neck, Head,
        Shoulders, Arms, Forearms, Hands, Fingers) which are the
        bones a walk / run / idle PAA actually animates.

        Tracks then map track[i] → kept_bone[i] in skeleton order.
        """
        from core.animation_parser import AnimationKeyframe
        import copy

        # Identify deformation bones (skip helper families).
        helper_prefixes = (
            "B_TL_", "B_face", "B_Eyeball", "B_Eyeside", "B_Forehead",
            "B_Chin", "B_Lip", "B_Cheek", "B_Tongue", "B_Ear",
            "B_MoveControl", "B_CatchMe", "B_EnemyCatch",
            "Bip_Weapon",
        )
        keep_indices: list[int] = []
        for b in skeleton.bones:
            name = b.name or ""
            if any(name.startswith(p) for p in helper_prefixes):
                continue
            # Skip bones at world origin (likely helpers we missed)
            bm = getattr(b, "bind_matrix", None)
            if bm and len(bm) == 16:
                tx, ty, tz = bm[12], bm[13], bm[14]
                if abs(tx) < 1e-4 and abs(ty) < 1e-4 and abs(tz) < 1e-4:
                    continue
            keep_indices.append(b.index)

        n_tracks = animation.bone_count
        n_kept = len(keep_indices)
        logger.info(
            "Export Full Character: %d PAA tracks → %d deformation bones "
            "(skipped %d helper/face/eye bones from %d total)",
            n_tracks, n_kept,
            len(skeleton.bones) - n_kept, len(skeleton.bones),
        )
        if n_kept == 0:
            return animation

        # Build new keyframes: each frame has bone_count entries; only
        # the kept bones get the PAA track values, others stay identity.
        new_frames = []
        bone_count = len(skeleton.bones)
        for kf in animation.keyframes:
            new_rotations = [(0.0, 0.0, 0.0, 1.0)] * bone_count
            for ti in range(min(n_tracks, n_kept)):
                bi = keep_indices[ti]
                if ti < len(kf.bone_rotations):
                    new_rotations[bi] = kf.bone_rotations[ti]
            new_frames.append(AnimationKeyframe(
                frame_index=kf.frame_index,
                bone_rotations=new_rotations,
            ))

        anim = copy.copy(animation)
        anim.keyframes = new_frames
        anim.bone_count = bone_count
        return anim

    def _lookup_vfs_entry(self, path: str):
        """Find a ``PamtFileEntry`` by its in-game path.

        VfsManager doesn't expose a path→entry lookup directly, so we
        walk the loaded PAMTs (same data the picker already iterates).
        Returns the first entry whose ``.path`` matches the requested
        string (case-insensitive, normalised slashes), or None if no
        match is found.
        """
        target = path.replace("\\", "/").lower()
        for _group, pamt in getattr(self._vfs, "_pamt_cache", {}).items():
            for entry in getattr(pamt, "file_entries", []):
                if (entry.path or "").replace("\\", "/").lower() == target:
                    return entry
        return None

    def _prompt_pick_paa(self, candidates: list[str],
                         rig_prefix: str) -> str | None:
        """Open a list-picker dialog showing matching PAA files.

        Returns:
          - The selected PAA path (string)
          - "" (empty string) if the user clicks the "None — skip" button
          - None if the user clicks Cancel / closes the dialog
        """
        from PySide6.QtWidgets import (
            QDialog, QVBoxLayout, QHBoxLayout, QLineEdit, QListWidget,
            QPushButton, QLabel, QListWidgetItem,
        )
        from PySide6.QtCore import Qt

        dlg = QDialog(self)
        dlg.setWindowTitle("Pick Animation (PAA)")
        dlg.resize(720, 480)

        layout = QVBoxLayout(dlg)

        info = QLabel(
            f"Found <b>{len(candidates)}</b> .paa files matching this "
            f"character / rig"
            + (f" (prefix <code>{rig_prefix}</code>)" if rig_prefix else "")
            + ".<br>Pick one to bake into the FBX, or click "
            "<b>No Animation</b> to skip."
        )
        info.setTextFormat(Qt.RichText)
        info.setWordWrap(True)
        layout.addWidget(info)

        # Search filter
        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("Filter:"))
        search_box = QLineEdit()
        search_box.setPlaceholderText(
            "Type to filter — e.g. 'idle', 'walk', 'talk'..."
        )
        search_row.addWidget(search_box, stretch=1)
        layout.addLayout(search_row)

        # File list
        list_widget = QListWidget()
        list_widget.setAlternatingRowColors(True)
        for p in candidates:
            item = QListWidgetItem(p)
            item.setData(Qt.UserRole, p)
            list_widget.addItem(item)
        layout.addWidget(list_widget, stretch=1)

        # Filter logic
        def _apply_filter(text: str):
            t = text.strip().lower()
            for i in range(list_widget.count()):
                it = list_widget.item(i)
                hidden = bool(t) and (t not in it.text().lower())
                it.setHidden(hidden)
        search_box.textChanged.connect(_apply_filter)

        # Buttons — "Use Selected PAA" is the default (Enter triggers it).
        # "No Animation" is intentionally smaller / less prominent so it
        # can't be hit by accident, and clicking it pops a confirmation
        # in the caller.
        btn_row = QHBoxLayout()
        btn_pick = QPushButton("✓ Use Selected PAA")
        btn_pick.setDefault(True)
        btn_pick.setAutoDefault(True)
        btn_pick.setStyleSheet(
            "QPushButton { padding: 8px 16px; font-weight: bold; }"
        )
        btn_none = QPushButton("No Animation")
        btn_none.setStyleSheet(
            "QPushButton { padding: 6px 10px; color: #888; }"
        )
        btn_cancel = QPushButton("Cancel")
        btn_row.addWidget(btn_pick)
        btn_row.addStretch(1)
        btn_row.addWidget(btn_none)
        btn_row.addWidget(btn_cancel)
        layout.addLayout(btn_row)

        result = {"path": None}

        def _on_pick():
            sel = list_widget.currentItem()
            if sel is None:
                # No selection — show feedback so user knows to pick.
                from PySide6.QtWidgets import QMessageBox as _QMB
                _QMB.information(
                    dlg, "Pick a PAA First",
                    "Click on a PAA file in the list above first, then "
                    "click Use Selected PAA. Or double-click any entry."
                )
                return
            result["path"] = sel.data(Qt.UserRole)
            dlg.accept()

        def _on_none():
            result["path"] = ""
            dlg.accept()

        def _on_cancel():
            result["path"] = None
            dlg.reject()

        btn_pick.clicked.connect(_on_pick)
        btn_none.clicked.connect(_on_none)
        btn_cancel.clicked.connect(_on_cancel)
        list_widget.itemDoubleClicked.connect(lambda *_: _on_pick())

        # Pre-select the first item so Enter / double-click works
        # immediately. Also disable Use Selected if no items at all
        # (shouldn't happen since caller checks candidates non-empty).
        if list_widget.count() > 0:
            list_widget.setCurrentRow(0)
        else:
            btn_pick.setEnabled(False)

        # Disable Use Selected when no item is highlighted (after filter
        # narrows the list to zero, etc.).
        def _on_selection_changed():
            btn_pick.setEnabled(list_widget.currentItem() is not None
                                and not list_widget.currentItem().isHidden())
        list_widget.currentItemChanged.connect(
            lambda *_: _on_selection_changed())

        if dlg.exec() == QDialog.Accepted:
            return result["path"]
        return None

    # ─── Shared helpers for FBX-with-skeleton flows ─────────────────

    def _prompt_skeleton_missing(
        self, asset_path: str, rig_prefix: str, reason: str,
    ) -> str:
        """Three-choice dialog shown when skeleton auto-resolve failed.

        Returns one of:
          * ``"browse"``   — user clicked "Browse for .pab..."
          * ``"continue"`` — user accepted a mesh-only (no armature) export
          * ``"cancel"``   — user cancelled the whole export

        The ``rig_prefix`` (if non-empty) is surfaced in the dialog so
        the user knows what family of asset they're dealing with
        (``phm`` = male hero, ``phw`` = female hero, etc.).
        """
        from PySide6.QtWidgets import QMessageBox

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("Skeleton not found")
        header = f"No matching .pab skeleton was found for {os.path.basename(asset_path)}."
        if rig_prefix:
            header += (
                f"\n\nDetected rig family: '{rig_prefix}' "
                f"(expected something like {rig_prefix}_01.pab)."
            )
        box.setText(header)
        box.setInformativeText(
            f"Reason: {reason}\n\n"
            "Choose what to do:\n\n"
            "  • Browse for .pab... — pick a rig file by hand "
            "(remembered for future exports of this class).\n"
            "  • Continue — export a mesh-only FBX (no armature, no skin weights).\n"
            "  • Cancel — abort the export."
        )
        browse_btn = box.addButton("Browse for .pab...", QMessageBox.ActionRole)
        continue_btn = box.addButton("Continue", QMessageBox.AcceptRole)
        cancel_btn = box.addButton("Cancel", QMessageBox.RejectRole)
        box.setDefaultButton(browse_btn)
        box.exec()
        clicked = box.clickedButton()
        if clicked is browse_btn:
            return "browse"
        if clicked is continue_btn:
            return "continue"
        return "cancel"

    def _pick_skeleton_from_vfs(self, rig_prefix: str = "") -> str:
        """Open a file-picker dialog showing every .pab in the VFS.

        When ``rig_prefix`` is supplied, the dialog pre-scrolls to
        the best-guess candidates (by the same ranking algorithm
        the auto-resolver uses). Returns the chosen VFS path, or
        empty string when the user cancelled.
        """
        from PySide6.QtWidgets import (
            QDialog, QDialogButtonBox, QLabel, QLineEdit, QListWidget,
            QListWidgetItem, QVBoxLayout,
        )
        from core.skeleton_resolver import (
            VfsManagerAdapter,
            rank_skeleton_candidates,
        )

        adapter = VfsManagerAdapter(self._vfs)
        all_pabs = adapter.list_pab_paths()
        if not all_pabs:
            from ui.dialogs.confirmation import show_error
            show_error(
                self, "No .pab files",
                "No .pab skeleton files are visible through the VFS. "
                "Load the game archives that contain the shared "
                "character rigs before retrying.",
            )
            return ""

        ordered = rank_skeleton_candidates(rig_prefix or None, all_pabs)

        dialog = QDialog(self)
        dialog.setWindowTitle("Pick a skeleton (.pab)")
        dialog.setMinimumSize(640, 480)
        layout = QVBoxLayout(dialog)

        hint = QLabel(
            f"Rig family detected: '{rig_prefix}'" if rig_prefix
            else "No rig family detected — every .pab in the VFS listed below."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        search = QLineEdit()
        search.setPlaceholderText("Filter by name or path...")
        layout.addWidget(search)

        listw = QListWidget()
        for path in ordered:
            item = QListWidgetItem(path)
            listw.addItem(item)
        if listw.count() > 0:
            listw.setCurrentRow(0)
        layout.addWidget(listw)

        def _apply_filter():
            text = search.text().strip().lower()
            for i in range(listw.count()):
                item = listw.item(i)
                item.setHidden(bool(text) and text not in item.text().lower())
        search.textChanged.connect(_apply_filter)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel,
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if dialog.exec() != QDialog.Accepted:
            return ""
        current = listw.currentItem()
        if current is None:
            return ""
        return current.text()

    def _dump_hkx_json(self, entry: PamtFileEntry):
        """Dump an HKX to JSON using the Layer 5 HkxDocument facade."""
        from ui.dialogs.file_picker import pick_save_file
        from ui.dialogs.confirmation import show_error, show_info
        from core.havok_tag0_document import HkxDocument

        try:
            data = self._vfs.read_entry_data(entry)
            hkx = HkxDocument.load(data)
        except Exception as exc:
            show_error(self, "HKX load failed", str(exc))
            return

        save_path = pick_save_file(
            self, "Save HKX JSON dump",
            default_name=os.path.splitext(os.path.basename(entry.path))[0] + ".hkx.json",
            filters="JSON (*.json);;All (*.*)",
        )
        if not save_path:
            return

        try:
            text = hkx.to_json(indent=2)
            with open(save_path, "w", encoding="utf-8") as f:
                f.write(text)
            show_info(
                self, "HKX dumped",
                f"Wrote {save_path}\n\n"
                f"SDK: {hkx.sdk_version}\n"
                f"Types: {len(hkx.registry.types)}\n"
                f"Instances: {sum(1 for _ in hkx.iter_instances())}\n"
                f"Patches: {len(hkx.index.patches)} blocks",
            )
        except Exception as exc:
            show_error(self, "HKX JSON export failed", str(exc))

    def _hkx_risk_report(self, entry: PamtFileEntry):
        """Show the physics-edit-risk verdict for an HKX file.

        Uses ``core.havok_parser.assess_mesh_edit_risk`` which recognises
        cloth / softbody / mesh-shape / ragdoll / rigid body classes
        and produces severity + reasons. The result is the same block
        the mesh-sidecar service exposes before a mesh edit commits.
        """
        from ui.dialogs.confirmation import show_info, show_error
        from core.havok_parser import assess_mesh_edit_risk

        try:
            data = self._vfs.read_entry_data(entry)
            risk = assess_mesh_edit_risk(data)
        except Exception as exc:
            show_error(self, "HKX risk assessment failed", str(exc))
            return

        if risk.severity == "none":
            msg = "No physics-binding classes detected. Safe to edit the paired mesh."
        else:
            msg = risk.format_message(hkx_path=entry.path)
        show_info(self, f"Physics risk: {risk.severity.upper()}", msg)

    def _diagnose_dye(self, entry: PamtFileEntry):
        """Run the dye-system diagnostic for a mesh prefab.

        The query is built from the mesh basename without the extension
        — so ``character/cd_phm_00_cloak_0060.pac`` maps to the prefab
        lookup ``cd_phm_00_cloak_0060``. The diagnostic reports whether
        the prefab is registered as dyeable and tells the modder
        whether a raw .dds edit will take effect.
        """
        from ui.dialogs.confirmation import show_info, show_error
        from core.dye_diagnostics import diagnose_armor_dye

        base = os.path.splitext(os.path.basename(entry.path))[0]
        try:
            report = diagnose_armor_dye(self._vfs, base)
        except Exception as exc:
            show_error(self, "Dye diagnostic failed", str(exc))
            return

        show_info(
            self, f"Dye diagnostic: {base}",
            report.format_message(),
        )

    def _import_mesh(self, entry: PamtFileEntry, fmt: str = "obj"):
        """Import an OBJ or FBX file, rebuild the mesh, and preview the result."""
        if fmt == "fbx":
            mesh_path = pick_file(self, "Select FBX File", filters="FBX Files (*.fbx);;All Files (*.*)")
        else:
            mesh_path = pick_file(self, "Select OBJ File", filters="OBJ Files (*.obj);;All Files (*.*)")
        if not mesh_path:
            return

        try:
            self._progress.set_status(f"Importing {os.path.basename(mesh_path)}...")

            if fmt == "fbx":
                from core.mesh_importer import import_fbx, build_mesh, transfer_pam_edit_to_pamlod_mesh
                imported = import_fbx(mesh_path)
                obj_path = mesh_path
            else:
                from core.mesh_importer import import_obj, build_mesh, transfer_pam_edit_to_pamlod_mesh
                imported = import_obj(mesh_path)
                obj_path = mesh_path

            if not imported.submeshes:
                show_error(self, "Import Error", "No geometry found in OBJ file.")
                return

            # Read original data for rebuild.
            # IMPORTANT — route through the baseline manager so the
            # donor source is the PRISTINE PAC, not whatever is
            # currently on disk. Prevents compound-corruption when
            # this method (or the patch variant) is run more than
            # once on the same PAC.
            original_data = self._mesh_baseline.get_or_snapshot(
                entry.path,
                live_read=lambda: self._vfs.read_entry_data(entry),
                source_paz=os.path.basename(entry.paz_file or ""),
            )

            # Override source info from the target entry
            imported.path = entry.path
            ext = os.path.splitext(entry.path.lower())[1]
            imported.format = "pac" if ext == ".pac" else "pamlod" if ext == ".pamlod" else "pam"

            # Build the strict skin write-back sidecar from the donor
            # PAC + its sibling PAB. Without this, FBX files coming
            # from Blender's native exporter (which lack the
            # ``.cfmeta.json`` companion that CF's own export writes)
            # produce a silent fallback where painted weights are
            # ignored and donor bytes survive verbatim. With the
            # sidecar attached, ``build_pac``'s strict write-back
            # fires and writes user-edited weights into the PAC's
            # vertex byte slots.
            if fmt == "fbx":
                from core.mesh_importer import build_skin_writeback_sidecar
                imported._cfmeta_sidecar = build_skin_writeback_sidecar(
                    original_data, vfs=self._vfs, pac_path=entry.path,
                )

            # Build new binary
            new_data = build_mesh(imported, original_data)

            # Preview the rebuilt mesh
            basename = os.path.basename(entry.path)
            temp_path = os.path.join(self._temp_dir, basename)
            with open(temp_path, "wb") as f:
                f.write(new_data)
            self._preview.preview_file(
                temp_path,
                vfs=self._vfs,
                vfs_path=entry.path.replace("\\", "/"),
            )

            # Store for potential patching
            self._pending_mesh_data[entry.path.lower()] = {
                "entry": entry,
                "new_data": new_data,
                "imported": imported,
                "obj_path": obj_path,
            }

            patch_label = f"Import {fmt.upper()} + Patch to Game"
            self._progress.set_status(
                f"Imported: {imported.total_vertices:,} verts, "
                f"{imported.total_faces:,} faces, {len(new_data):,} bytes. "
                f"Right-click > '{patch_label}' to apply."
            )
            show_info(self, "Import Complete",
                      f"Imported {os.path.basename(mesh_path)}\n\n"
                      f"Vertices: {imported.total_vertices:,}\n"
                      f"Faces: {imported.total_faces:,}\n"
                      f"Submeshes: {len(imported.submeshes)}\n"
                      f"New size: {len(new_data):,} bytes\n\n"
                      f"This step only previews the rebuilt mesh.\n"
                      f"Use 'Import OBJ + Patch to Game' to write to game files.")

        except Exception as e:
            self._progress.set_status(f"Import error: {e}")
            logger.error("Mesh import error for %s: %s", entry.path, e)
            show_error(self, "Import Error", str(e))

    def _import_and_patch_mesh(self, entry: PamtFileEntry, fmt: str = "obj"):
        """Import OBJ or FBX, rebuild binary, and patch directly into the game."""
        if fmt == "fbx":
            mesh_path = pick_file(self, "Select FBX File", filters="FBX Files (*.fbx);;All Files (*.*)")
        else:
            mesh_path = pick_file(self, "Select OBJ File", filters="OBJ Files (*.obj);;All Files (*.*)")
        if not mesh_path:
            return
        obj_path = mesh_path  # kept for downstream references

        try:
            self._progress.set_status(f"Importing and patching {os.path.basename(mesh_path)}...")

            if fmt == "fbx":
                from core.mesh_importer import import_fbx, build_mesh, transfer_pam_edit_to_pamlod_mesh
                imported = import_fbx(mesh_path)
            else:
                from core.mesh_importer import import_obj, build_mesh, transfer_pam_edit_to_pamlod_mesh
                imported = import_obj(mesh_path)

            if not imported.submeshes:
                show_error(self, "Import Error", "No geometry found in OBJ file.")
                return

            # Read original data.
            # IMPORTANT — route through the baseline manager so the
            # donor source is the PRISTINE PAC captured the very
            # first time this mesh was touched, not the live (and
            # potentially already-patched) bytes. Without this
            # safeguard, a second patch inherits tiny data drifts
            # from the first and compounds them until the mesh in-
            # game shatters.
            original_data = self._mesh_baseline.get_or_snapshot(
                entry.path,
                live_read=lambda: self._vfs.read_entry_data(entry),
                source_paz=os.path.basename(entry.paz_file or ""),
            )

            # Set format from target entry
            imported.path = entry.path
            ext = os.path.splitext(entry.path.lower())[1]
            imported.format = "pac" if ext == ".pac" else "pamlod" if ext == ".pamlod" else "pam"

            # Same strict skin write-back sidecar as the preview-
            # only flow. Without this, Blender-native FBX imports
            # land in the silent-donor-preserve branch.
            if fmt == "fbx":
                from core.mesh_importer import build_skin_writeback_sidecar
                imported._cfmeta_sidecar = build_skin_writeback_sidecar(
                    original_data, vfs=self._vfs, pac_path=entry.path,
                )

            # Build new binary
            new_data = build_mesh(imported, original_data)
            self._pending_mesh_data[entry.path.lower()] = {
                "entry": entry,
                "new_data": new_data,
                "imported": imported,
                "obj_path": obj_path,
            }

            # Find which package group this entry belongs to
            paz_dir = os.path.basename(os.path.dirname(entry.paz_file))

            # Load PAMT data for this group
            pamt_data = self._vfs.load_pamt(paz_dir)

            extra_mod_files = []
            pair_note = ""
            pair_warning = ""
            if imported.format == "pam":
                paired_path = entry.path[:-4] + ".pamlod"
                paired_entry = next(
                    (e for e in pamt_data.file_entries if e.path.lower() == paired_path.lower()),
                    None,
                )
                if paired_entry:
                    try:
                        # Same baseline protection for the paired
                        # LOD so the LOD pass is also idempotent
                        # across repeat patches.
                        paired_original = self._mesh_baseline.get_or_snapshot(
                            paired_entry.path,
                            live_read=lambda pe=paired_entry: self._vfs.read_entry_data(pe),
                            source_paz=os.path.basename(paired_entry.paz_file or ""),
                        )
                        paired_mesh = transfer_pam_edit_to_pamlod_mesh(
                            imported, original_data, paired_original, paired_entry.path
                        )
                        paired_new_data = build_mesh(paired_mesh, paired_original)
                        from core.repack_engine import ModifiedFile
                        extra_mod_files.append(ModifiedFile(
                            data=paired_new_data,
                            entry=paired_entry,
                            pamt_data=pamt_data,
                            package_group=paz_dir,
                        ))
                        pair_note = f"\nPaired LOD: {paired_entry.path}"
                    except Exception as pair_exc:
                        pair_warning = f"\nPaired LOD not patched: {pair_exc}"
                        logger.warning("Paired PAMLOD patch skipped for %s: %s", entry.path, pair_exc)

            # Confirm with user
            if not confirm_action(self, "Patch to Game",
                                  f"Replace {entry.path} in game?\n\n"
                                  f"Original: {len(original_data):,} bytes\n"
                                  f"New: {len(new_data):,} bytes\n"
                                  f"Vertices: {imported.total_vertices:,}\n"
                                  f"Faces: {imported.total_faces:,}"
                                  f"{pair_note}"
                                  f"{pair_warning}\n\n"
                                  f"A backup will be created automatically."):
                return

            # Repack using the existing engine
            from core.repack_engine import RepackEngine, ModifiedFile
            game_path = os.path.dirname(os.path.dirname(entry.paz_file))
            papgt_path = os.path.join(game_path, "meta", "0.papgt")

            mod_file = ModifiedFile(
                data=new_data,
                entry=entry,
                pamt_data=pamt_data,
                package_group=paz_dir,
            )

            engine = RepackEngine(game_path)
            result = engine.repack(
                [mod_file, *extra_mod_files], papgt_path=papgt_path,
                create_backup=True, verify_after=True,
            )

            if result.success:
                basename = os.path.basename(entry.path)
                temp_path = os.path.join(self._temp_dir, basename)
                with open(temp_path, "wb") as f:
                    f.write(new_data)
                self._preview.preview_file(
                    temp_path,
                    vfs=self._vfs,
                    vfs_path=entry.path.replace("\\", "/"),
                )
                self._progress.set_status(
                    f"Patched {entry.path}: {imported.total_vertices:,} verts, "
                    f"{imported.total_faces:,} faces"
                )
                # Invalidate the PAMT cache for the affected group
                # so any subsequent read from this tab sees the
                # refreshed file-entry offsets. The main window's
                # periodic staleness poll will also notice the
                # 0.pamt mtime change and show the reload badge.
                try:
                    self._vfs.invalidate_pamt_cache(paz_dir)
                except Exception:
                    pass
                show_info(self, "Patch Complete",
                          f"Successfully patched {entry.path}\n\n"
                          f"Vertices: {imported.total_vertices:,}\n"
                          f"Faces: {imported.total_faces:,}\n"
                          f"Size: {len(new_data):,} bytes"
                          f"{pair_note}"
                          f"{pair_warning}\n\n"
                          f"Launch the game to see your changes!")
            else:
                err_text = "; ".join(result.errors) if result.errors else "Unknown repack failure."
                show_error(self, "Patch Error", f"Repack failed: {err_text}")

        except Exception as e:
            self._progress.set_status(f"Patch error: {e}")
            logger.error("Mesh patch error for %s: %s", entry.path, e)
            show_error(self, "Patch Error", str(e))

    def _build_pac_to_folder(self, entry: PamtFileEntry, fmt: str = "obj"):
        """Convert an OBJ or FBX into a .pac / .pam / .pamlod file on disk
        without touching the live game archives.

        Workflow
        --------
        1. Prompt for the OBJ to import.
        2. Prompt for an output folder.
        3. Source donor vertex data from the baseline manager
           (snapshotting the current live bytes on first call so
           subsequent builds are idempotent).
        4. Run the OBJ→binary rebuild.
        5. Write the rebuilt file to ``<folder>/<basename>`` and
           open the containing folder in Explorer.

        Why this exists
        ---------------
        Mesh editing is trial-and-error: a user often iterates on
        the OBJ several times before they're happy with the
        result. Patching into game archives each iteration is slow
        (the PAZ files are ~870 MB post-April-2026 patch and Bob
        Jenkins Lookup3 is O(n)), risks the double-patch corruption
        bug, and requires a Steam verify to recover from mistakes.

        This action gives the user a safe, fast loop: build PAC,
        inspect it (preview, open in external tool, diff), iterate,
        THEN hit "Import OBJ + Patch to Game" once at the end.
        """
        from ui.dialogs.file_picker import pick_directory

        if fmt == "fbx":
            mesh_path = pick_file(self, "Select FBX File", filters="FBX Files (*.fbx);;All Files (*.*)")
        else:
            mesh_path = pick_file(self, "Select OBJ File", filters="OBJ Files (*.obj);;All Files (*.*)")
        if not mesh_path:
            return
        obj_path = mesh_path  # kept for downstream references

        out_dir = pick_directory(self, "Select output folder for rebuilt PAC")
        if not out_dir:
            return

        try:
            self._progress.set_status(
                f"Building {os.path.basename(entry.path)} from "
                f"{os.path.basename(mesh_path)}..."
            )
            if fmt == "fbx":
                from core.mesh_importer import import_fbx, build_mesh
                imported = import_fbx(mesh_path)
            else:
                from core.mesh_importer import import_obj, build_mesh
                imported = import_obj(mesh_path)
            if not imported.submeshes:
                show_error(self, "Import Error", "No geometry found in OBJ file.")
                return

            # Baseline-backed donor source (idempotent).
            original_data = self._mesh_baseline.get_or_snapshot(
                entry.path,
                live_read=lambda: self._vfs.read_entry_data(entry),
                source_paz=os.path.basename(entry.paz_file or ""),
            )

            imported.path = entry.path
            ext = os.path.splitext(entry.path.lower())[1]
            imported.format = (
                "pac" if ext == ".pac"
                else "pamlod" if ext == ".pamlod"
                else "pam"
            )

            # Strict skin write-back sidecar — see the corresponding
            # comment block in ``_import_mesh``. Without this, FBX
            # files exported by Blender's native exporter (no
            # ``.cfmeta.json`` companion) silently preserve donor
            # weights instead of applying the user's vertex paint.
            if fmt == "fbx":
                from core.mesh_importer import build_skin_writeback_sidecar
                imported._cfmeta_sidecar = build_skin_writeback_sidecar(
                    original_data, vfs=self._vfs, pac_path=entry.path,
                )

            new_data = build_mesh(imported, original_data)

            basename = os.path.basename(entry.path)
            out_path = os.path.join(out_dir, basename)
            with open(out_path, "wb") as f:
                f.write(new_data)

            # Stash as pending so the user can choose to patch
            # later without re-running the build.
            self._pending_mesh_data[entry.path.lower()] = {
                "entry": entry,
                "new_data": new_data,
                "imported": imported,
                "obj_path": obj_path,
            }

            self._progress.set_status(
                f"Built {basename}: {imported.total_vertices:,} verts, "
                f"{imported.total_faces:,} faces"
            )
            show_info(
                self, "Build Complete",
                f"Wrote {out_path}\n\n"
                f"Vertices: {imported.total_vertices:,}\n"
                f"Faces: {imported.total_faces:,}\n"
                f"Submeshes: {len(imported.submeshes)}\n"
                f"Size: {len(new_data):,} bytes\n\n"
                f"Game archives were NOT modified. Use "
                f"'Import OBJ + Patch to Game' when ready to apply.",
            )

            # Reveal in Explorer (Windows — best-effort, failure
            # is not fatal).
            try:
                import subprocess
                subprocess.Popen(["explorer.exe", "/select,", out_path])
            except Exception:
                pass

        except Exception as e:
            self._progress.set_status(f"Build error: {e}")
            logger.error("Build PAC error for %s: %s", entry.path, e)
            show_error(self, "Build Error", str(e))

    def _restore_from_baseline(self, entry: PamtFileEntry):
        """Patch the baseline (pristine) bytes back into the live
        archive. One-click undo without Steam's Verify Integrity.

        Only works if a baseline has been captured for this PAC —
        i.e. the user has already run one of the import/build
        actions on it. If no baseline exists, we tell the user to
        run Steam Verify instead.
        """
        from core.repack_engine import RepackEngine, ModifiedFile

        meta = self._mesh_baseline.get_meta(entry.path)
        if meta is None:
            show_info(
                self, "No Baseline",
                f"No baseline snapshot exists for {entry.path}.\n\n"
                "A baseline is captured the first time you run 'Import OBJ' "
                "or 'Import OBJ + Patch to Game'. Since none was taken for "
                "this file, the only way to restore is:\n\n"
                "  Steam → right-click Crimson Desert → Properties → "
                "Installed Files → Verify integrity of game files",
            )
            return

        baseline_bytes = self._mesh_baseline.get_bytes(entry.path, verify=True)
        if baseline_bytes is None:
            show_error(
                self, "Baseline Corrupted",
                f"The baseline snapshot for {entry.path} failed its "
                "integrity check and cannot be used. Delete the baseline "
                "(Tools → Clear Mesh Baselines), run Steam Verify to "
                "restore the live archive, then re-import to capture a "
                "fresh baseline.",
            )
            return

        if not confirm_action(
            self, "Restore from Baseline",
            f"Restore {entry.path} to its pristine baseline?\n\n"
            f"Baseline size: {meta.byte_size:,} bytes\n"
            f"Captured: {time.strftime('%Y-%m-%d %H:%M', time.localtime(meta.snapshot_unix))}\n"
            f"Source PAZ: {meta.source_paz}\n\n"
            f"A backup of the current bytes will be created first.",
        ):
            return

        try:
            self._progress.set_status(f"Restoring {entry.path} from baseline...")
            paz_dir = os.path.basename(os.path.dirname(entry.paz_file))
            pamt_data = self._vfs.load_pamt(paz_dir)
            game_path = os.path.dirname(os.path.dirname(entry.paz_file))
            papgt_path = os.path.join(game_path, "meta", "0.papgt")

            mod_file = ModifiedFile(
                data=baseline_bytes,
                entry=entry,
                pamt_data=pamt_data,
                package_group=paz_dir,
            )
            engine = RepackEngine(game_path)
            result = engine.repack(
                [mod_file], papgt_path=papgt_path,
                create_backup=True, verify_after=True,
            )
            if result.success:
                self._progress.set_status(
                    f"Restored {entry.path} from baseline "
                    f"({meta.byte_size:,} bytes)."
                )
                show_info(
                    self, "Restored",
                    f"{entry.path} is back to its original state.\n\n"
                    f"Launch the game to verify.",
                )
            else:
                err = "; ".join(result.errors) if result.errors else "Unknown failure."
                show_error(self, "Restore Error", f"Repack failed: {err}")

        except Exception as e:
            self._progress.set_status(f"Restore error: {e}")
            logger.error("Restore-from-baseline error for %s: %s", entry.path, e)
            show_error(self, "Restore Error", str(e))

    def _open_archive_in_editor(self, entry: PamtFileEntry):
        try:
            data = self._vfs.read_entry_data(entry)
            basename = os.path.basename(entry.path)
            temp_path = os.path.join(self._temp_dir, basename)
            with open(temp_path, "wb") as f:
                f.write(data)
            self._open_in_editor(temp_path)
        except Exception as e:
            show_error(self, "Open Error", f"Failed to read {entry.path}: {e}")

    def _get_checked_entries(self) -> list[PamtFileEntry]:
        return self._model.get_checked_entries()

    def _extract_selected(self):
        entries = self._get_checked_entries()
        if not entries:
            show_error(self, "Error", "No files selected for extraction.")
            return
        self._do_extract(entries)

    def _extract_all(self):
        entries = self._get_checked_entries()
        if not entries:
            show_error(self, "Error", "No files to extract.")
            return
        self._do_extract(entries)

    def _do_extract(self, entries: list[PamtFileEntry]):
        output = self._output_path.text().strip()
        if not output:
            show_error(self, "Error", "Select an output directory first.")
            return
        os.makedirs(output, exist_ok=True)
        self._config.set("general.last_output_path", output)
        self._config.save()
        self._extract_sel_btn.setEnabled(False)
        self._extract_all_btn.setEnabled(False)

        def extract_work(worker, _entries=entries, _output=output):
            total = len(_entries)
            results = {"extracted": 0, "errors": 0, "decrypted": 0, "decompressed": 0}
            for i, entry in enumerate(_entries):
                if worker.is_cancelled():
                    break
                try:
                    result = self._vfs.extract_entry(entry, _output)
                    results["extracted"] += 1
                    if result.get("decrypted"):
                        results["decrypted"] += 1
                    if result.get("decompressed"):
                        results["decompressed"] += 1
                except Exception as e:
                    results["errors"] += 1
                    logger.error("Extract error: %s - %s", entry.path, e)
                pct = int(((i + 1) / total) * 100)
                worker.report_progress(pct, f"Extracting {os.path.basename(entry.path)}...")
            return results

        self._worker = FunctionWorker(extract_work)
        self._worker.progress.connect(lambda p, m: self._progress.set_progress(p, m))
        self._worker.finished_result.connect(self._on_extract_done)
        self._worker.error_occurred.connect(lambda e: show_error(self, "Extract Error", e))
        self._worker.start()

    def _on_extract_done(self, results):
        self._extract_sel_btn.setEnabled(True)
        self._extract_all_btn.setEnabled(True)
        msg = f"Extracted {results['extracted']} files"
        if results["decrypted"]:
            msg += f", {results['decrypted']} decrypted"
        if results["decompressed"]:
            msg += f", {results['decompressed']} decompressed"
        if results["errors"]:
            msg += f", {results['errors']} errors"
        self._progress.set_progress(100, msg)
        show_info(self, "Extraction Complete", msg)
        output = self._output_path.text().strip()
        self.files_extracted.emit(output)

    def _open_in_editor(self, path: str):
        if self._current_edit_file and self._editor.modified:
            if self._current_edit_file != path:
                if not confirm_action(self, "Unsaved Changes",
                                      f"'{os.path.basename(self._current_edit_file)}' has unsaved changes. Discard?"):
                    return
        if path == self._current_edit_file and not self._editor.modified:
            return
        try:
            encoding = self._encoding_combo.currentText()
            self._editor.load_file(path, encoding)
            self._current_edit_file = path
            self._edit_file_label.setText(f"Editor: {os.path.basename(path)}")
            syntax = get_syntax_type(path)
            self._syntax_combo.setCurrentText(syntax)
            self._editor.set_syntax(syntax)
            self._editor.modified = False
            self._edit_status.setText(f"Opened: {os.path.basename(path)}")
        except Exception as e:
            show_error(self, "Open Error", f"Failed to open {path}: {e}")

    def _save_file(self):
        if not self._current_edit_file:
            self._save_as()
            return
        try:
            encoding = self._encoding_combo.currentText()
            self._editor.save_file(self._current_edit_file, encoding)
            self._edit_status.setText(f"Saved: {os.path.basename(self._current_edit_file)}")
        except Exception as e:
            show_error(self, "Save Error", f"Failed to save: {e}")

    def _save_as(self):
        path = pick_save_file(self, "Save As", self._current_edit_file or "")
        if path:
            try:
                encoding = self._encoding_combo.currentText()
                self._editor.save_file(path, encoding)
                self._current_edit_file = path
                self._edit_file_label.setText(f"Editor: {os.path.basename(path)}")
                self._edit_status.setText(f"Saved: {os.path.basename(path)}")
            except Exception as e:
                show_error(self, "Save Error", f"Failed to save: {e}")

    def set_root_path(self, path: str):
        self._output_path.setText(path)

    def _get_selected_mesh_entries(self) -> list[PamtFileEntry]:
        from core.mesh_parser import is_mesh_file

        result = []
        seen = set()
        for row in self._get_selected_rows():
            row_data = self._model.row_at(row)
            if not row_data or not is_mesh_file(row_data.entry.path):
                continue
            key = row_data.entry.path.lower()
            if key in seen:
                continue
            seen.add(key)
            result.append(row_data.entry)
        return result

    def _ship_selected_meshes(self):
        if not self._vfs:
            show_error(self, "Ship to App", "Load the game data first.")
            return

        entries = self._get_selected_mesh_entries()
        if not entries:
            show_error(
                self,
                "Ship to App",
                "Select one or more .pac, .pam, or .pamlod rows in Explorer first.",
            )
            return

        prefilled = {}
        for entry in entries:
            pending = self._pending_mesh_data.get(entry.path.lower())
            if pending and pending.get("obj_path"):
                prefilled[entry.path.lower()] = pending["obj_path"]

        from ui.dialogs.ship_mesh_dialog import ShipMeshDialog

        dlg = ShipMeshDialog(self._vfs, self._config, entries, prefilled, self._item_index, self)
        dlg.exec()

    def _ship_single_mesh(self, entry: PamtFileEntry, fmt: str = "obj"):
        if not self._vfs:
            show_error(self, "Ship to App", "Load the game data first.")
            return

        if fmt == "fbx":
            mesh_path = pick_file(self, "Select FBX File", filters="FBX Files (*.fbx);;All Files (*.*)")
        else:
            mesh_path = pick_file(self, "Select OBJ File", filters="OBJ Files (*.obj);;All Files (*.*)")
        if not mesh_path:
            return

        from ui.dialogs.ship_mesh_dialog import ShipMeshDialog

        dlg = ShipMeshDialog(
            self._vfs,
            self._config,
            [entry],
            {entry.path.lower(): mesh_path},
            self._item_index,
            self,
        )
        dlg.exec()
