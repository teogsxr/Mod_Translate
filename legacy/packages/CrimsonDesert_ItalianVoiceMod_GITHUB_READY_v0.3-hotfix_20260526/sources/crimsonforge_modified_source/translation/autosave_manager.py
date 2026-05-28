"""Periodic autosave of translation progress.

Runs a timer that saves the translation project at configurable intervals.
Tracks unsaved changes and provides recovery on restart.
"""

from PySide6.QtCore import QTimer, QObject, Signal

from translation.translation_project import TranslationProject
from utils.logger import get_logger

logger = get_logger("translation.autosave")


class AutosaveManager(QObject):
    """Manages periodic autosave of translation projects."""

    autosaved = Signal(str)
    autosave_error = Signal(str)

    def __init__(self, interval_seconds: int = 30, parent=None):
        super().__init__(parent)
        self._interval = interval_seconds
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._do_autosave)
        self._project: TranslationProject = None
        self._enabled = True
        self._unsaved_changes = 0
        self._last_save_time = ""

    def set_project(self, project: TranslationProject) -> None:
        """Set the project to autosave."""
        self._project = project

    def start(self) -> None:
        """Start the autosave timer."""
        if self._enabled and self._interval > 0:
            self._timer.start(self._interval * 1000)
            logger.info("Autosave started: every %d seconds", self._interval)

    def stop(self) -> None:
        """Stop the autosave timer."""
        self._timer.stop()

    def set_interval(self, seconds: int) -> None:
        """Update the autosave interval."""
        self._interval = seconds
        if self._timer.isActive():
            self._timer.stop()
            self.start()

    def set_enabled(self, enabled: bool) -> None:
        """Enable or disable autosave."""
        self._enabled = enabled
        if not enabled:
            self._timer.stop()
        elif self._project and self._project.project_file:
            self.start()

    def notify_change(self) -> None:
        """Notify that a change was made (increment unsaved count)."""
        self._unsaved_changes += 1

    def _do_autosave(self) -> None:
        """Perform the autosave."""
        if not self._project or not self._project.project_file:
            return
        if not self._project.modified:
            return

        try:
            self._project.save()
            self._unsaved_changes = 0
            from datetime import datetime
            self._last_save_time = datetime.now().strftime("%H:%M:%S")
            self.autosaved.emit(self._last_save_time)
            logger.info("Autosaved at %s", self._last_save_time)
        except Exception as e:
            error_msg = f"Autosave failed: {e}"
            self.autosave_error.emit(error_msg)
            logger.error(error_msg)

    @property
    def unsaved_changes(self) -> int:
        return self._unsaved_changes

    @property
    def last_save_time(self) -> str:
        return self._last_save_time

    @property
    def is_active(self) -> bool:
        return self._timer.isActive()

    @property
    def enabled(self) -> bool:
        return self._enabled
