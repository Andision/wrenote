"""whisper.cpp STT backend, via the pywhispercpp Python binding.

Per design.v1.1 §3.2 / §4.2.1. P1-a implementation: ``transcribe_segment``
runs the full segment once and returns a ``final`` event — no streaming
``partial`` callbacks yet (P1-c upgrade).
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from ..core.events import AudioSegment, BackendInfo, TranscriptEvent
from ..core.lang import LanguagePolicy, choose_language
from ..core.registry import register_stt
from .base import PartialCallback, STTBackend

if TYPE_CHECKING:
    from pywhispercpp.model import Model

log = logging.getLogger(__name__)

MIN_AUDIO_SAMPLES = 16_000 // 2  # below ~0.5 s, Whisper output is unreliable

# whisper.cpp's lang-id probabilities on <2s windows are noisy; below this
# threshold we ignore the guess and fall back to the previously-detected
# language (or "en" on first segment). 0.5 was chosen empirically from the
# smoke test (Tingting/Samantha clips): clean single-language clips score
# 0.99+, while concatenated bilingual audio scored 0.57 on the wrong half.
LANG_CONFIDENCE_THRESHOLD = 0.5


def _normalize_lang(lang: str | None) -> str | None:
    """Normalize 'auto' / None / '' to None (auto-detect signal)."""
    if lang is None:
        return None
    s = lang.strip().lower()
    if s in ("", "auto"):
        return None
    return s


def compose_prompt(*, context: str, glossary: str) -> str:
    """The decoder prompt for one segment: what was just said, then the glossary.

    Whisper trims a long prompt from the front, so the order is what gets
    dropped first: the older context goes before the glossary the user wrote.
    Both are already capped by their producers (context_tail, stt_initial_prompt).
    """
    parts = [p.strip() for p in (context, glossary) if p and p.strip()]
    return " ".join(parts)


@register_stt("whisper_cpp")
class WhisperCppBackend(STTBackend):
    def __init__(
        self,
        *,
        model_path: str,
        device: str = "auto",
        language: str | None = None,
        n_threads: int | None = None,
    ) -> None:
        self._model_path = str(Path(model_path).expanduser().resolve())
        self._device = device  # informational; whisper.cpp auto-detects (Metal on Mac)
        # None / "auto" / "" all mean: run language detection per segment.
        self._language = _normalize_lang(language)
        self._n_threads = n_threads or max(1, (os.cpu_count() or 4) - 1)
        self._model: Model | None = None
        # Optional glossary bias (core.glossary); prepended to decoding context.
        self._initial_prompt: str = ""
        # Sticky fallback for short / low-confidence segments. Set after the
        # first segment that detects with confidence >= threshold.
        self._last_detected_lang: str | None = None
        # The session's languages (core.lang); None = the caller's src_lang
        # alone decides, as before.
        self._policy: LanguagePolicy | None = None
        # whisper.cpp + Metal is not thread-safe. asyncio.to_thread's default
        # executor spawns multiple workers, which can race even with a
        # threading.Lock (cancelled-but-still-running threads, GC of return
        # values, etc.). A single dedicated worker thread eliminates the
        # whole class of bugs: every C call runs on this one thread, in order.
        # Made in load(), so a backend can be loaded again after unload().
        self._executor: concurrent.futures.ThreadPoolExecutor | None = None

    async def load(self) -> None:
        if self._model is not None:
            return
        path = Path(self._model_path)
        if not path.exists():
            raise FileNotFoundError(f"Whisper model not found at {self._model_path}")
        def _load() -> Model:
            # Imported here, not at module top: the native binding is part of
            # the (swappable) compute runtime and must not be a hard import
            # dependency of the engine package. See core/runtimes.py.
            from pywhispercpp.model import Model

            return Model(
                self._model_path,
                n_threads=self._n_threads,
                print_realtime=False,
                print_progress=False,
                print_timestamps=False,
            )

        log.info("Loading whisper.cpp model from %s (n_threads=%d)", self._model_path, self._n_threads)
        loop = asyncio.get_event_loop()
        self._model = await loop.run_in_executor(self._worker, _load)
        log.info("Whisper model loaded")

    async def unload(self) -> None:
        # Drop the model reference inside the worker thread so any cleanup
        # touches whisper.cpp from the same thread that allocated it.
        def _drop() -> None:
            self._model = None
        ex, self._executor = self._executor, None
        if self._model is not None and ex is not None:
            try:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(ex, _drop)
            except RuntimeError:
                self._model = None
        if ex is not None:
            ex.shutdown(wait=False)

    async def transcribe_segment(
        self,
        segment: AudioSegment,
        *,
        src_lang: str | None = None,
        on_partial: PartialCallback | None = None,
        context: str = "",
    ) -> TranscriptEvent:
        if self._model is None:
            raise RuntimeError("WhisperCppBackend not loaded; call load() first")

        # int16 PCM bytes → float32 numpy in [-1, 1]
        audio = np.frombuffer(segment.pcm, dtype=np.int16).astype(np.float32) / 32768.0

        # Resolve the language to feed Whisper. Four cases:
        #   1. A session policy with secondary languages → detect among the
        #      languages it allows; a secondary one needs the policy's
        #      confidence to win over the main one.
        #   2. Caller pinned a language ("en", "zh", …) → use it as-is.
        #   3. Caller said auto (None/"auto") AND we have a stuck-on backend
        #      language from config → use that.
        #   4. Otherwise auto-detect on this audio, with sticky fallback for
        #      low-confidence short segments.
        requested = _normalize_lang(src_lang)
        policy = self._policy
        confidence: float | None
        if policy is not None and policy.main is not None and policy.secondary:
            lang, confidence = await self._detect_language(audio, policy)
        elif requested is not None:
            lang = requested
            confidence = None
        elif self._language is not None:
            lang = self._language
            confidence = None
        else:
            lang, confidence = await self._detect_language(audio, None)

        if audio.size < MIN_AUDIO_SAMPLES:
            # Too short to transcribe reliably; return empty final. Don't
            # update _last_detected_lang here — short windows are exactly
            # the case where the lang-id distribution is too flat to trust.
            return TranscriptEvent(
                type="final",
                segment_id=segment.segment_id,
                text="",
                lang=lang,
                t0=segment.t0,
                t1=segment.t1,
                confidence=confidence,
            )

        prompt = compose_prompt(context=context, glossary=self._initial_prompt)

        def _transcribe() -> list:
            assert self._model is not None
            kwargs: dict[str, object] = {"language": lang}
            if prompt:
                kwargs["initial_prompt"] = prompt
            return list(self._model.transcribe(audio, **kwargs))

        # All transcribe calls funnel through the single-threaded executor,
        # so partial / final / next-segment-partial run strictly serially —
        # no concurrent access to the Model or its Metal context.
        loop = asyncio.get_event_loop()
        segments = await loop.run_in_executor(self._worker, _transcribe)
        text = " ".join(s.text.strip() for s in segments if s.text.strip())

        return TranscriptEvent(
            type="final",
            segment_id=segment.segment_id,
            text=text,
            lang=lang,
            t0=segment.t0,
            t1=segment.t1,
            confidence=confidence,
        )

    async def _detect_language(
        self, audio: np.ndarray, policy: LanguagePolicy | None
    ) -> tuple[str, float]:
        """Auto-detect language. Returns (lang, confidence).

        With a policy: the choice is made from Whisper's whole distribution
        by :func:`choose_language`, and a short or failed detection is the
        main language. Without one: short / low-confidence segments fall
        back to the most recently confidently-detected language (or "en" on
        cold start), to avoid the UI flipping between languages on every
        clipped utterance.
        """
        assert self._model is not None
        fallback = policy.main if policy is not None and policy.main else (self._last_detected_lang or "en")

        if audio.size < MIN_AUDIO_SAMPLES:
            return (fallback, 0.0)

        def _detect() -> tuple[str, float, dict[str, float]]:
            assert self._model is not None
            (lang, prob), all_probs = self._model.auto_detect_language(audio)
            return (lang, float(prob), {k: float(v) for k, v in dict(all_probs).items()})

        loop = asyncio.get_event_loop()
        try:
            lang, prob, all_probs = await loop.run_in_executor(self._worker, _detect)
        except Exception:
            log.exception("auto_detect_language failed; falling back")
            return (fallback, 0.0)

        if policy is not None and policy.main is not None:
            chosen, score = choose_language(all_probs, policy)
            log.debug("lang policy: whisper said %s (p=%.2f) → %s (p=%.2f)", lang, prob, chosen, score)
            return (chosen, score)

        if prob >= LANG_CONFIDENCE_THRESHOLD:
            self._last_detected_lang = lang
            return (lang, prob)

        # Low-confidence: use sticky fallback. Keep prob so callers can log it.
        fallback = self._last_detected_lang or "en"
        log.debug(
            "low-confidence lang detect: got %s (p=%.2f); falling back to %s",
            lang, prob, fallback,
        )
        return (fallback, prob)

    @property
    def _worker(self) -> concurrent.futures.ThreadPoolExecutor:
        if self._executor is None:
            self._executor = concurrent.futures.ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="whisper"
            )
        return self._executor

    def set_initial_prompt(self, prompt: str) -> None:
        self._initial_prompt = prompt or ""

    def set_language_policy(self, policy: LanguagePolicy) -> None:
        self._policy = policy

    @property
    def info(self) -> BackendInfo:
        return BackendInfo(
            name="whisper_cpp",
            version="pywhispercpp-1.4.1",
            model=Path(self._model_path).stem,
            device=self._device,
            supported_languages=[
                "en", "zh", "ja", "ko", "es", "fr", "de", "it",
                "ru", "pt", "tr", "pl", "nl", "ar", "hi", "vi",
            ],
        )
