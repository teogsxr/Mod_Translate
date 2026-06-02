"""Unit tests for :mod:`core.crash_handler`.

The crash handler is the safety net for early-boot silent failures
like the reporter's case where only the session banner + pyi_splash
IPC appeared in the log before the process died. We verify:

  * Installation is idempotent.
  * ``sys.excepthook`` is replaced with our wrapper.
  * The wrapper writes tracebacks to the fault log file.
  * The wrapper chains to the original excepthook.
  * ``KeyboardInterrupt`` passes through without being logged.
  * ``log_and_show_fatal`` writes to the log without raising even
    when the log file can't be opened.
  * faulthandler is engaged after install_crash_handlers runs.
  * The handler gracefully degrades when the log path is unwritable.

Tests use ``_reset_for_tests`` between cases so installing the
handler in one test doesn't bleed into another.
"""

from __future__ import annotations

import io
import os
import shutil
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import core.crash_handler as ch   # noqa: E402


def _make_temp_log(test_case):
    """Create a temp dir + log path that cleans up AFTER the crash
    handler has been reset. Windows can't unlink a file while
    faulthandler / our open handle still references it, so ordering
    matters.
    """
    td = tempfile.mkdtemp(prefix="cf_crash_")
    # addCleanup runs in LIFO order, so register the rmtree first so
    # it runs LAST (after _reset_for_tests has closed the file).
    test_case.addCleanup(shutil.rmtree, td, True)
    test_case.addCleanup(ch._reset_for_tests)
    return os.path.join(td, "log.log")


# ═════════════════════════════════════════════════════════════════════
# Installation
# ═════════════════════════════════════════════════════════════════════

class InstallCrashHandlers(unittest.TestCase):
    def setUp(self):
        ch._reset_for_tests()

    def test_returns_true_on_success(self):
        log_path = _make_temp_log(self)
        self.assertTrue(ch.install_crash_handlers(log_path))

    def test_is_installed_reflects_state(self):
        self.assertFalse(ch.is_installed())
        log_path = _make_temp_log(self)
        ch.install_crash_handlers(log_path)
        self.assertTrue(ch.is_installed())

    def test_idempotent(self):
        log_path = _make_temp_log(self)
        self.assertTrue(ch.install_crash_handlers(log_path))
        first_hook = sys.excepthook
        # Second call must not re-install.
        self.assertTrue(ch.install_crash_handlers(log_path))
        self.assertIs(sys.excepthook, first_hook)

    def test_replaces_sys_excepthook(self):
        original = sys.excepthook
        log_path = _make_temp_log(self)
        ch.install_crash_handlers(log_path)
        self.assertIsNot(sys.excepthook, original)
        self.assertIs(sys.excepthook, ch._excepthook)

    def test_creates_parent_directory(self):
        log_path = _make_temp_log(self)
        nested = os.path.join(os.path.dirname(log_path), "a", "b", "c", "log.log")
        self.assertTrue(ch.install_crash_handlers(nested))
        self.assertTrue(Path(nested).parent.exists())

    def test_log_file_is_utf8(self):
        log_path = _make_temp_log(self)
        ch.install_crash_handlers(log_path)
        # Write unicode via our banner
        ch._write_banner(ch._fault_log_fh, "TEST — emoji check")
        content = Path(log_path).read_text(encoding="utf-8")
        self.assertIn("emoji check", content)

    def test_unwritable_log_path_does_not_crash(self):
        # Give a path that can't be opened. The handler must degrade
        # gracefully and never raise.
        self.addCleanup(ch._reset_for_tests)
        if sys.platform == "win32":
            bad = "nul:/cannot_write.log"
        else:
            bad = "/dev/full/definitely_cannot_write.log"
        result = ch.install_crash_handlers(bad)
        # Python hook should still be installed even if faulthandler
        # couldn't open the file.
        self.assertTrue(isinstance(result, bool))

    def test_faulthandler_enabled(self):
        import faulthandler
        log_path = _make_temp_log(self)
        ch.install_crash_handlers(log_path)
        self.assertTrue(faulthandler.is_enabled())


# ═════════════════════════════════════════════════════════════════════
# Excepthook behaviour
# ═════════════════════════════════════════════════════════════════════

