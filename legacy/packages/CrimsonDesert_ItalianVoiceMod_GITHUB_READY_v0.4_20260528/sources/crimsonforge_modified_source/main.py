"""CrimsonForge - Crimson Desert Modding Studio."""

import sys
import os
import tempfile
import glob
import shutil

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ── Crash diagnostics: install FIRST, before any import that could
#    fail at module-init time. Previously a native DLL-load crash in
#    QApplication() or in a hidden import exited the process with
#    zero log output. faulthandler now captures C-level faults and
#    the Python excepthook catches uncaught exceptions. Both write
#    to the session log before the process dies. See
#    core.crash_handler for the three-layer defence.
try:
    from core.crash_handler import install_crash_handlers, log_and_show_fatal
    _crash_log_path = os.path.join(tempfile.gettempdir(), "crimsonforge.log")
    install_crash_handlers(_crash_log_path)
except Exception:
    # Best-effort only — the rest of the app must still boot even
    # when the diagnostics module can't load.
    def log_and_show_fatal(title, message):   # type: ignore[no-redef]
        pass

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from version import APP_VERSION, APP_NAME
from utils.config import ConfigManager, ConfigLoadError
from utils.logger import setup_logger, get_logger
# NOTE: ai.provider_registry is intentionally NOT imported at module
# top-level. Importing it eagerly pulls in 10 provider modules
# (openai, anthropic, gemini, deepseek, ollama, vllm, mistral,
# cohere, custom, deepl) which collectively take ~2 s warm and
# ~14 s cold on first launch. We hand MainWindow a factory that
# imports + builds the registry only when an AI-using tab actually
# calls a registry method (typically when the user opens Translate
# or Settings, not at startup).
from ui.main_window import MainWindow


def _close_splash():
    """Close the PyInstaller splash screen if running from a bundled exe."""
    try:
        import pyi_splash          # only available inside PyInstaller bundle
        pyi_splash.close()
    except ImportError:
        pass


def _cleanup_temp_files():
    """Delete all temporary directories and files created during the session."""
    tmp = tempfile.gettempdir()
    patterns = [
        "crimsonforge_audio_*",
        "crimsonforge_preview_*",
        "cf_wem_out",
        "cf_wwise_project",
        "cf_wwise_*",
        "cf_wem_*.wem"
    ]
    
    count = 0
    for pat in patterns:
        for path in glob.glob(os.path.join(tmp, pat)):
            try:
                if os.path.isdir(path):
                    shutil.rmtree(path, ignore_errors=True)
                else:
                    os.remove(path)
                count += 1
            except Exception:
                pass
                
    if count > 0:
        log = get_logger("cleanup")
        log.info("Cleaned up %d temporary files/folders on exit", count)


