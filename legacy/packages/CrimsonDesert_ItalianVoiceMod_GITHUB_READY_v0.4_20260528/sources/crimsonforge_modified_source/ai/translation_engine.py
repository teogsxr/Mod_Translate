"""Batch translation engine with queue, rate limiting, and cost tracking.

Processes translation requests through AI providers with configurable
batch size, concurrency, and rate limiting. Tracks token usage and costs.
"""

import time
import threading
from dataclasses import dataclass, field
from typing import Optional, Callable
from enum import Enum

from ai.provider_base import AIProviderBase, TranslationResult
from ai.prompt_manager import PromptManager
from utils.logger import get_logger

logger = get_logger("ai.translation_engine")


class BatchState(Enum):
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPING = "stopping"
    COMPLETED = "completed"
    ERROR = "error"


@dataclass
class TranslationStats:
    """Accumulated translation statistics."""
    total_requests: int = 0
    successful: int = 0
    failed: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_tokens: int = 0
    total_cost: float = 0.0
    total_latency_ms: float = 0.0
    avg_latency_ms: float = 0.0


@dataclass
class TranslationRequest:
    """A single translation request in the queue."""
    index: int
    text: str
    key: str = ""
    context: str = ""


class TranslationEngine:
    """Batch translation engine with queue management."""

    def __init__(
        self,
        provider: AIProviderBase,
        prompt_manager: PromptManager,
        batch_size: int = 10,
        batch_delay_ms: int = 500,
    ):
        self._provider = provider
        self._prompt_manager = prompt_manager
        self._batch_size = batch_size
        self._batch_delay_ms = batch_delay_ms
        self._state = BatchState.IDLE
        self._stats = TranslationStats()
        self._lock = threading.Lock()
        self._pause_event = threading.Event()
        self._pause_event.set()
        self._stop_requested = False

    def set_glossary(self, glossary_text: str) -> None:
        """Set glossary text to inject into every AI prompt."""
        self._glossary_text = glossary_text

    def translate_single(
        self,
        text: str,
        source_lang: str,
        target_lang: str,
        model: str = "",
        context: str = "",
    ) -> TranslationResult:
        """Translate a single string.

        Protected-token pipeline
        ------------------------
        Pearl Abyss paloc strings contain non-prose placeholders
        (``<br/>``, ``[EMPTY]``, ``%0``, ``{Key:...}``,
        ``{Staticinfo:Knowledge:...#Korean_Label}``, etc.) that
        MUST survive translation byte-for-byte. We round-trip
        them through opaque sentinels so the AI can't mangle them:

          1. Encode every protected token as ``⟦CFn⟧`` (or a
             paired ``⟦CFn⟧label⟦/CFn⟧`` for hash-label braces
             that contain a translatable Korean label).
          2. Append a short preservation instruction to the
             system prompt.
          3. Send the encoded text + augmented prompt to the AI.
          4. Decode the returned sentinels back to the original
             tokens (for paired sentinels, the AI-translated
             label stays put and we splice it back into the
             namespace#label frame).

        The result object stores the FINAL decoded translation,
        so downstream consumers never see a sentinel.
        """
        from core.translation_tokenizer import (
            PROMPT_INSTRUCTION,
            decode_after_translation,
            encode_for_translation,
        )

        glossary = getattr(self, "_glossary_text", "")
        system_prompt = self._prompt_manager.get_system_prompt(
            source_lang, target_lang, glossary_text=glossary
        )
        # Append the sentinel-preservation rule to whatever the
        # prompt manager produced. One concise paragraph; the
        # existing prompt stays intact.
        system_prompt = system_prompt + "\n\n" + PROMPT_INSTRUCTION

        encoded_text, token_table = encode_for_translation(text)

        result = self._provider.translate(
            text=encoded_text,
            source_lang=source_lang,
            target_lang=target_lang,
            model=model,
            system_prompt=system_prompt,
            context=context,
        )
        # Restore the protected tokens in the translated string.
        # Uses a tolerant matcher that handles minor sentinel
        # noise the AI occasionally introduces (case changes,
        # stray whitespace).
        if result.success and result.translated_text:
            result = result.__class__(**{
                **result.__dict__,
                "translated_text": decode_after_translation(
                    result.translated_text, token_table,
                ),
            })

        with self._lock:
            self._stats.total_requests += 1
            if result.success:
                self._stats.successful += 1
            else:
                self._stats.failed += 1
            self._stats.total_input_tokens += result.input_tokens
            self._stats.total_output_tokens += result.output_tokens
            self._stats.total_tokens += result.total_tokens
            self._stats.total_cost += result.cost_estimate
            self._stats.total_latency_ms += result.latency_ms
            if self._stats.successful > 0:
                self._stats.avg_latency_ms = (
                    self._stats.total_latency_ms / self._stats.successful
                )

        return result

    def translate_batch(
        self,
        requests: list[TranslationRequest],
        source_lang: str,
        target_lang: str,
        model: str = "",
        progress_callback: Optional[Callable[[int, int, TranslationResult], None]] = None,
    ) -> list[TranslationResult]:
        """Translate a batch of strings with progress reporting.

        Args:
            requests: List of translation requests.
            source_lang: Source language.
            target_lang: Target language.
            model: Model to use.
            progress_callback: Optional callback(completed, total, last_result).

        Returns:
            List of TranslationResult, one per request.
        """
        self._state = BatchState.RUNNING
        self._stop_requested = False
        results = []
        total = len(requests)

        for i, req in enumerate(requests):
            self._pause_event.wait()

            if self._stop_requested:
                self._state = BatchState.IDLE
                break

            result = self.translate_single(
                text=req.text,
                source_lang=source_lang,
                target_lang=target_lang,
                model=model,
                context=req.context,
            )
            results.append(result)

            if progress_callback:
                progress_callback(i + 1, total, result)

            if i < total - 1 and self._batch_delay_ms > 0:
                time.sleep(self._batch_delay_ms / 1000.0)

        if not self._stop_requested:
            self._state = BatchState.COMPLETED

        return results

    def pause(self) -> None:
        """Pause batch translation."""
        self._pause_event.clear()
        self._state = BatchState.PAUSED
        logger.info("Translation paused")

    def resume(self) -> None:
        """Resume batch translation."""
        self._pause_event.set()
        self._state = BatchState.RUNNING
        logger.info("Translation resumed")

    def stop(self) -> None:
        """Stop batch translation."""
        self._stop_requested = True
        self._pause_event.set()
        self._state = BatchState.STOPPING
        logger.info("Translation stop requested")

    @property
    def state(self) -> BatchState:
        return self._state

    @property
    def stats(self) -> TranslationStats:
        with self._lock:
            return TranslationStats(
                total_requests=self._stats.total_requests,
                successful=self._stats.successful,
                failed=self._stats.failed,
                total_input_tokens=self._stats.total_input_tokens,
                total_output_tokens=self._stats.total_output_tokens,
                total_tokens=self._stats.total_tokens,
                total_cost=self._stats.total_cost,
                total_latency_ms=self._stats.total_latency_ms,
                avg_latency_ms=self._stats.avg_latency_ms,
            )

    def reset_stats(self) -> None:
        with self._lock:
            self._stats = TranslationStats()

    def set_provider(self, provider: AIProviderBase) -> None:
        self._provider = provider

    def set_batch_config(self, batch_size: int = 0, batch_delay_ms: int = 0) -> None:
        if batch_size > 0:
            self._batch_size = batch_size
        if batch_delay_ms >= 0:
            self._batch_delay_ms = batch_delay_ms
