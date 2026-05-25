"""About tab - version, changelog, license, credits, and game info.

Shows the current application version, a full color-coded changelog,
credits and acknowledgments, and links to project resources.
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTextBrowser,
    QTabWidget, QGroupBox, QScrollArea, QFrame,
)
from PySide6.QtCore import Qt

from version import APP_VERSION, APP_NAME, CHANGELOG, get_changelog_html


DESCRIPTION = "Crimson Desert Modding Studio"

# Injected into every QTextBrowser HTML so colors render correctly on Windows.
# QTextBrowser renders an internal QTextDocument; on Windows the QSS `color`
# property does NOT cascade into that document, so headings/body appear black.
_BROWSER_CSS = """
<style>
  body   { color: #cdd6f4; background-color: transparent; font-size: 13px; }
  h1, h2 { color: #89b4fa; margin-top: 18px; margin-bottom: 6px; }
  h3     { color: #cba6f7; margin-top: 14px; margin-bottom: 4px; }
  h4     { color: #a6adc8; margin-top: 10px; margin-bottom: 2px; }
  a      { color: #89b4fa; }
  b      { color: #f5f5f5; }
  hr     { border: none; border-top: 1px solid #45475a; margin: 12px 0; }
  li     { margin-bottom: 3px; }
  pre    { color: #cdd6f4; }
  td, th { color: #cdd6f4; }
</style>
"""

# Crimson Desert game info (March 2026)
GAME_INFO = {
    "name": "Crimson Desert",
    "developer": "Pearl Abyss",
    "release_date": "March 19, 2026",
    "platforms": "PC (Steam, Epic), PS5, Xbox Series X|S, macOS",
    "steam_app_id": "3321460",
}


class AboutTab(QWidget):
    """Enterprise About tab with version info, changelog, and credits."""

    def __init__(self, config=None, parent=None):
        super().__init__(parent)
        self._config = config
        self._game_version = self._detect_game_version()
        self._setup_ui()

    def _detect_game_version(self) -> str:
        """Read game version from meta/0.paver (3 x uint16 LE: major.minor.patch)."""
        try:
            import os, struct
            game_path = self._config.get("general.last_game_path", "") if self._config else ""
            if not game_path:
                return "Not loaded"
            paver_path = os.path.join(game_path, "meta", "0.paver")
            if not os.path.isfile(paver_path):
                return "Not found"
            with open(paver_path, "rb") as f:
                data = f.read()
            if len(data) < 6:
                return "Unknown"
            major, minor, patch = struct.unpack_from("<HHH", data, 0)
            return f"v{major}.{minor:02d}.{patch:02d}"
        except Exception:
            return "Unknown"

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)

        # Header
        header = QWidget()
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)

        title = QLabel(f"{APP_NAME} v{APP_VERSION}")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("font-size: 28px; font-weight: bold; padding: 12px 0 4px 0;")
        header_layout.addWidget(title)

        subtitle = QLabel(DESCRIPTION)
        subtitle.setAlignment(Qt.AlignCenter)
        subtitle.setStyleSheet("font-size: 15px; color: #a6adc8; padding: 2px;")
        header_layout.addWidget(subtitle)

        # Version badge row
        badge_row = QHBoxLayout()
        badge_row.setAlignment(Qt.AlignCenter)
        badge_row.setSpacing(12)

        app_badge = QLabel(f"App v{APP_VERSION}")
        app_badge.setStyleSheet(
            "background-color: #89b4fa; color: #1e1e2e; font-weight: bold; "
            "padding: 3px 12px; border-radius: 10px; font-size: 12px;"
        )
        badge_row.addWidget(app_badge)

        self._game_badge = QLabel(f"Game: {self._game_version}")
        self._game_badge.setStyleSheet(
            "background-color: #a6e3a1; color: #1e1e2e; font-weight: bold; "
            "padding: 3px 12px; border-radius: 10px; font-size: 12px;"
        )
        self._game_badge.setToolTip("Game version detected dynamically from PAPGT root index.")
        badge_row.addWidget(self._game_badge)

        total_changes = sum(len(c) for _, _, c in CHANGELOG)
        changes_badge = QLabel(f"{total_changes} changes logged")
        changes_badge.setStyleSheet(
            "background-color: #f9e2af; color: #1e1e2e; font-weight: bold; "
            "padding: 3px 12px; border-radius: 10px; font-size: 12px;"
        )
        badge_row.addWidget(changes_badge)

        header_layout.addLayout(badge_row)
        layout.addWidget(header)

        # Sub-tabs: Changelog | Game Info | Credits
        sub_tabs = QTabWidget()
        sub_tabs.addTab(self._build_changelog_tab(), "Changelog")
        sub_tabs.addTab(self._build_game_info_tab(), "Game Info")
        sub_tabs.addTab(self._build_credits_tab(), "Credits")
        sub_tabs.addTab(self._build_license_tab(), "License")
        layout.addWidget(sub_tabs, 1)

    def _build_changelog_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(8, 8, 8, 8)

        # Stats summary
        stats_row = QHBoxLayout()
        stats_row.setSpacing(16)

        total_versions = len(CHANGELOG)
        total_features = sum(
            1 for _, _, changes in CHANGELOG
            for c in changes if c.startswith("[Feature]")
        )
        total_fixes = sum(
            1 for _, _, changes in CHANGELOG
            for c in changes if c.startswith("[Fix]")
        )
        total_enhancements = sum(
            1 for _, _, changes in CHANGELOG
            for c in changes if c.startswith("[Enhancement]")
        )

        for label, value, color in [
            ("Versions", total_versions, "#cba6f7"),
            ("Features", total_features, "#a6e3a1"),
            ("Enhancements", total_enhancements, "#89b4fa"),
            ("Bug Fixes", total_fixes, "#f9e2af"),
        ]:
            stat = QLabel(f"<b style='color:{color};'>{value}</b> {label}")
            stat.setStyleSheet("font-size: 13px; padding: 4px 8px;")
            stats_row.addWidget(stat)
        stats_row.addStretch()
        layout.addLayout(stats_row)

        # Legend
        legend = QLabel(
            '<span style="color:#a6e3a1;">[Feature]</span> '
            '<span style="color:#89b4fa;">[Enhancement]</span> '
            '<span style="color:#f9e2af;">[Fix]</span> '
            '<span style="color:#f38ba8;">[Breaking]</span> '
            '<span style="color:#94e2d5;">[Performance]</span>'
        )
        legend.setStyleSheet("font-size: 11px; padding: 2px 8px; color: #6c7086;")
        layout.addWidget(legend)

        # Changelog browser
        browser = QTextBrowser()
        browser.setOpenExternalLinks(True)
        browser.setHtml(f"""
        {_BROWSER_CSS}
        <div style="padding: 8px;">
            {get_changelog_html()}
        </div>
        """)
        layout.addWidget(browser, 1)

        return widget

    def _build_game_info_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(8, 8, 8, 8)

        browser = QTextBrowser()
        browser.setOpenExternalLinks(True)
        browser.setHtml(f"""
        {_BROWSER_CSS}
        <div style="padding: 16px;">
            <h2>Crimson Desert</h2>
            <p><b>Developer:</b> {GAME_INFO['developer']}</p>
            <p><b>Release Date:</b> {GAME_INFO['release_date']}</p>
            <p><b>Platforms:</b> {GAME_INFO['platforms']}</p>
            <p><b>Detected Game Version:</b> {self._game_version}</p>
            <p><b>Steam App ID:</b>
                <a href="https://store.steampowered.com/app/{GAME_INFO['steam_app_id']}/">{GAME_INFO['steam_app_id']}</a>
            </p>

            <hr>

            <h3>Modding Notes</h3>
            <p>Crimson Desert uses a custom package archive system:</p>
            <ul>
                <li><b>PAPGT</b> - Root package group index with CRC chain</li>
                <li><b>PAMT</b> - Per-group metadata table mapping files to PAZ offsets</li>
                <li><b>PAZ</b> - Encrypted (ChaCha20) and compressed (LZ4) data archives</li>
                <li><b>PALOC</b> - Localization string files (key-value pairs)</li>
            </ul>
            <p>CrimsonForge handles the full pipeline: decrypt, decompress, parse, modify,
            recompress, re-encrypt, and update the entire checksum chain.</p>

            <hr>
            <p><i>Pearl Abyss / Crimson Desert are trademarks of Pearl Abyss.
            CrimsonForge is not affiliated with Pearl Abyss.</i></p>
        </div>
        """)
        layout.addWidget(browser, 1)
        return widget

    def _build_credits_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(8, 8, 8, 8)

        browser = QTextBrowser()
        browser.setOpenExternalLinks(True)
        browser.setHtml(f"""
        {_BROWSER_CSS}
        <div style="padding: 16px;">
            <h2>Credits &amp; Acknowledgments</h2>

            <h3>Author</h3>
            <p><b>hzeem</b>
            (<a href="https://github.com/hzeemr/crimsonforge">GitHub</a>
            | <a href="https://www.nexusmods.com/profile/hz33m">NexusMods</a>)
            - CrimsonForge developer and maintainer.</p>

            <hr>

            <h3>Research &amp; Reverse Engineering</h3>

            <p><b>Lazorr</b>
            (<a href="https://www.nexusmods.com/crimsondesert/mods/62">NexusMods</a>)
            - Original PAZ unpacker &amp; encryption key derivation algorithm research.
            Uploaded the foundational tools that made Crimson Desert modding possible.</p>

            <p><b>lazorr410</b>
            (<a href="https://github.com/lazorr410/crimson-desert-unpacker">GitHub</a>)
            - Python implementation of PAZ unpacker with ChaCha20 decryption,
            LZ4 decompression, PAMT parsing, and comprehensive documentation.</p>

            <p><b>MrIkso</b>
            (<a href="https://github.com/MrIkso">GitHub</a>)
            - CrimsonDesertTools C# reference implementation including PaChecksum
            algorithm, PAMT/PAPGT readers, and VFS path resolver.</p>

            <p><b>Altair200333</b>
            (<a href="https://github.com/Altair200333/crimson-desert-model-browser">GitHub</a>)
            - Crimson Desert Model Browser reference project. Helpful for PAC mesh
            parsing research, model validation, and cross-checking export behavior.</p>

            <hr>

            <h3>Built With</h3>
            <table cellpadding="4" cellspacing="0" width="100%">
                <tr><td><b>Python 3</b></td><td>Core application runtime</td></tr>
                <tr><td><b>PySide6 (Qt6)</b></td><td>Cross-platform UI framework</td></tr>
                <tr><td><b>fonttools</b></td><td>Font file parsing and generation</td></tr>
                <tr><td><b>lz4</b></td><td>LZ4 compression/decompression</td></tr>
                <tr><td><b>pycryptodome</b></td><td>ChaCha20 encryption/decryption</td></tr>
            </table>
        </div>
        """)
        layout.addWidget(browser, 1)
        return widget

    def _build_license_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(8, 8, 8, 8)

        browser = QTextBrowser()
        browser.setHtml(f"""
        {_BROWSER_CSS}
        <div style="padding: 16px;">
            <h2>License</h2>
            <p><b>{APP_NAME} v{APP_VERSION}</b> is released under the <b>MIT License</b>.</p>

            <pre style="background-color: #1e1e2e; padding: 12px; border-radius: 6px; color: #cdd6f4; white-space: pre-wrap;">
MIT License

Copyright (c) 2026 hzeem

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
            </pre>
        </div>
        """)
        layout.addWidget(browser, 1)
        return widget
