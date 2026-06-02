"""Diagnostic crash handlers for early-boot failures.

Why this module exists
----------------------
A reporter's log showed only the session banner + PyInstaller's
``pyi_splash`` IPC-connect message, then silence. No traceback, no
dialog. Forensics pointed at a **native DLL load failure** inside
``QApplication()`` — most likely Qt's platform plugin being truncated
after a hard-reboot interrupted the PyInstaller extraction.

A Python-level exception would have been caught by PyInstaller's
built-in windowed traceback dialog. A native C-level crash (access
violation, ``abort()`` from a C extension) kills the process with
zero output when ``console=False`` — exactly what the reporter saw.

This module installs three layers of diagnostics so the NEXT silent
exit writes enough evidence for us to pinpoint the cause:

1. **faulthandler** — Python stdlib, catches fatal signals (SEGV,
   ABRT, BUS, ILL) and native stack overflows. Dumps a C-level
   traceback to the file we hand it before the process dies.
2. **sys.excepthook** — replaces the default so any uncaught Python
   exception writes its traceback to our log file first, then the
   original hook (so PyInstaller's windowed dialog still fires).
3. **Windows ctypes MessageBox** fallback — for the critical early
   window where Qt can't be used yet (because Qt itself is what
   might be failing to load), we surface a native MessageBoxW
   dialog. Works even when PySide6/Qt fails to import or construct.

All three layers are **optional** — the module is structured so a
missing dependency (e.g. non-Windows platform for MessageBox)
degrades gracefully without breaking the app.

Design notes
------------
* The module is pure-Python-stdlib for the primary path. Qt/ctypes
  are imported lazily inside the functions that need them so the
  module itself imports cheaply.
* ``install_crash_handlers()`` is idempotent — calling it twice
  doesn't stack hooks.
* The handler keeps a reference to the opened fault-log file. It's
  intentionally never closed — that would race the final write
  during a crash. The OS reclaims it at process exit.
* All errors inside the handler are swallowed. A crashing crash-
  handler would be very bad; we'd rather lose one diagnostic than
  spawn a secondary exception.
"""

from __future__ import annotations

import io
import os
import sys
import traceback
from pathlib import Path
from typing import Optional, TextIO

# Module-level state. ``_installed`` guards against double-install.
# ``_fault_log_fh`` is kept alive for the process lifetime so
# ``faulthandler`` can still write during a crash.
_installed: bool = False
_fault_log_fh: Optional[TextIO] = None
_fault_log_path: str = ""
_original_excepthook = None


def _open_fault_log(path: str) -> Optional[TextIO]:
    """Open ``path`` in append mode + line-buffered + utf-8.

    Returns ``None`` on any I/O error — the caller treats that as
    "skip the faulthandler half, keep the Python-exception half".
    Creating the parent directory is attempted but not required to
    succeed; if it fails the open will fail too, which is fine.
    """
    try:
        p = Path(path).resolve()
        p.parent.mkdir(parents=True, exist_ok=True)
        # line-buffered so partial writes during a crash still flush
        return open(str(p), "a", encoding="utf-8", buffering=1)
    except Exception:
        return None


def _write_banner(fh: TextIO, kind: str) -> None:
    """Write a visually-distinctive separator to the fault log."""
    try:
        import datetime
        stamp = datetime.datetime.now().isoformat(timespec="seconds")
    except Exception:
        stamp = "?"
    try:
        fh.write("\n")
        fh.write("=" * 78 + "\n")
        fh.write(f"{kind}  @  {stamp}\n")
        fh.write("=" * 78 + "\n")
        fh.flush()
    except Exception:
        pass


def _format_exception(exc_type, exc_value, tb) -> str:
    """Format an exception + traceback like the default excepthook."""
    try:
        lines = traceback.format_exception(exc_type, exc_value, tb)
        return "".join(lines)
    except Exception:
        return f"{exc_type}: {exc_value}\n(traceback formatting failed)\n"