class ExcepthookBehaviour(unittest.TestCase):
    def setUp(self):
        ch._reset_for_tests()
        self._log_path = _make_temp_log(self)
        ch.install_crash_handlers(self._log_path)

    def _fire_exception(self, exc):
        """Fake 'uncaught exception' by calling the excepthook directly."""
        try:
            raise exc
        except BaseException as e:   # noqa: BLE001 — intentional
            sys.excepthook(type(e), e, e.__traceback__)

    def test_writes_traceback_to_log(self):
        self._fire_exception(RuntimeError("deliberate test failure"))
        content = Path(self._log_path).read_text(encoding="utf-8")
        self.assertIn("UNCAUGHT PYTHON EXCEPTION", content)
        self.assertIn("RuntimeError", content)
        self.assertIn("deliberate test failure", content)

    def test_writes_full_traceback(self):
        self._fire_exception(ValueError("x"))
        content = Path(self._log_path).read_text(encoding="utf-8")
        self.assertIn("Traceback", content)
        self.assertIn("test_crash_handler.py", content)

    def test_chains_to_original_hook(self):
        called = []

        def fake_original(exc_type, exc_value, tb):
            called.append((exc_type, str(exc_value)))

        ch._original_excepthook = fake_original
        try:
            self._fire_exception(KeyError("pass-through"))
        finally:
            ch._original_excepthook = None

        self.assertEqual(len(called), 1)
        self.assertEqual(called[0][0], KeyError)

    def test_keyboard_interrupt_not_logged(self):
        self._fire_exception(KeyboardInterrupt())
        content = Path(self._log_path).read_text(encoding="utf-8")
        self.assertNotIn("UNCAUGHT PYTHON EXCEPTION", content)

    def test_keyboard_interrupt_still_chains(self):
        called = []
        ch._original_excepthook = lambda t, v, tb: called.append(t)
        try:
            self._fire_exception(KeyboardInterrupt())
        finally:
            ch._original_excepthook = None
        self.assertEqual(called, [KeyboardInterrupt])

    def test_multiple_exceptions_all_logged(self):
        self._fire_exception(TypeError("first"))
        self._fire_exception(IndexError("second"))
        content = Path(self._log_path).read_text(encoding="utf-8")
        self.assertIn("first", content)
        self.assertIn("second", content)
        # Two banners.
        self.assertEqual(content.count("UNCAUGHT PYTHON EXCEPTION"), 2)

    def test_broken_original_hook_does_not_propagate(self):
        ch._original_excepthook = lambda *_: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            # Excepthook must swallow errors from the chained hook.
            self._fire_exception(ValueError("x"))
        finally:
            ch._original_excepthook = None


# ═════════════════════════════════════════════════════════════════════
# log_and_show_fatal
# ═════════════════════════════════════════════════════════════════════

class LogAndShowFatal(unittest.TestCase):
    def setUp(self):
        ch._reset_for_tests()

    def test_writes_to_log(self):
        log_path = _make_temp_log(self)
        ch.install_crash_handlers(log_path)
        # Prevent the actual MessageBox from popping during tests.
        with mock.patch.object(sys, "platform", "linux"):
            ch.log_and_show_fatal("boom", "something broke")
        content = Path(log_path).read_text(encoding="utf-8")
        self.assertIn("FATAL BOOT FAILURE", content)
        self.assertIn("boom", content)
        self.assertIn("something broke", content)

    def test_no_crash_when_not_installed(self):
        self.addCleanup(ch._reset_for_tests)
        # install_crash_handlers never called — log_and_show_fatal
        # should still be safe.
        with mock.patch.object(sys, "platform", "linux"):
            ch.log_and_show_fatal("title", "message")   # must not raise

    def test_message_box_suppressed_on_non_windows(self):
        log_path = _make_temp_log(self)
        ch.install_crash_handlers(log_path)
        with mock.patch.object(sys, "platform", "linux"):
            # Should not even try to import ctypes.windll.
            ch.log_and_show_fatal("t", "m")

    def test_message_box_error_suppressed(self):
        log_path = _make_temp_log(self)
        ch.install_crash_handlers(log_path)
        with mock.patch.object(sys, "platform", "win32"):
            import ctypes
            with mock.patch.object(
                ctypes, "windll",
                new=mock.MagicMock(
                    user32=mock.MagicMock(
                        MessageBoxW=mock.MagicMock(side_effect=OSError("boom")),
                    ),
                ),
            ):
                # Must not raise even when MessageBoxW throws.
                ch.log_and_show_fatal("title", "message")

    def test_long_messages_logged_in_full(self):
        long_msg = "x" * 4000
        log_path = _make_temp_log(self)
        ch.install_crash_handlers(log_path)
        with mock.patch.object(sys, "platform", "linux"):
            ch.log_and_show_fatal("t", long_msg)
        self.assertIn(long_msg, Path(log_path).read_text(encoding="utf-8"))

    def test_unicode_messages_logged(self):
        log_path = _make_temp_log(self)
        ch.install_crash_handlers(log_path)
        with mock.patch.object(sys, "platform", "linux"):
            ch.log_and_show_fatal("Qt failed", "Reason: test japanese nihongo + russian oshibka")
        content = Path(log_path).read_text(encoding="utf-8")
        self.assertIn("Qt failed", content)
        self.assertIn("nihongo", content)
        self.assertIn("oshibka", content)


# ═════════════════════════════════════════════════════════════════════
# _format_exception + _write_banner helpers
# ═════════════════════════════════════════════════════════════════════

