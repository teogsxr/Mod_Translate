"""QThread-based worker for background operations.

Provides a reusable worker pattern for running heavy operations
without blocking the UI thread, with progress reporting and cancellation.
"""

from PySide6.QtCore import QThread, Signal, QMutex, QMutexLocker


class ThreadWorker(QThread):
    """Base worker thread with progress, result, and error signals.

    Subclass and override run_task() to implement your operation.

    Signals:
        progress(int, str): Emitted with (percentage 0-100, status message)
        finished_result(object): Emitted on successful completion with the result
        error_occurred(str): Emitted on error with the error message
        cancelled(): Emitted when the operation is cancelled
    """

    progress = Signal(int, str)
    finished_result = Signal(object)
    error_occurred = Signal(str)
    cancelled = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cancel_requested = False
        self._mutex = QMutex()

    def run(self):
        try:
            result = self.run_task()
            if self.is_cancelled():
                self.cancelled.emit()
            else:
                self.finished_result.emit(result)
        except Exception as e:
            if not self.is_cancelled():
                self.error_occurred.emit(
                    f"{type(e).__name__}: {str(e)}"
                )

    def run_task(self):
        """Override this method with your actual work.

        Call self.report_progress(pct, msg) periodically.
        Check self.is_cancelled() to support cancellation.

        Returns:
            The result object to be emitted via finished_result signal.
        """
        raise NotImplementedError("Subclasses must implement run_task()")

    def request_cancel(self):
        """Request cancellation of the running task."""
        with QMutexLocker(self._mutex):
            self._cancel_requested = True

    def is_cancelled(self) -> bool:
        """Check if cancellation has been requested. Thread-safe."""
        with QMutexLocker(self._mutex):
            return self._cancel_requested

    def report_progress(self, percentage: int, message: str):
        """Emit progress update. Call from run_task()."""
        if not self.is_cancelled():
            self.progress.emit(percentage, message)

    def reset(self):
        """Reset cancellation state for reuse."""
        with QMutexLocker(self._mutex):
            self._cancel_requested = False


class FunctionWorker(ThreadWorker):
    """Worker that wraps an arbitrary callable function.

    Usage:
        def my_heavy_task(worker):
            for i in range(100):
                if worker.is_cancelled():
                    return None
                worker.report_progress(i, f"Processing {i}%")
                do_work(i)
            return "done"

        worker = FunctionWorker(my_heavy_task)
        worker.finished_result.connect(on_done)
        worker.start()
    """

    def __init__(self, func, *args, **kwargs):
        super().__init__()
        self._func = func
        self._args = args
        self._kwargs = kwargs

    def run_task(self):
        return self._func(self, *self._args, **self._kwargs)
