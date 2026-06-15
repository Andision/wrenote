"""Pipeline orchestrator.

Per design.v1.1 §4.4. Coordinates audio flow:

    audio_in → vad_loop → stt_jobs → stt_loop → final_events → translation_loop
                  ↓                       ↓                       ↓
                  └───────────────── client_events ───────────────┘

Queue policies per design.v1.1 §4.4.1:

* ``audio_in`` (200 max): drop oldest on overflow (real-time wins; old frames are stale)
* ``stt_jobs`` (10 max): block; segments must not be lost
* ``final_events`` (20 max): block; finals must not be lost
* ``client_events`` (500 max): drop new partial on overflow; block for other events
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import numpy as np

from ..speaker.base import SpeakerBackend
from ..stt.base import STTBackend
from ..translator.base import TranslatorBackend
from ..vad.base import VADBackend
from .events import (
    AudioChunk,
    AudioSegment,
    ClientEvent,
    ErrorEvent,
    TranscriptEvent,
    TranslationEvent,
    VADEvent,
)
from .lang import text_lang_override

log = logging.getLogger(__name__)


@dataclass
class SessionParams:
    """Runtime parameters for one pipeline session."""

    # "auto" → STT detects the source language per segment. The translator
    # then receives the per-segment detected lang (see _translation_loop),
    # so a session with mixed languages routes each segment correctly. When
    # a detected lang equals tgt_lang, translation is skipped entirely.
    src_lang: str = "auto"
    tgt_lang: str = "zh"
    # When False, the translator backend is never loaded and every final
    # gets a `skipped=True` TranslationEvent so the UI hides the row. Use
    # this for "transcribe-only" sessions (no translator GPU/CPU cost).
    translate_enabled: bool = True
    # Default 800ms: shorter values (~500ms) tend to cut natural English
    # speech mid-sentence because speakers pause briefly between phrases.
    # 800-1000ms catches sentence-end pauses cleanly. Set lower for slow
    # speakers / longer for rapid-fire conversation.
    min_silence_ms: int = 800
    # If the latest partial transcript does NOT end with sentence-ending
    # punctuation (.!?), multiply ``min_silence_ms`` by this factor before
    # closing the segment. The intent: if Whisper hasn't seen a sentence
    # boundary yet, the speaker is probably mid-thought — give them more
    # room. 2.25 × 800ms = 1800ms catches typical breathing / word-search
    # pauses without crossing into "feels laggy" territory (~2.5s+).
    # Set to 1.0 to disable adaptive behaviour.
    extended_silence_factor: float = 2.25
    max_segment_ms: int = 25000  # Whisper context limit is 30s; leave a buffer
    # Growing-buffer partial cadence: re-transcribe the current segment buffer
    # every N ms while the segment is still open. Set to 0 to disable partials.
    partial_interval_ms: int = 800
    # Don't emit a partial until the buffer has at least this much audio.
    partial_min_audio_ms: int = 500
    # If True, also translate partial transcripts as they come in (best-effort).
    # The latest partial per segment wins; translation arrives a beat after
    # the transcript update. Set False to translate only finals.
    translate_partials: bool = True
    # Speaker-identification (online diarization) parameters. Only effective
    # if the pipeline was constructed with a SpeakerBackend.
    speaker_threshold: float = 0.65         # cosine-distance below this → known speaker
    speaker_min_audio_ms: int = 1000        # segments shorter than this skip clustering
    speaker_enabled: bool = True


async def _put_drop_oldest(q: asyncio.Queue[Any], item: Any) -> None:
    """Put item, dropping the oldest item if the queue is full."""
    if q.full():
        try:
            q.get_nowait()
        except asyncio.QueueEmpty:
            pass
    await q.put(item)


class Pipeline:
    """Orchestrate audio → VAD → STT → translator → client events.

    Lifecycle: ``build → start() → feed_audio()/client_event_stream() → stop()``.
    All public coroutines are safe to call from any task; the loops run as
    background asyncio tasks created in :meth:`start`.
    """

    def __init__(
        self,
        stt: STTBackend,
        vad: VADBackend,
        translator: TranslatorBackend,
        params: SessionParams,
        speaker: SpeakerBackend | None = None,
    ) -> None:
        self.stt = stt
        self.vad = vad
        self.translator = translator
        self.speaker = speaker  # None disables speaker identification
        self.params = params
        # Online-clustering state. Each entry of _speaker_centroids is the
        # running-mean embedding for "Speaker N+1" (1-indexed).
        self._speaker_centroids: list[np.ndarray] = []
        self._speaker_counts: list[int] = []

        self.audio_in: asyncio.Queue[AudioChunk] = asyncio.Queue(maxsize=200)
        self.stt_jobs: asyncio.Queue[AudioSegment] = asyncio.Queue(maxsize=10)
        self.final_events: asyncio.Queue[TranscriptEvent] = asyncio.Queue(maxsize=20)
        self.client_events: asyncio.Queue[ClientEvent] = asyncio.Queue(maxsize=500)

        self._tasks: list[asyncio.Task[None]] = []
        self._vad_task: asyncio.Task[None] | None = None
        self._next_seq = 0
        self._started = False
        # Set at the end of start(); all timestamps emitted downstream are
        # relative to this (so the UI sees 0.0s, 1.2s, … instead of the
        # monotonic clock value which is whatever the OS uptime happens to be).
        self._session_start_ts: float = time.monotonic()
        # Signals graceful shutdown: vad_loop sees this, closes any open
        # segment as a final, and exits; stt + translation loops drain.
        self._stopping = asyncio.Event()
        # One-shot signal: close any in-flight VAD segment immediately
        # (used by WS "pause" so pre-pause speech finalizes right away
        # instead of waiting on the silence timeout that will never fire
        # while the client isn't sending PCM).
        self._force_close_segment = asyncio.Event()
        # In-flight job counters so flush() can wait for actual completion
        # (queue empty alone is not sufficient — the worker may still be
        # processing the job it took out).
        self._stt_inflight = 0
        self._trans_inflight = 0
        # Partial-translation state: latest partial (text, detected_lang) per
        # active segment. Lang is per-segment so that auto-detect sessions
        # route each segment to the right source. _partial_translation_event
        # wakes the loop when new work arrives. _finalized_segments lets the
        # partial translator skip segments that already have a final in
        # flight (avoids a stale partial overwriting the final translation
        # in the UI).
        self._partial_jobs: dict[str, tuple[str, str]] = {}
        self._partial_translation_event = asyncio.Event()
        self._finalized_segments: set[str] = set()
        self._partial_trans_inflight = 0
        # Latest partial transcript per active segment, used by the VAD loop
        # to adapt the silence threshold (we wait longer to close a segment
        # whose partial doesn't end on a sentence boundary).
        self._latest_partial_text: dict[str, str] = {}

    # ---------- Public lifecycle ----------

    async def start(self) -> None:
        """Load backends and spawn internal loop tasks. Idempotent."""
        if self._started:
            return
        loads = [self.stt.load(), self.vad.load()]
        if self.params.translate_enabled:
            loads.append(self.translator.load())
        if self.speaker is not None and self.params.speaker_enabled:
            loads.append(self.speaker.load())
        await asyncio.gather(*loads)
        # Reset session clock now that backends are loaded — timestamps in
        # client-facing events start from 0 here, not from process start.
        self._session_start_ts = time.monotonic()
        self._vad_task = asyncio.create_task(self._vad_loop(), name="pipeline.vad")
        self._tasks = [
            self._vad_task,
            asyncio.create_task(self._stt_loop(), name="pipeline.stt"),
            asyncio.create_task(self._translation_loop(), name="pipeline.translator"),
        ]
        if self.params.translate_partials:
            self._tasks.append(
                asyncio.create_task(
                    self._partial_translation_loop(), name="pipeline.partial-trans"
                )
            )
        self._started = True
        log.info(
            "Pipeline started: stt=%s vad=%s translator=%s %s→%s",
            self.stt.info.name,
            self.vad.info.name,
            self.translator.info.name,
            self.params.src_lang,
            self.params.tgt_lang,
        )

    async def flush(self, timeout: float = 15.0) -> None:
        """Graceful drain: signal vad_loop to close any in-flight segment, then
        wait for STT + translation to drain. Should be awaited before stop().

        Safe to call multiple times; subsequent calls are no-ops once drained.
        """
        if not self._started:
            return
        self._stopping.set()

        deadline = asyncio.get_event_loop().time() + timeout

        # 1. Wait for vad_loop to exit (it'll flush any open segment first)
        if self._vad_task is not None and not self._vad_task.done():
            try:
                remaining = max(0.1, deadline - asyncio.get_event_loop().time())
                await asyncio.wait_for(asyncio.shield(self._vad_task), timeout=remaining)
            except asyncio.TimeoutError:
                log.warning("flush: vad_loop did not exit within timeout")

        # 2. Wait for stt + translation pipelines to fully idle (queue empty
        #    AND no in-flight work). Partial-translation loop is best-effort;
        #    its in-flight requests are also drained to avoid orphaned events.
        while asyncio.get_event_loop().time() < deadline:
            if (
                self.stt_jobs.empty()
                and self.final_events.empty()
                and self._stt_inflight == 0
                and self._trans_inflight == 0
                and self._partial_trans_inflight == 0
            ):
                break
            await asyncio.sleep(0.05)
        else:
            log.warning(
                "flush: did not drain in time (stt_q=%d stt_busy=%d final_q=%d trans_busy=%d ptrans_busy=%d)",
                self.stt_jobs.qsize(), self._stt_inflight,
                self.final_events.qsize(), self._trans_inflight,
                self._partial_trans_inflight,
            )

    async def stop(self) -> None:
        """Cancel internal tasks and unload backends.

        For graceful shutdown that flushes in-flight work first, call
        :meth:`flush` before this.
        """
        self._stopping.set()
        for t in self._tasks:
            t.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []
        self._vad_task = None
        unloads = [self.stt.unload()]
        if self.params.translate_enabled:
            unloads.append(self.translator.unload())
        if self.speaker is not None and self.params.speaker_enabled:
            unloads.append(self.speaker.unload())
        await asyncio.gather(*unloads, return_exceptions=True)
        self._started = False
        log.info("Pipeline stopped")

    async def close_open_segment(self) -> None:
        """Force-close any in-flight VAD segment, if one is open.

        Idempotent and safe to call when nothing is open (the VAD loop
        checks for an active segment before flushing). Used by the WS
        ``pause`` handler so the user sees their pre-pause utterance as
        a final segment immediately instead of as a stuck partial.
        """
        if self._started:
            self._force_close_segment.set()

    async def feed_audio(self, pcm: bytes, sample_rate: int = 16000) -> None:
        """Push a PCM frame into the pipeline. Server assigns seq + ts.

        ``server_ts`` is **session-relative** (seconds since :meth:`start`
        returned), so all downstream events read nice human-readable times.
        """
        chunk = AudioChunk(
            pcm=pcm,
            sample_rate=sample_rate,
            seq=self._next_seq,
            server_ts=time.monotonic() - self._session_start_ts,
        )
        self._next_seq += 1
        await _put_drop_oldest(self.audio_in, chunk)

    async def client_event_stream(self) -> AsyncIterator[ClientEvent]:
        """Yield events from ``client_events`` until cancelled."""
        while True:
            yield await self.client_events.get()

    def queue_status(self) -> dict[str, int]:
        return {
            "audio_in": self.audio_in.qsize(),
            "stt_jobs": self.stt_jobs.qsize(),
            "final_events": self.final_events.qsize(),
            "client_events": self.client_events.qsize(),
        }

    # ---------- Internal helpers ----------

    async def _identify_speaker(self, segment: AudioSegment) -> str | None:
        """Run embedding + online clustering, return a speaker label.

        Returns:
            * ``"Speaker N"`` for a known or new speaker
            * ``"unknown"`` for segments too short to embed reliably
            * ``None`` if speaker identification is disabled or failed
        """
        if self.speaker is None or not self.params.speaker_enabled:
            return None

        duration_ms = (segment.t1 - segment.t0) * 1000.0
        if duration_ms < self.params.speaker_min_audio_ms:
            return "unknown"

        try:
            emb = await self.speaker.embed(segment.pcm)
        except Exception:
            log.exception("speaker embedding failed for %s", segment.segment_id)
            return None

        if emb is None or emb.size == 0 or not np.isfinite(emb).all():
            return None
        if np.linalg.norm(emb) < 1e-6:
            return None

        # First speaker, no centroids yet.
        if not self._speaker_centroids:
            self._speaker_centroids.append(emb.copy())
            self._speaker_counts.append(1)
            return "Speaker 1"

        # Cosine distance to each existing centroid.
        emb_norm = emb / (np.linalg.norm(emb) + 1e-9)
        distances = []
        for c in self._speaker_centroids:
            c_norm = c / (np.linalg.norm(c) + 1e-9)
            distances.append(float(1.0 - np.dot(emb_norm, c_norm)))

        best_idx = int(np.argmin(distances))
        best_dist = distances[best_idx]

        if best_dist < self.params.speaker_threshold:
            # Update centroid with running mean of new embedding.
            self._speaker_counts[best_idx] += 1
            n = self._speaker_counts[best_idx]
            self._speaker_centroids[best_idx] += (emb - self._speaker_centroids[best_idx]) / n
            label = f"Speaker {best_idx + 1}"
            log.debug("segment %s → %s (dist=%.3f)", segment.segment_id, label, best_dist)
            return label

        # All centroids are too far → new speaker.
        self._speaker_centroids.append(emb.copy())
        self._speaker_counts.append(1)
        label = f"Speaker {len(self._speaker_centroids)}"
        log.info(
            "segment %s → new %s (best existing dist=%.3f >= threshold=%.2f)",
            segment.segment_id, label, best_dist, self.params.speaker_threshold,
        )
        return label

    async def _emit_client(self, event: ClientEvent) -> None:
        """Push to client_events. If queue is full and event is a partial, drop it."""
        if self.client_events.full() and isinstance(event, TranscriptEvent) and event.type == "partial":
            return
        await self.client_events.put(event)

    async def _close_segment(
        self,
        segment_id: str,
        buffer: bytearray,
        t0: float,
        t1: float,
    ) -> None:
        seg = AudioSegment(
            segment_id=segment_id,
            pcm=bytes(buffer),
            t0=t0,
            t1=t1,
        )
        await self.stt_jobs.put(seg)  # block on full: never drop a segment
        await self._emit_client(VADEvent(type="speech_end", segment_id=segment_id, ts=t1))

    async def _cancel_partial_task(self, task: asyncio.Task[None] | None) -> None:
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    # ---------- Loops ----------

    async def _wait_audio_or_stop(self) -> AudioChunk | None | str:
        """Wait for the next chunk, the stopping signal, or a force-close signal.

        Returns:
            * AudioChunk on normal data path
            * None if shutdown was requested
            * "force_close" if the caller asked for an in-flight segment to be flushed
        """
        if self._stopping.is_set() and self.audio_in.empty():
            return None
        get_task = asyncio.create_task(self.audio_in.get())
        stop_task = asyncio.create_task(self._stopping.wait())
        flush_task = asyncio.create_task(self._force_close_segment.wait())
        try:
            done, pending = await asyncio.wait(
                {get_task, stop_task, flush_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
        except asyncio.CancelledError:
            for t in (get_task, stop_task, flush_task):
                t.cancel()
            raise
        for t in pending:
            t.cancel()
        if get_task in done:
            return get_task.result()
        if flush_task in done:
            self._force_close_segment.clear()
            # Drain a queued chunk if any was racing, then return the sentinel
            # so the caller flushes its open segment.
            return "force_close"
        # Stopping fired; drain any chunk that might be sitting in the queue.
        try:
            return self.audio_in.get_nowait()
        except asyncio.QueueEmpty:
            return None

    async def _vad_loop(self) -> None:
        """Consume audio frames, maintain segment state machine, push to stt_jobs.

        For each active segment we spawn a background ``_partial_loop`` task
        that periodically re-transcribes the growing buffer as partial events.
        It is cancelled when the segment is closed (so the final transcription
        owns the last word for that segment).

        Graceful shutdown: when ``_stopping`` is set, any open segment is
        flushed as a final (so the user does not lose the last in-flight
        utterance), then this loop exits.
        """
        current_segment_id: str | None = None
        buffer: bytearray = bytearray()
        seg_t0 = 0.0
        silence_start_ts: float | None = None
        partial_task: asyncio.Task[None] | None = None

        async def _flush_open_segment() -> None:
            nonlocal current_segment_id, buffer, partial_task, silence_start_ts
            if current_segment_id is None:
                return
            await self._cancel_partial_task(partial_task)
            partial_task = None
            t_end = seg_t0 + len(buffer) / 2 / 16000  # bytes → samples → seconds
            log.info("flush: closing in-flight segment %s (%.2fs)", current_segment_id, t_end - seg_t0)
            self._latest_partial_text.pop(current_segment_id, None)
            await self._close_segment(current_segment_id, buffer, seg_t0, t_end)
            current_segment_id = None
            buffer = bytearray()
            silence_start_ts = None

        def _silence_threshold_for(segment_id: str) -> float:
            """Return the silence-ms threshold to use for closing this segment.

            If the latest partial transcript doesn't end with sentence-ending
            punctuation, extend the threshold (the speaker is mid-sentence).
            """
            base = self.params.min_silence_ms
            partial = self._latest_partial_text.get(segment_id, "").rstrip()
            if not partial:
                return base  # no info yet
            if partial.endswith((".", "!", "?", "。", "！", "？", "…")):
                return base
            return base * self.params.extended_silence_factor

        while True:
            try:
                chunk = await self._wait_audio_or_stop()
            except asyncio.CancelledError:
                await self._cancel_partial_task(partial_task)
                return

            if chunk is None:
                # Stopping: flush in-flight, exit.
                await _flush_open_segment()
                return

            if chunk == "force_close":
                # Pause requested by client — finalize whatever's open now
                # so the user sees it as a final, not a stale partial.
                await _flush_open_segment()
                continue

            try:
                speaking = await self.vad.is_speech(chunk)
            except Exception:
                log.exception("VAD error on chunk seq=%d", chunk.seq)
                continue

            now = chunk.server_ts

            if speaking:
                silence_start_ts = None
                if current_segment_id is None:
                    current_segment_id = str(uuid.uuid4())
                    buffer = bytearray(chunk.pcm)
                    seg_t0 = now
                    await self._emit_client(
                        VADEvent(type="speech_start", segment_id=current_segment_id, ts=now)
                    )
                    if self.params.partial_interval_ms > 0:
                        partial_task = asyncio.create_task(
                            self._partial_loop(current_segment_id, buffer, seg_t0),
                            name=f"pipeline.partial.{current_segment_id[:8]}",
                        )
                else:
                    buffer.extend(chunk.pcm)
                    if (now - seg_t0) * 1000 >= self.params.max_segment_ms:
                        log.info("Force-closing segment %s at max length", current_segment_id)
                        await self._cancel_partial_task(partial_task)
                        partial_task = None
                        self._latest_partial_text.pop(current_segment_id, None)
                        await self._close_segment(current_segment_id, buffer, seg_t0, now)
                        current_segment_id = None
                        buffer = bytearray()
                continue

            # Not speaking
            if current_segment_id is None:
                continue
            buffer.extend(chunk.pcm)  # include trailing silence frame
            if silence_start_ts is None:
                silence_start_ts = now
                continue
            threshold_ms = _silence_threshold_for(current_segment_id)
            if (now - silence_start_ts) * 1000 >= threshold_ms:
                log.info(
                    "Closing segment %s on silence (%.0fms, threshold=%.0fms)",
                    current_segment_id, (now - silence_start_ts) * 1000, threshold_ms,
                )
                await self._cancel_partial_task(partial_task)
                partial_task = None
                self._latest_partial_text.pop(current_segment_id, None)
                await self._close_segment(current_segment_id, buffer, seg_t0, now)
                current_segment_id = None
                buffer = bytearray()
                silence_start_ts = None

    async def _partial_loop(
        self,
        segment_id: str,
        buffer_ref: bytearray,
        t0: float,
    ) -> None:
        """Periodically transcribe the current segment buffer as partial events.

        Reads ``buffer_ref`` (the same bytearray that ``_vad_loop`` mutates) and
        snapshots it before each transcription. Cancellation is the normal exit
        path — when the segment closes, ``_vad_loop`` cancels us so the final
        transcription owns the segment id.
        """
        interval_s = self.params.partial_interval_ms / 1000.0
        min_bytes = self.params.partial_min_audio_ms * 16  # 16kHz int16 → 32 bytes/ms / 2 channels=1 → 16
        last_emitted_text: str | None = None
        try:
            while True:
                await asyncio.sleep(interval_s)
                # Snapshot is atomic — bytes() copies in one sync op
                pcm = bytes(buffer_ref)
                if len(pcm) < min_bytes:
                    continue
                t_now = t0 + len(pcm) / 2 / 16000
                snapshot = AudioSegment(
                    segment_id=segment_id, pcm=pcm, t0=t0, t1=t_now,
                )
                try:
                    event = await self.stt.transcribe_segment(
                        snapshot, src_lang=self.params.src_lang
                    )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("partial transcription failed for %s", segment_id)
                    continue

                text = (event.text or "").strip()
                if not text or text == last_emitted_text:
                    continue
                last_emitted_text = text

                # Track latest partial text for adaptive silence threshold in VAD loop.
                self._latest_partial_text[segment_id] = text

                await self._emit_client(
                    TranscriptEvent(
                        type="partial",
                        segment_id=segment_id,
                        text=text,
                        lang=event.lang,
                        t0=t0,
                        t1=t_now,
                    )
                )

                # Queue partial for translation unless: transcribe-only mode,
                # detected lang already equals target, or this segment was
                # finalized while we were transcribing.
                effective_src = text_lang_override(
                    text, audio_lang=event.lang, tgt_lang=self.params.tgt_lang,
                )
                if (
                    self.params.translate_enabled
                    and self.params.translate_partials
                    and effective_src != self.params.tgt_lang
                    and segment_id not in self._finalized_segments
                ):
                    self._partial_jobs[segment_id] = (text, effective_src)
                    self._partial_translation_event.set()
        except asyncio.CancelledError:
            return

    async def _stt_loop(self) -> None:
        """Transcribe each segment; fan out to client and (if src lang) translation loop."""
        while True:
            try:
                seg = await self.stt_jobs.get()
            except asyncio.CancelledError:
                return

            self._stt_inflight += 1
            try:
                async def emit_partial(ev: TranscriptEvent) -> None:
                    await self._emit_client(ev)

                try:
                    final = await self.stt.transcribe_segment(
                        seg,
                        src_lang=self.params.src_lang,
                        on_partial=emit_partial,
                    )
                except asyncio.CancelledError:
                    return
                except Exception as e:
                    log.exception("STT failed for %s", seg.segment_id)
                    await self._emit_client(
                        ErrorEvent(
                            code="STT_FAILED",
                            msg=f"{type(e).__name__}: {e}",
                            recoverable=True,
                        )
                    )
                    continue

                # Speaker identification on the final segment audio.
                speaker_label = await self._identify_speaker(seg)
                if speaker_label is not None:
                    final = final.model_copy(update={"speaker": speaker_label})

                await self._emit_client(final)
                # Always mark as finalized + queue for translation. The
                # translation loop decides whether to skip (detected lang
                # equals target) or actually translate. Marking finalized
                # here prevents a late partial translation from clobbering
                # the final one in the UI.
                self._finalized_segments.add(final.segment_id)
                self._partial_jobs.pop(final.segment_id, None)
                await self.final_events.put(final)
            finally:
                self._stt_inflight -= 1

    async def _translation_loop(self) -> None:
        """Translate each final event, routing per-segment by detected lang.

        Skipped cases (emit a `skipped=True` TranslationEvent instead of
        calling the translator):
          * detected source lang equals target → original already is target
          * empty / whitespace-only transcript (too-short segments)
        """
        while True:
            try:
                final = await self.final_events.get()
            except asyncio.CancelledError:
                return

            self._trans_inflight += 1
            try:
                text = (final.text or "").strip()
                # Text-level re-check overrides the audio-side lang guess
                # when the transcript itself betrays the real language
                # (e.g. CJK chars present → it's CJK regardless of what
                # Whisper thought).
                src_lang = text_lang_override(
                    text, audio_lang=final.lang, tgt_lang=self.params.tgt_lang,
                )
                # Skip path: transcribe-only mode OR nothing to translate.
                if (
                    not self.params.translate_enabled
                    or not text
                    or src_lang == self.params.tgt_lang
                ):
                    # Nothing to translate: tell the UI we're done so it can
                    # collapse the translation row. Empty text + skipped=True
                    # is the "no translation needed" marker.
                    await self._emit_client(
                        TranslationEvent(
                            segment_id=final.segment_id,
                            text="",
                            src_lang=src_lang,
                            tgt_lang=self.params.tgt_lang,
                            partial=False,
                            skipped=True,
                            speaker=final.speaker,
                        )
                    )
                    continue

                try:
                    translated = await self.translator.translate(
                        text,
                        src=src_lang,
                        tgt=self.params.tgt_lang,
                    )
                except asyncio.TimeoutError:
                    await self._emit_client(
                        ErrorEvent(
                            code="TRANSLATION_TIMEOUT",
                            msg=f"segment {final.segment_id}",
                            recoverable=True,
                        )
                    )
                    continue
                except asyncio.CancelledError:
                    return
                except Exception as e:
                    log.exception("Translation failed for %s", final.segment_id)
                    await self._emit_client(
                        ErrorEvent(
                            code="TRANSLATION_FAILED",
                            msg=f"{type(e).__name__}: {e}",
                            recoverable=True,
                        )
                    )
                    continue

                await self._emit_client(
                    TranslationEvent(
                        segment_id=final.segment_id,
                        text=translated,
                        src_lang=src_lang,
                        tgt_lang=self.params.tgt_lang,
                        partial=False,
                        speaker=final.speaker,
                    )
                )
            finally:
                self._trans_inflight -= 1

    async def _partial_translation_loop(self) -> None:
        """Translate the latest queued partial per segment, emit as partial TranslationEvent.

        Latest-wins: while we translate one partial, newer partials for the
        same segment replace the queued text. After translating, we re-check
        and pick up any newer text. Segments already finalized are skipped.
        """
        while True:
            try:
                await self._partial_translation_event.wait()
            except asyncio.CancelledError:
                return
            self._partial_translation_event.clear()

            # Snapshot and clear so producers can keep filling.
            snapshot = dict(self._partial_jobs)
            self._partial_jobs.clear()

            for segment_id, (text, src_lang) in snapshot.items():
                if segment_id in self._finalized_segments:
                    continue  # final already produced; partial would be stale

                self._partial_trans_inflight += 1
                try:
                    try:
                        translated = await self.translator.translate(
                            text,
                            src=src_lang,
                            tgt=self.params.tgt_lang,
                        )
                    except asyncio.CancelledError:
                        return
                    except Exception:
                        log.exception("partial translation failed for %s", segment_id)
                        continue

                    # If finalized while we were translating, skip — don't
                    # overwrite the final translation with stale partial.
                    if segment_id in self._finalized_segments:
                        continue

                    await self._emit_client(
                        TranslationEvent(
                            segment_id=segment_id,
                            text=translated,
                            src_lang=src_lang,
                            tgt_lang=self.params.tgt_lang,
                            partial=True,
                        )
                    )
                finally:
                    self._partial_trans_inflight -= 1