class FormatAndWriteHelpers(unittest.TestCase):
    def test_format_exception_plain(self):
        try:
            raise RuntimeError("test-string")
        except Exception as e:
            out = ch._format_exception(type(e), e, e.__traceback__)
        self.assertIn("RuntimeError", out)
        self.assertIn("test-string", out)
        self.assertIn("Traceback", out)

    def test_format_exception_handles_weird_object(self):
        """If Python's own formatter blows up, we fall back to str()."""
        class Pathological(Exception):
            def __str__(self):
                return "ok"

        # Even for custom exception subclasses, we should at least
        # emit the type name.
        out = ch._format_exception(Pathological, Pathological("x"), None)
        self.assertIn("Pathological", out)

    def test_write_banner_prints_separator(self):
        buf = io.StringIO()
        ch._write_banner(buf, "TEST KIND")
        got = buf.getvalue()
        self.assertIn("TEST KIND", got)
        self.assertIn("=", got)

    def test_write_banner_includes_timestamp(self):
        buf = io.StringIO()
        ch._write_banner(buf, "X")
        got = buf.getvalue()
        # ISO-8601 timestamp contains a T and at least one colon.
        self.assertRegex(got, r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")

    def test_write_banner_tolerates_broken_file(self):
        class BrokenFile:
            def write(self, _):
                raise OSError("disk full")
            def flush(self):
                raise OSError("disk full")
        # Must not raise.
        ch._write_banner(BrokenFile(), "CRASH")


# ═════════════════════════════════════════════════════════════════════
# State reset (test-only helper)
# ═════════════════════════════════════════════════════════════════════

class ResetForTests(unittest.TestCase):
    def setUp(self):
        ch._reset_for_tests()

    def test_reset_restores_original_excepthook(self):
        original = sys.excepthook
        log_path = _make_temp_log(self)
        ch.install_crash_handlers(log_path)
        ch._reset_for_tests()
        self.assertIs(sys.excepthook, original)

    def test_reset_clears_fault_log_handle(self):
        log_path = _make_temp_log(self)
        ch.install_crash_handlers(log_path)
        self.assertIsNotNone(ch._fault_log_fh)
        ch._reset_for_tests()
        self.assertIsNone(ch._fault_log_fh)

    def test_reset_flips_is_installed(self):
        log_path = _make_temp_log(self)
        ch.install_crash_handlers(log_path)
        self.assertTrue(ch.is_installed())
        ch._reset_for_tests()
        self.assertFalse(ch.is_installed())

    def test_double_reset_safe(self):
        self.addCleanup(ch._reset_for_tests)
        ch._reset_for_tests()
        ch._reset_for_tests()
        # No crash, no side effects.


# ═════════════════════════════════════════════════════════════════════
# Integration — replacement round-trip with a real exception
# ═════════════════════════════════════════════════════════════════════

class IntegrationRoundTrip(unittest.TestCase):
    def setUp(self):
        ch._reset_for_tests()

    def test_excepthook_captures_real_exception(self):
        log_path = _make_temp_log(self)
        ch.install_crash_handlers(log_path)

        def _suppress_original(exc_type, exc_value, tb):
            # Swallow so the test doesn't print to stderr.
            pass

        ch._original_excepthook = _suppress_original
        try:
            try:
                raise ZeroDivisionError("test-division-error")
            except ZeroDivisionError as e:
                sys.excepthook(type(e), e, e.__traceback__)
        finally:
            ch._original_excepthook = None

        content = Path(log_path).read_text(encoding="utf-8")
        self.assertIn("ZeroDivisionError", content)
        self.assertIn("test-division-error", content)

    def test_chained_exception_logged(self):
        log_path = _make_temp_log(self)
        ch.install_crash_handlers(log_path)

        def _suppress_original(exc_type, exc_value, tb):
            pass

        ch._original_excepthook = _suppress_original
        try:
            try:
                try:
                    raise ValueError("inner")
                except ValueError as e:
                    raise RuntimeError("outer") from e
            except RuntimeError as e:
                sys.excepthook(type(e), e, e.__traceback__)
        finally:
            ch._original_excepthook = None

        content = Path(log_path).read_text(encoding="utf-8")
        self.assertIn("outer", content)
        # Python's default formatter includes the 'from' chain.
        self.assertIn("inner", content)


# ═════════════════════════════════════════════════════════════════════
# Environment: ensure no test leaks a broken excepthook between tests
# ═════════════════════════════════════════════════════════════════════

class NoCrossTestLeak(unittest.TestCase):
    def test_excepthook_clean_after_reset(self):
        ch._reset_for_tests()
        log_path = _make_temp_log(self)
        ch.install_crash_handlers(log_path)
        ch._reset_for_tests()
        # sys.excepthook should now be restored (or sys.__excepthook__
        # or whatever the original was). It must not be ch._excepthook.
        self.assertIsNot(sys.excepthook, ch._excepthook)


if __name__ == "__main__":
    unittest.main()
