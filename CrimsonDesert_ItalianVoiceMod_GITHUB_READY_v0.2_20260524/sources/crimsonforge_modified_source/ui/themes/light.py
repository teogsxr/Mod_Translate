"""Light theme stylesheet for PySide6 - Catppuccin Latte inspired."""

LIGHT_THEME = """
/* ========== WINDOW & BASE ========== */
QMainWindow, QDialog {
    background-color: #eff1f5;
    color: #4c4f69;
    font-family: "Segoe UI", "Microsoft YaHei", "Malgun Gothic", "Meiryo", "Noto Sans", sans-serif;
    font-size: 13px;
}
QWidget {
    font-family: "Segoe UI", "Microsoft YaHei", "Malgun Gothic", "Meiryo", "Noto Sans", sans-serif;
}
QWidget#settingsTab,
QWidget#settingsPage,
QWidget#settingsScrollViewport,
QWidget#settingsScrollContent,
QStackedWidget#settingsStack {
    background-color: #eff1f5;
}

/* ========== TABS ========== */
QTabWidget::pane {
    border: 1px solid #dce0e8;
    background-color: #eff1f5;
    border-radius: 0 0 6px 6px;
}
QTabBar::tab {
    background-color: #e6e9ef;
    color: #6c6f85;
    padding: 10px 20px;
    border: none;
    border-bottom: 2px solid transparent;
    margin-right: 1px;
    font-weight: 500;
    font-size: 13px;
}
QTabBar::tab:selected {
    background-color: #eff1f5;
    color: #1e66f5;
    border-bottom: 2px solid #1e66f5;
    font-weight: 600;
}
QTabBar::tab:hover:!selected {
    background-color: #dce0e8;
    color: #4c4f69;
}
QTabBar::tab:disabled {
    color: #bcc0cc;
}

/* ========== BUTTONS ========== */
QPushButton {
    background-color: #e6e9ef;
    color: #4c4f69;
    border: 1px solid #ccd0da;
    padding: 7px 18px;
    border-radius: 6px;
    min-height: 26px;
    font-weight: 500;
    font-size: 12px;
}
QPushButton:hover {
    background-color: #dce0e8;
    border-color: #bcc0cc;
    color: #1e1e2e;
}
QPushButton:pressed {
    background-color: #ccd0da;
}
QPushButton:disabled {
    background-color: #eff1f5;
    color: #bcc0cc;
    border-color: #dce0e8;
}
QPushButton:focus {
    border-color: #1e66f5;
    outline: none;
}
QPushButton#primary {
    background-color: #1e66f5;
    color: #ffffff;
    border: none;
    font-weight: 700;
    padding: 7px 22px;
}
QPushButton#primary:hover {
    background-color: #4080f7;
}
QPushButton#primary:pressed {
    background-color: #1554d4;
}
QPushButton#primary:disabled {
    background-color: #bcc0cc;
    color: #9ca0b0;
}
QPushButton#danger {
    background-color: #d20f39;
    color: #ffffff;
    border: none;
    font-weight: 700;
}
QPushButton#danger:hover {
    background-color: #e03050;
}
QPushButton#success {
    background-color: #40a02b;
    color: #ffffff;
    border: none;
    font-weight: 700;
}
QPushButton#success:hover {
    background-color: #50b03b;
}
QPushButton#warning {
    background-color: #df8e1d;
    color: #ffffff;
    border: none;
    font-weight: 700;
}
QPushButton#warning:hover {
    background-color: #e9a030;
}

/* ========== TOOL BUTTONS ========== */
QToolButton {
    background-color: #e6e9ef;
    color: #4c4f69;
    border: 1px solid #ccd0da;
    padding: 4px 8px;
    border-radius: 5px;
    font-size: 11px;
    font-weight: 500;
}
QToolButton:hover {
    background-color: #dce0e8;
    border-color: #bcc0cc;
}
QToolButton:checked {
    background-color: #1e66f5;
    color: #ffffff;
    border-color: #1e66f5;
}
QToolButton:pressed {
    background-color: #ccd0da;
}

/* ========== TEXT INPUTS ========== */
QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox {
    background-color: #ffffff;
    color: #4c4f69;
    border: 1px solid #ccd0da;
    padding: 6px 10px;
    border-radius: 6px;
    selection-background-color: #1e66f5;
    selection-color: #ffffff;
    font-size: 13px;
}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QSpinBox:focus {
    border-color: #1e66f5;
}
QLineEdit:hover, QTextEdit:hover, QPlainTextEdit:hover {
    border-color: #bcc0cc;
}

/* ========== COMBOBOX ========== */
QComboBox {
    background-color: #ffffff;
    color: #4c4f69;
    border: 1px solid #ccd0da;
    padding: 6px 10px;
    border-radius: 6px;
    min-height: 26px;
    font-size: 13px;
}
QComboBox:hover {
    border-color: #bcc0cc;
}
QComboBox:focus {
    border-color: #1e66f5;
}
QComboBox::drop-down {
    border: none;
    width: 28px;
    subcontrol-position: center right;
    padding-right: 6px;
}
QComboBox QAbstractItemView {
    background-color: #ffffff;
    color: #4c4f69;
    border: 1px solid #ccd0da;
    border-radius: 6px;
    selection-background-color: #1e66f5;
    selection-color: #ffffff;
    padding: 4px;
    outline: none;
}
QComboBox QAbstractItemView::item {
    color: #4c4f69;
    background-color: transparent;
    padding: 5px 10px;
    border-radius: 4px;
    min-height: 22px;
}
QComboBox QAbstractItemView::item:hover {
    color: #1e1e2e;
    background-color: #e6e9ef;
}
QComboBox QAbstractItemView::item:selected {
    color: #ffffff;
    background-color: #1e66f5;
}

/* ========== TABLE / TREE / LIST ========== */
QTreeView, QListView, QTableView {
    background-color: #ffffff;
    color: #4c4f69;
    border: 1px solid #dce0e8;
    border-radius: 6px;
    alternate-background-color: #eff1f5;
    selection-background-color: #e6e9ef;
    selection-color: #4c4f69;
    gridline-color: #eff1f5;
    outline: none;
    font-size: 13px;
}
QTreeView::item, QListView::item, QTableView::item {
    padding: 3px 6px;
    border: none;
}
QTreeView::item:hover, QListView::item:hover, QTableView::item:hover {
    background-color: #eff1f5;
}
QTreeView::item:selected, QListView::item:selected, QTableView::item:selected {
    background-color: #e6e9ef;
}
QHeaderView::section {
    background-color: #e6e9ef;
    color: #6c6f85;
    padding: 6px 10px;
    border: none;
    border-right: 1px solid #eff1f5;
    border-bottom: 1px solid #dce0e8;
    font-weight: 600;
    font-size: 12px;
}
QHeaderView::section:hover {
    background-color: #dce0e8;
    color: #4c4f69;
}

/* ========== PROGRESS BAR ========== */
QProgressBar {
    background-color: #e6e9ef;
    border: 1px solid #dce0e8;
    border-radius: 8px;
    text-align: center;
    color: #4c4f69;
    min-height: 20px;
    font-size: 11px;
    font-weight: 600;
}
QProgressBar::chunk {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #209fb5, stop:1 #1e66f5);
    border-radius: 7px;
}

/* ========== SLIDER ========== */
QSlider::groove:horizontal {
    background-color: #dce0e8;
    height: 6px;
    border-radius: 3px;
}
QSlider::handle:horizontal {
    background-color: #1e66f5;
    width: 14px;
    height: 14px;
    margin: -4px 0;
    border-radius: 7px;
    border: 2px solid #ffffff;
}
QSlider::handle:horizontal:hover {
    background-color: #4080f7;
    width: 16px;
    height: 16px;
    margin: -5px 0;
    border-radius: 8px;
}
QSlider::sub-page:horizontal {
    background-color: #1e66f5;
    border-radius: 3px;
}

/* ========== STATUS BAR ========== */
QStatusBar {
    background-color: #e6e9ef;
    color: #6c6f85;
    border-top: 1px solid #dce0e8;
    padding: 2px 8px;
    font-size: 12px;
}
QStatusBar::item {
    border: none;
}

/* ========== MENU ========== */
QMenuBar {
    background-color: #e6e9ef;
    color: #4c4f69;
    border-bottom: 1px solid #dce0e8;
    padding: 2px;
}
QMenuBar::item {
    padding: 6px 12px;
    border-radius: 4px;
}
QMenuBar::item:selected {
    background-color: #dce0e8;
}
QMenu {
    background-color: #ffffff;
    color: #4c4f69;
    border: 1px solid #ccd0da;
    border-radius: 8px;
    padding: 6px;
}
QMenu::item {
    padding: 7px 24px 7px 16px;
    border-radius: 4px;
    font-size: 12px;
}
QMenu::item:selected {
    background-color: #1e66f5;
    color: #ffffff;
}
QMenu::item:disabled {
    color: #bcc0cc;
}
QMenu::separator {
    height: 1px;
    background-color: #dce0e8;
    margin: 4px 8px;
}

/* ========== SPLITTER ========== */
QSplitter::handle {
    background-color: #dce0e8;
    width: 2px;
    height: 2px;
}
QSplitter::handle:hover {
    background-color: #1e66f5;
}

/* ========== SCROLLBAR ========== */
QScrollBar:vertical {
    background-color: transparent;
    width: 10px;
}
QScrollBar::handle:vertical {
    background-color: #ccd0da;
    border-radius: 5px;
    min-height: 30px;
    margin: 2px;
}
QScrollBar::handle:vertical:hover {
    background-color: #bcc0cc;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    background: transparent;
}
QScrollBar:horizontal {
    background-color: transparent;
    height: 10px;
}
QScrollBar::handle:horizontal {
    background-color: #ccd0da;
    border-radius: 5px;
    min-width: 30px;
    margin: 2px;
}
QScrollBar::handle:horizontal:hover {
    background-color: #bcc0cc;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0;
}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {
    background: transparent;
}

/* ========== CHECKBOX ========== */
QCheckBox {
    color: #4c4f69;
    spacing: 8px;
    font-size: 13px;
}
QCheckBox::indicator {
    width: 18px;
    height: 18px;
    border: 2px solid #ccd0da;
    border-radius: 4px;
    background-color: #ffffff;
}
QCheckBox::indicator:hover {
    border-color: #1e66f5;
}
QCheckBox::indicator:checked {
    background-color: #1e66f5;
    border-color: #1e66f5;
}

/* ========== LABELS ========== */
QLabel {
    color: #4c4f69;
}

/* ========== GROUP BOX ========== */
QGroupBox {
    color: #6c6f85;
    border: 1px solid #dce0e8;
    border-radius: 8px;
    margin-top: 12px;
    padding: 20px 12px 12px 12px;
    font-weight: 600;
    font-size: 12px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 2px 10px;
    color: #1e66f5;
}

/* ========== TOOLTIP ========== */
QToolTip {
    background-color: #ffffff;
    color: #4c4f69;
    border: 1px solid #ccd0da;
    border-radius: 6px;
    padding: 6px 10px;
    font-size: 12px;
}

/* ========== TEXT BROWSER ========== */
QTextBrowser {
    background-color: #ffffff;
    color: #4c4f69;
    border: 1px solid #dce0e8;
    border-radius: 6px;
    padding: 8px;
    selection-background-color: #1e66f5;
    selection-color: #ffffff;
}

/* ========== SCROLL AREA ========== */
QScrollArea {
    border: none;
    background-color: transparent;
}

/* ========== DISABLED TAB ========== */
QTabBar::tab:disabled {
    color: #bcc0cc;
    background-color: #e6e9ef;
}
"""