def main():
    # QApplication() is the single most-likely silent-crash point in
    # PyInstaller bundles — it loads the Qt platform plugin which can
    # abort natively when the plugin DLL is truncated (e.g. after a
    # force-reboot interrupted the bundle extraction). We surround it
    # with a Python try/except so any Python-level failure surfaces
    # as a log line + native MessageBox; faulthandler (installed at
    # module top) catches the native-abort case.
    try:
        app = QApplication(sys.argv)
    except Exception as e:
        log_and_show_fatal(
            "CrimsonForge — Qt initialisation failed",
            (
                f"Qt / PySide6 could not be initialised:\n\n{type(e).__name__}: {e}\n\n"
                "Likely causes:\n"
                "  • Hard reboot corrupted the PyInstaller extraction. "
                "Delete %TEMP%\\_MEI* folders and retry.\n"
                "  • Missing VC++ 2015-2022 redistributable "
                "(https://aka.ms/vs/17/release/vc_redist.x64.exe).\n"
                "  • Antivirus quarantined a bundled DLL. "
                "Whitelist the exe and %TEMP%\\_MEI* folders."
            ),
        )
        return 1
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName("hzeem")

    # Set a multilingual font with fallbacks for Korean, Chinese, Japanese, Arabic, etc.
    from PySide6.QtGui import QFont
    font = QFont("Segoe UI", 10)
    font.setFamilies([
        "Segoe UI",            # Latin, Cyrillic
        "Microsoft YaHei",     # Chinese (Simplified)
        "Malgun Gothic",       # Korean
        "Meiryo",              # Japanese
        "Segoe UI Symbol",     # Symbols, emoji
        "Noto Sans",           # Broad Unicode coverage (if installed)
    ])
    app.setFont(font)

    try:
        config = ConfigManager()
    except ConfigLoadError as e:
        _close_splash()
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.critical(None, "Configuration Error", str(e))
        return 1

    logger = setup_logger(
        log_level=config.get("advanced.log_level", "INFO"),
        log_file=config.get("advanced.log_file", ""),
        debug_mode=config.get("advanced.debug_mode", False),
    )
    logger.info("%s v%s starting...", APP_NAME, APP_VERSION)
    logger.info("Config loaded from: %s", config.config_path)

    # ── Splash screen so the user doesn't see a blank/white window ──
    # MainWindow's constructor builds 8 tabs, the OpenGL preview, the
    # syntax-highlighted editor, etc. — that takes ~2-5 s on a fresh
    # Python process (PySide6 imports + Qt resource init). Without a
    # splash, Windows shows the OS shell shadow + a blank client area
    # for that whole time. The splash is a fully-painted top-level
    # window with a frameless dark style so Windows DOESN'T paint a
    # blank client area first.
    splash = None
    try:
        from PySide6.QtWidgets import QSplashScreen
        from PySide6.QtGui import QPixmap, QColor, QPainter, QFont as _QFont, QLinearGradient
        # Big enough to be visibly "the app starting", small enough to
        # not dominate the screen on first run.
        pix = QPixmap(560, 320)
        # Solid base then gradient overlay so the pixmap NEVER paints
        # transparent (even one transparent frame is what looks like
        # the "white window" the user sees).
        pix.fill(QColor("#11111b"))
        p = QPainter(pix)
        gradient = QLinearGradient(0, 0, 0, pix.height())
        gradient.setColorAt(0.0, QColor("#1e1e2e"))
        gradient.setColorAt(1.0, QColor("#11111b"))
        p.fillRect(pix.rect(), gradient)
        # Subtle border so the splash reads as a window, not just text.
        p.setPen(QColor("#45475a"))
        p.drawRect(0, 0, pix.width() - 1, pix.height() - 1)
        # Title.
        p.setPen(QColor("#cdd6f4"))
        p.setFont(_QFont("Segoe UI", 32, _QFont.Bold))
        p.drawText(0, 0, pix.width(), 200,
                   Qt.AlignHCenter | Qt.AlignBottom, APP_NAME)
        # Tagline.
        p.setPen(QColor("#a6adc8"))
        p.setFont(_QFont("Segoe UI", 11))
        p.drawText(0, 200, pix.width(), 30,
                   Qt.AlignHCenter | Qt.AlignVCenter,
                   "Crimson Desert modding studio")
        # Version + status bar at bottom.
        p.setPen(QColor("#7f849c"))
        p.setFont(_QFont("Segoe UI", 9))
        p.drawText(0, pix.height() - 32, pix.width(), 20,
                   Qt.AlignHCenter | Qt.AlignVCenter,
                   f"v{APP_VERSION}")
        p.end()

        splash = QSplashScreen(pix)
        # WindowStaysOnTopHint stops Windows hiding it behind the
        # spawning console window during cold start.
        splash.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        splash.show()
        splash.showMessage(
            "Loading Python modules...",
            Qt.AlignHCenter | Qt.AlignBottom,
            QColor("#cdd6f4"),
        )
        # Force the window manager to realise the splash and paint
        # it BEFORE we start the expensive MainWindow constructor.
        # processEvents is one round; we run a tiny event-loop spin
        # here so X11/Wayland/Win32 all flush their compositor.
        for _ in range(3):
            app.processEvents()
        splash.repaint()
    except Exception as splash_exc:
        logger.debug("Splash screen unavailable: %s", splash_exc)

    def _splash_status(message: str) -> None:
        """Update the splash's status line and force a repaint.

        The user reported a "white window for ~5 s" symptom — that's
        the gap between QApplication paint and MainWindow's first
        widget render. Updating the splash with each major init
        milestone keeps it visibly alive during that gap.
        """
        if splash is None:
            return
        try:
            splash.showMessage(
                message,
                Qt.AlignHCenter | Qt.AlignBottom,
                QColor("#cdd6f4"),
            )
            app.processEvents()
            splash.repaint()
        except Exception:
            pass

    _splash_status("Initialising AI provider stubs...")

    def _build_registry():
        """Construct the AI provider registry on first access.

        Runs at most once — the result is cached inside MainWindow.
        Imported lazily so users who never open the AI-aware tabs
        don't pay the ~2-14 s startup cost of loading 10 provider
        SDK modules (openai, anthropic, gemini, deepseek, etc.).
        """
        from ai.provider_registry import ProviderRegistry
        registry = ProviderRegistry()
        registry.initialize_from_config(config.get_section("ai_providers"))
        logger.info(
            "AI providers initialized: %s",
            registry.list_enabled_provider_ids(),
        )
        return registry

    _splash_status("Building main window...")
    window = MainWindow(config, registry_factory=_build_registry)

    _close_splash()
    _splash_status("Almost ready...")
    window.show()
    # Wait for the window to be fully painted before tearing down the
    # splash — otherwise the user sees a momentary flash of nothing
    # between splash dismissal and window first paint.
    for _ in range(3):
        app.processEvents()
    if splash is not None:
        splash.finish(window)
        splash = None

    logger.info("Application ready")
    ret = app.exec()
    
    logger.info("Application closing. Running cleanup...")
    _cleanup_temp_files()
    
    return ret


if __name__ == "__main__":
    sys.exit(main())