def _excepthook(exc_type, exc_value, tb) -> None:
    """Write the traceback to our fault log, then chain to the original.

    Chaining keeps PyInstaller's windowed-traceback dialog working
    for interactive users while also guaranteeing the traceback ends
    up in ``crimsonforge.log`` for us to see post-mortem.
    """
    # Never interfere with KeyboardInterrupt — let it pass through.
    if issubclass(exc_type, KeyboardInterrupt):
        if _original_excepthook is not None:
            _original_excepthook(exc_type, exc_value, tb)
        else:
            sys.__excepthook__(exc_type, exc_value, tb)
        return

    if _fault_log_fh is not None:
        _write_banner(_fault_log_fh, "UNCAUGHT PYTHON EXCEPTION")
        try:
            _fault_log_fh.write(_format_exception(exc_type, exc_value, tb))
            _fault_log_fh.flush()
        except Exception:
            pass

    # Chain to the original hook so dialogs / stderr printing still fire.
    try:
        if _original_excepthook is not None:
            _original_excepthook(exc_type, exc_value, tb)
        else:
            sys.__excepthook__(exc_type, exc_value, tb)
    except Exception:
        # Absolutely last-ditch: if the chain raises, swallow it.
        pass


def install_crash_handlers(log_path: str) -> bool:
    """Install faulthandler + Python excepthook. Idempotent.

    Returns ``True`` when at least one handler was installed, even
    if other parts failed. The caller typically doesn't care — the
    function never raises.

    ``log_path`` is the file that native faults + Python tracebacks
    will be appended to. The CrimsonForge convention is the same
    file as the regular session log, so all startup diagnostics
    land in one place.
    """
    global _installed, _fault_log_fh, _fault_log_path, _original_excepthook
    if _installed:
        return True

    _fault_log_path = log_path
    _fault_log_fh = _open_fault_log(log_path)

    # 1. faulthandler — catches native crashes (SEGV, ABRT, stack
    # overflow in a C extension, etc.). Noop when fh is None; we
    # still install the Python-level hook.
    installed_any = False
    if _fault_log_fh is not None:
        try:
            import faulthandler
            faulthandler.enable(file=_fault_log_fh, all_threads=True)
            installed_any = True
        except Exception:
            pass

    # 2. Python uncaught-exception hook.
    # Guard against saving our own wrapper as "the original" if the
    # caller re-runs install after a partial shutdown. That would
    # cause the reset path to be a no-op.
    try:
        existing = sys.excepthook
        if existing is not _excepthook:
            _original_excepthook = existing
        sys.excepthook = _excepthook
        installed_any = True
    except Exception:
        pass

    _installed = installed_any
    return installed_any


def log_and_show_fatal(title: str, message: str) -> None:
    """Write a fatal-boot message to the log + surface a MessageBox.

    Used at the very top of ``main()`` around ``QApplication()``
    construction — the one place where Qt itself might be what's
    failing. We use a ctypes MessageBoxW because PySide6 by that
    point may not be importable.

    No-op on non-Windows platforms (the log write still happens).
    Any exception inside is swallowed.
    """
    # Write to log first — that's the primary artefact.
    if _fault_log_fh is not None:
        _write_banner(_fault_log_fh, "FATAL BOOT FAILURE")
        try:
            _fault_log_fh.write(f"{title}\n\n{message}\n")
            _fault_log_fh.flush()
        except Exception:
            pass

    # Native MessageBox — Windows only.
    if sys.platform != "win32":
        return
    try:
        import ctypes
        MB_OK = 0x00000000
        MB_ICONERROR = 0x00000010
        ctypes.windll.user32.MessageBoxW(
            None,
            ctypes.c_wchar_p(message),
            ctypes.c_wchar_p(title),
            MB_OK | MB_ICONERROR,
        )
    except Exception:
        pass


def is_installed() -> bool:
    """Test helper — True once :func:`install_crash_handlers` ran."""
    return _installed


def _reset_for_tests() -> None:
    """Test-only: restore the pristine excepthook + reset module state.

    Tests install handlers, exercise them, then call this to clean
    up. Production code never calls this.

    We explicitly restore ``sys.__excepthook__`` rather than whatever
    was saved in ``_original_excepthook``. That avoids a subtle bug
    where a previous test leaked ``ch._excepthook`` into
    ``sys.excepthook``; the next ``install_crash_handlers`` call
    would then save our own hook as "the original" and reset would
    end up back where it started.
    """
    global _installed, _fault_log_fh, _fault_log_path, _original_excepthook
    try:
        sys.excepthook = sys.__excepthook__
    except Exception:
        pass
    try:
        if _fault_log_fh is not None:
            _fault_log_fh.close()
    except Exception:
        pass
    # Also turn faulthandler off so the test-created file can be
    # unlinked by the OS cleanup hook. The next test that actually
    # needs fault handling re-enables it via install_crash_handlers.
    try:
        import faulthandler
        faulthandler.disable()
    except Exception:
        pass
    _installed = False
    _fault_log_fh = None
    _fault_log_path = ""
    _original_excepthook = None
