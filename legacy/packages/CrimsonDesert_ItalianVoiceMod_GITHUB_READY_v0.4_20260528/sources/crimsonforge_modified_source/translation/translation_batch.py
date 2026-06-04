"""Batch translation processing with queue, progress, pause, and resume.

Wraps the AI translation engine with project-level state management,
connecting AI results to TranslationEntry state machine updates.
"""

from typing import Optional, Callable

from ai.translation_engine import TranslationEngine, TranslationRequest, BatchState
from ai.provider_base import TranslationResult
from translation.translation_state import TranslationEntry, StringStatus
from translation.translation_project import TranslationProject
from utils.logger import get_logger

logger = get_logger("translation.batch")


class TranslationBatchProcessor:
    """Processes batch translations, updating project entries with results."""

    def __init__(self, engine: TranslationEngine, project: TranslationProject):
        self._engine = engine
        self._project = project

    def translate_all_pending(
        self,
        model: str = "",
        progress_callback: Optional[Callable[[int, int, Optional[TranslationResult]], None]] = None,
    ) -> list[TranslationResult]:
        """Translate all pending entries in the project.

        Args:
            model: AI model to use.
            progress_callback: callback(completed, total, last_result).

        Returns:
            List of TranslationResult objects.
        """
        pending = self._project.get_pending_entries()
        if not pending:
            logger.info("No pending entries to translate")
            return []

        requests = [
            TranslationRequest(
                index=entry.index,
                text=entry.original_text,
                key=entry.key,
            )
            for entry in pending
        ]

        def on_progress(completed: int, total: int, result: TranslationResult):
            if result.success:
                entry = pending[completed - 1]
                entry.set_translated(
                    text=result.translated_text,
                    provider=result.provider,
                    model=result.model_used,
                    tokens=result.total_tokens,
                    cost=result.cost_estimate,
                )
                self._project.mark_modified()

            if progress_callback:
                progress_callback(completed, total, result)

        results = self._engine.translate_batch(
            requests=requests,
            source_lang=self._project.source_lang,
            target_lang=self._project.target_lang,
            model=model,
            progress_callback=on_progress,
        )

        return results

    def translate_entries(
        self,
        entries: list[TranslationEntry],
        model: str = "",
        progress_callback: Optional[Callable[[int, int, Optional[TranslationResult]], None]] = None,
    ) -> list[TranslationResult]:
        """Translate a specific list of entries (for batch-selected AI translate).

        Args:
            entries: List of TranslationEntry objects to translate.
            model: AI model to use.
            progress_callback: callback(completed, total, last_result).

        Returns:
            List of TranslationResult objects.
        """
        if not entries:
            return []

        requests = [
            TranslationRequest(
                index=entry.index,
                text=entry.original_text,
                key=entry.key,
            )
            for entry in entries
        ]

        def on_progress(completed: int, total: int, result: TranslationResult):
            if result.success:
                entry = entries[completed - 1]
                entry.set_translated(
                    text=result.translated_text,
                    provider=result.provider,
                    model=result.model_used,
                    tokens=result.total_tokens,
                    cost=result.cost_estimate,
                )
                self._project.mark_modified()

            if progress_callback:
                progress_callback(completed, total, result)

        results = self._engine.translate_batch(
            requests=requests,
            source_lang=self._project.source_lang,
            target_lang=self._project.target_lang,
            model=model,
            progress_callback=on_progress,
        )

        return results

    def translate_single_entry(
        self,
        entry: TranslationEntry,
        model: str = "",
        context: str = "",
    ) -> TranslationResult:
        """Translate a single entry using AI."""
        result = self._engine.translate_single(
            text=entry.original_text,
            source_lang=self._project.source_lang,
            target_lang=self._project.target_lang,
            model=model,
            context=context,
        )

        if result.success:
            entry.set_translated(
                text=result.translated_text,
                provider=result.provider,
                model=result.model_used,
                tokens=result.total_tokens,
                cost=result.cost_estimate,
            )
            self._project.mark_modified()

        return result

    def pause(self) -> None:
        self._engine.pause()

    def resume(self) -> None:
        self._engine.resume()

    def stop(self) -> None:
        self._engine.stop()

    @property
    def state(self) -> BatchState:
        return self._engine.state

    @property
    def stats(self):
        return self._engine.stats
