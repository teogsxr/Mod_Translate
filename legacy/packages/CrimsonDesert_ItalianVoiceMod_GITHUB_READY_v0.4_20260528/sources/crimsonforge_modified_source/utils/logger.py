"""Structured logging system with file and console output.

Provides a configured logger for the entire application with
configurable log levels, file output, and formatted console output.

HARDENED:
  - Every write is flushed immediately to disk (ImmediateFileHandler).
  - The file handler is attached to the ROOT Python logger so that
    ALL modules — even those using logging.getLogger(__name__) — write
    to crimsonforge.log automatically.
  - The log file is cleared at startup so every run begins fresh.
  - Path is resolved absolutely from this file's physical location,
    never from CWD, so the log file is always found.
"""

import logging
import sys
from pathlib import Path
from typing import Optional


_logger_instance: Optional[logging.Logger] = None
_file_handler: Optional[logging.FileHandler] = None
_file_cleared_this_session: bool = False


class _ImmediateFileHandler(logging.FileHandler):
    """FileHandler that flushes after every emit so no entry is lost on crash."""

    def emit(self, record):
        super().emit(record)
        self.flush()


def setup_logger(
    log_level: str = "INFO",
    log_file: str = "",
    debug_mode: bool = True,
    name: str = "crimsonforge"
) -> logging.Logger:
    """Initialize and configure the application logger.

    The file handler is attached to the ROOT logger so that every module
    using logging.getLogger(__name__) writes to the log file without any
    extra configuration.

    Args:
        log_level: Logging level string (DEBUG, INFO, WARNING, ERROR, CRITICAL).
        log_file: Path to log file. Empty string means no file logging.
        debug_mode: If True, forces DEBUG level and detailed formatting.
        name: Named logger returned to callers.

    Returns:
        Configured named logger instance.
    """
    global _logger_instance, _file_handler, _file_cleared_this_session

    level = logging.DEBUG if debug_mode else getattr(logging, log_level.upper(), logging.INFO)

    # ── Formatters ────────────────────────────────────────────────────────────
    detail_fmt = logging.Formatter(
        "%(asctime)s [%(levelname)-8s] %(name)s:%(funcName)s:%(lineno)d - %(message)s",
        datefmt="%H:%M:%S"
    )
    file_fmt = logging.Formatter(
        "%(asctime)s [%(levelname)-8s] %(name)s:%(funcName)s:%(lineno)d - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # ── Root logger: owns the file handler ────────────────────────────────────
    # By configuring the root logger, EVERY logger in the process (including
    # logging.getLogger("core.audio_converter"), logging.getLogger(__name__),
    # etc.) will propagate its records here and write to disk automatically.
    # We forcefully set the root level to DEBUG so ALL handlers receive verbose
    # logs (handlers do their own level filtering).
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    # Remove any previously added handlers to avoid duplicates on re-init
    for h in list(root.handlers):
        root.removeHandler(h)

    # Console handler on root (Filtered by user configuration)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.setFormatter(detail_fmt)
    root.addHandler(console_handler)

    # ── File handler on root ──
    # Always default to project root crimsonforge.log if empty
    if not log_file:
        here = Path(__file__).resolve()
        project_root = here.parent.parent.parent
        log_file = str(project_root / "crimsonforge.log")

    log_path = Path(log_file).resolve()
    log_path.parent.mkdir(parents=True, exist_ok=True)

    # Clear the file at startup so every run starts fresh, 
    # but only do it ONCE per process lifetime so we don't wipe earlier logs
    if not _file_cleared_this_session:
        try:
            log_path.write_text("", encoding="utf-8")
            _file_cleared_this_session = True
        except OSError:
            pass  # If we can't clear, we'll append

    fh = _ImmediateFileHandler(
        str(log_path), mode="a", encoding="utf-8", delay=False
    )
    # File ALWAYS records verbose logs for troubleshooting
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(file_fmt)
    root.addHandler(fh)
    _file_handler = fh

    # ── Named logger: returned to callers ─────────────────────────────────────
    # propagate=True (default) means records flow up to the root logger above.
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    # Do NOT touch logger.propagate — leave it True so messages reach root handlers.

    _logger_instance = logger
    return logger


def get_logger(module_name: str = "") -> logging.Logger:
    """Get the application logger, optionally with a child name.

    First call initialises the root+named logger with the correct log file
    path (resolved from this file's physical location, not CWD).

    Args:
        module_name: Sub-module name (e.g. 'core.crypto'). If empty, returns
                     the root 'crimsonforge' logger.

    Returns:
        Logger instance.
    """
    global _logger_instance
    if _logger_instance is None:
        # Resolve project root from this file's real path:
        #   .../Project_IDE/crimsonforge/utils/logger.py
        #   three .parent calls → Project_IDE/
        here = Path(__file__).resolve()
        project_root = here.parent.parent.parent  # Project_IDE/
        log_file = str(project_root / "crimsonforge.log")

        _logger_instance = setup_logger(log_file=log_file, debug_mode=True)

        _logger_instance.info("=" * 70)
        _logger_instance.info("CrimsonForge session started")
        _logger_instance.info("Log file : %s", log_file)
        _logger_instance.info("=" * 70)

    if module_name:
        return _logger_instance.getChild(module_name)
    return _logger_instance
