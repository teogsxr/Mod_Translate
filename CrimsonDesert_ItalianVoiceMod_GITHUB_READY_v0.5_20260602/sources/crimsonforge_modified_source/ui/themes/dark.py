"""Dark theme stylesheet for PySide6 - Catppuccin Mocha inspired."""

DARK_THEME = """
/* ========== WINDOW & BASE ========== */
QMainWindow, QDialog {
    background-color: #1e1e2e;
    color: #cdd6f4;
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
    background-color: #1e1e2e;
}

/* ========== TABS ========== */
QTabWidget::pane {
    border: 1px solid #313244;
    background-color: #1e1e2e;
    border-radius: 0 0 6px 6px;
}
QTabBar::tab {
    background-color: #181825;
    color: #a6adc8;
    padding: 10px 20px;
    border: none;
    border-bottom: 2px solid transparent;
    margin-right: 1px;
    font-weight: 500;
    font-size: 13px;
}
QTabBar::tab:selected {
    background-color: #1e1e2e;
    color: #89b4fa;
    border-bottom: 2px solid #89b4fa;
    font-weight: 600;
}
QTabBar::tab:hover:!selected {
    background-color: #313244;
    color: #cdd6f4;
}
QTabBar::tab:disabled {
    color: #45475a;
}

/* ========== BUTTONS ========== */
QPushButton {
    background-color: #313244;
    color: #cdd6f4;
    border: 1px solid #45475a;
    padding: 7px 18px;
    border-radius: 6px;
    min-height: 26px;
    font-weight: 500;
    font-size: 12px;
}
QPushButton:hover {
    background-color: #45475a;
    border-color: #585b70;
    color: #ffffff;
}
QPushButton:pressed {
    background-color: #585b70;
    border-color: #6c7086;
}
QPushButton:disabled {
    background-color: #262637;
    color: #45475a;
    border-color: #313244;
}
QPushButton:focus {
    border-color: #89b4fa;
    outline: none;
}

/* Primary action buttons */
QPushButton#primary {
    background-color: #89b4fa;
    color: #1e1e2e;
    border: none;
    font-weight: 700;
    padding: 7px 22px;
}
QPushButton#primary:hover {
    background-color: #a6c8fc;
}
QPushButton#primary:pressed {
    background-color: #74a8f9;
}
QPushButton#primary:disabled {
    background-color: #45475a;
    color: #6c7086;
}

/* Danger buttons */
QPushButton#danger {
    background-color: #f38ba8;
    color: #1e1e2e;
    border: none;
    font-weight: 700;
}
QPushButton#danger:hover {
    background-color: #f5a0b8;
}
QPushButton#danger:pressed {
    background-color: #e87898;
}

/* Success buttons */
QPushButton#success {
    background-color: #a6e3a1;
    color: #1e1e2e;
    border: none;
    font-weight: 700;
}
QPushButton#success:hover {
    background-color: #b8eab4;
}

/* Warning buttons */
QPushButton#warning {
    background-color: #f9e2af;
    color: #1e1e2e;
    border: none;
    font-weight: 700;
}
QPushButton#warning:hover {
    background-color: #fae8c0;
}

/* ========== TOOL BUTTONS ========== */
QToolButton {
    background-color: #313244;
    color: #cdd6f4;
    border: 1px solid #45475a;
    padding: 4px 8px;
    border-radius: 5px;
    font-size: 11px;
    font-weight: 500;
}
QToolButton:hover {
    background-color: #45475a;
    border-color: #585b70;
}
QToolButton:checked {
    background-color: #89b4fa;
    color: #1e1e2e;
    border-color: #89b4fa;
}
QToolButton:pressed {
    background-color: #585b70;
}

/* ========== TEXT INPUTS ========== */
QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox {
    background-color: #181825;
    color: #cdd6f4;
    border: 1px solid #313244;
    padding: 6px 10px;
    border-radius: 6px;
    selection-background-color: #89b4fa;
    selection-color: #1e1e2e;
    font-size: 13px;
}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QSpinBox:focus {
    border-color: #89b4fa;
    background-color: #1e1e2e;
}
QLineEdit:hover, QTextEdit:hover, QPlainTextEdit:hover {
    border-color: #45475a;
}
QLineEdit:disabled {
    background-color: #181825;
    color: #45475a;
    border-color: #262637;
}
QLineEdit::placeholder {
    color: #6c7086;
}

/* ========== COMBOBOX ========== */
QComboBox {
    background-color: #313244;
    color: #cdd6f4;
    border: 1px solid #45475a;
    padding: 6px 10px;
    border-radius: 6px;
    min-height: 26px;
    font-size: 13px;
}
QComboBox:hover {
    border-color: #585b70;
    background-color: #3b3c52;
}
QComboBox:focus {
    border-color: #89b4fa;
}
QComboBox::drop-down {
    border: none;
    width: 28px;
    subcontrol-position: center right;
    padding-right: 6px;
}
QComboBox QAbstractItemView {
    background-color: #313244;
    color: #cdd6f4;
    border: 1px solid #45475a;
    border-radius: 6px;
    selection-background-color: #89b4fa;
    selection-color: #1e1e2e;
    padding: 4px;
    outline: none;
}
QComboBox QAbstractItemView::item {
    color: #cdd6f4;
    background-color: transparent;
    padding: 5px 10px;
    border-radius: 4px;
    min-height: 22px;
}
QComboBox QAbstractItemView::item:hover {
    color: #cdd6f4;
    background-color: #45475a;
}
QComboBox QAbstractItemView::item:selected {
    color: #1e1e2e;
    background-color: #89b4fa;
}

/* ========== TABLE / TREE / LIST ========== */
QTreeView, QListView, QTableView {
    background-color: #1e1e2e;
    color: #cdd6f4;
    border: 1px solid #313244;
    border-radius: 6px;
    alternate-background-color: #181825;
    selection-background-color: #313244;
    selection-color: #cdd6f4;
    gridline-color: #262637;
    outline: none;
    font-size: 13px;
}
QTreeView::item, QListView::item, QTableView::item {
    padding: 3px 6px;
    border: none;
}
QTreeView::item:hover, QListView::item:hover, QTableView::item:hover {
    background-color: #262637;
}
QTreeView::item:selected, QListView::item:selected, QTableView::item:selected {
    background-color: #313244;
    color: #cdd6f4;
}
QHeaderView::section {
    background-color: #181825;
    color: #a6adc8;
    padding: 6px 10px;
    border: none;
    border-right: 1px solid #262637;
    border-bottom: 1px solid #313244;
    font-weight: 600;
    font-size: 12px;
}
QHeaderView::section:hover {
    background-color: #313244;
    color: #cdd6f4;
}

/* ========== PROGRESS BAR ========== */
QProgressBar {
    background-color: #181825;
    border: 1px solid #313244;
    border-radius: 8px;
    text-align: center;
    color: #cdd6f4;
    min-height: 20px;
    font-size: 11px;
    font-weight: 600;
}
QProgressBar::chunk {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #74c7ec, stop:1 #89b4fa);
    border-radius: 7px;
}

/* ========== SLIDER ========== */
QSlider::groove:horizontal {
    background-color: #313244;
    height: 6px;
    border-radius: 3px;
}
QSlider::handle:horizontal {
    background-color: #89b4fa;
    width: 14px;
    height: 14px;
    margin: -4px 0;
    border-radius: 7px;
    border: 2px solid #1e1e2e;
}
QSlider::handle:horizontal:hover {
    background-color: #a6c8fc;
    width: 16px;
    height: 16px;
    margin: -5px 0;
    border-radius: 8px;
}
QSlider::sub-page:horizontal {
    background-color: #89b4fa;
    border-radius: 3px;
}

/* ========== STATUS BAR ========== */
QStatusBar {
    background-color: #181825;
    color: #a6adc8;
    border-top: 1px solid #313244;
    padding: 2px 8px;
    font-size: 12px;
}
QStatusBar::item {
    border: none;
}

/* ========== MENU BAR ========== */
QMenuBar {
    background-color: #181825;
    color: #cdd6f4;
    border-bottom: 1px solid #313244;
    padding: 2px;
}
QMenuBar::item {
    padding: 6px 12px;
    border-radius: 4px;
}
QMenuBar::item:selected {
    background-color: #313244;
}

/* ========== CONTEXT MENU ========== */
QMenu {
    background-color: #313244;
    color: #cdd6f4;
    border: 1px solid #45475a;
    border-radius: 8px;
    padding: 6px;
}
QMenu::item {
    padding: 7px 24px 7px 16px;
    border-radius: 4px;
    font-size: 12px;
}
QMenu::item:selected {
    background-color: #89b4fa;
    color: #1e1e2e;
}
QMenu::item:disabled {
    color: #585b70;
}
QMenu::separator {
    height: 1px;
    background-color: #45475a;
    margin: 4px 8px;
}

/* ========== SPLITTER ========== */
QSplitter::handle {
    background-color: #313244;
    width: 2px;
    height: 2px;
}
QSplitter::handle:hover {
    background-color: #89b4fa;
}

/* ========== SCROLLBAR ========== */
QScrollBar:vertical {
    background-color: transparent;
    width: 10px;
    margin: 0;
}
QScrollBar::handle:vertical {
    background-color: #45475a;
    border-radius: 5px;
    min-height: 30px;
    margin: 2px;
}
QScrollBar::handle:vertical:hover {
    background-color: #585b70;
}
QScrollBar::handle:vertical:pressed {
    background-color: #6c7086;
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
    background-color: #45475a;
    border-radius: 5px;
    min-width: 30px;
    margin: 2px;
}
QScrollBar::handle:horizontal:hover {
    background-color: #585b70;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0;
}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {
    background: transparent;
}

/* ========== CHECKBOX ========== */
QCheckBox {
    color: #cdd6f4;
    spacing: 8px;
    font-size: 13px;
}
QCheckBox::indicator {
    width: 18px;
    height: 18px;
    border: 2px solid #45475a;
    border-radius: 4px;
    background-color: #181825;
}
QCheckBox::indicator:hover {
    border-color: #89b4fa;
}
QCheckBox::indicator:checked {
    background-color: #89b4fa;
    border-color: #89b4fa;
}

/* ========== LABELS ========== */
QLabel {
    color: #cdd6f4;
}

/* ========== GROUP BOX ========== */
QGroupBox {
    color: #a6adc8;
    border: 1px solid #313244;
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
    color: #89b4fa;
}

/* ========== TOOLTIP ========== */
QToolTip {
    background-color: #313244;
    color: #cdd6f4;
    border: 1px solid #45475a;
    border-radius: 6px;
    padding: 6px 10px;
    font-size: 12px;
}

/* ========== TEXT BROWSER (About, Changelog) ========== */
QTextBrowser {
    background-color: #181825;
    color: #cdd6f4;
    border: 1px solid #313244;
    border-radius: 6px;
    padding: 8px;
    selection-background-color: #89b4fa;
    selection-color: #1e1e2e;
}

/* ========== SCROLL AREA ========== */
QScrollArea {
    border: none;
    background-color: transparent;
}

/* ========== STACKED WIDGET ========== */
QStackedWidget {
    background-color: transparent;
}

/* ========== DIALOG BUTTONS ========== */
QDialogButtonBox QPushButton {
    min-width: 80px;
}

/* ========== DISABLED TAB BADGE ========== */
QTabBar::tab:disabled {
    color: #45475a;
    background-color: #181825;
}
"""
