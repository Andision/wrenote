"""sherpa-onnx streaming backend — a recogniser built for live audio.

Zipformer transducers (k2-fsa) decode causally: text comes out as the audio
comes in, a decoded prefix never changes, and the recogniser itself says
when an utterance is over. That is the whole reason this backend exists —
the live path needs none of the segmenting, re-decoding and prompting the
Whisper path does (see core/pipeline.py, the stream loop). Runs on the CPU
through ONNX Runtime at several times real time; needs no compute runtime
pack.

The bilingual zh-en model is the default (see models.yaml). It writes
English in capitals and no punctuation on either language; the text is
tidied for display, and the post-recording pass replaces it with Whisper's
anyway.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import os
import re
from pathlib import Path
from typing import Any

import numpy as np

from ..core.events import AudioSegment, BackendInfo, TranscriptEvent
from ..core.lang import LanguagePolicy
from ..core.registry import register_stt
from .base import PartialCallback, StreamUpdate, STTBackend, STTStream

log = logging.getLogger(__name__)

SAMPLE_RATE = 16000
# The model's right context: audio at the very end is decoded only once
# this much silence follows it, so a flush pads with it.
_TAIL_PAD_S = 0.6
# An utterance with nothing decoded ends after this much audio (rule 1);
# keeps a silent stream from holding an "utterance" open forever.
_SILENT_UTTERANCE_S = 2.4

# A run of capitals with no letter on either side; `\b` would not do, since
# a CJK character counts as a word character and hides the boundary.
_CAPS_WORD = re.compile(r"(?<![A-Za-z])[A-Z][A-Z'-]*(?![A-Za-z])")


def tidy_text(text: str) -> str:
    """The model's ``TODAY IS 星期三`` → ``Today is 星期三``: lower-case the
    all-caps English, capitalise a sentence start, tidy the spaces."""
    text = " ".join(text.split())
    if not text:
        return ""
    # Space between a CJK run and Latin, both ways, for readability.
    text = re.sub(r"([\u4e00-\u9fff])([A-Za-z])", r"\1 \2", text)
    text = re.sub(r"([A-Za-z])([\u4e00-\u9fff])", r"\1 \2", text)
    text = _CAPS_WORD.sub(lambda m: m.group(0).lower(), text)
    return text[0].upper() + text[1:] if text[0].isascii() else text


def _guess_lang(text: str, main: str | None) -> str:
    """The line's language, from its script: CJK means Chinese; otherwise
    the session's main language, or English."""
    if re.search(r"[\u4e00-\u9fff]", text):
        return "zh"
    return main or "en"


@register_stt("sherpa_onnx")
class SherpaOnnxBackend(STTBackend):
    def __init__(
        self,
        *,
        encoder_path: str,
        decoder_path: str,
        joiner_path: str,
        tokens_path: str,
        num_threads: int | None = None,
        provider: str = "cpu",
        decoding_method: str = "greedy_search",
        **_ignored: Any,
    ) -> None:
        # Extra keys are the other STT backend's tuning (whisper's `device`,
        # `language`) left in the config after a switch; harmless here.
        self._paths = {
            "encoder": str(Path(encoder_path).expanduser()),
            "decoder": str(Path(decoder_path).expanduser()),
            "joiner": str(Path(joiner_path).expanduser()),
            "tokens": str(Path(tokens_path).expanduser()),
        }
        self._num_threads = num_threads or max(1, min(4, (os.cpu_count() or 4) - 1))
        self._provider = provider
        self._decoding_method = decoding_method
        # One recogniser per endpoint setting; a session's stream picks its own.
        self._recognizers: dict[tuple[float, float], Any] = {}
        self._policy: LanguagePolicy | None = None
        # ONNX Runtime sessions are not to be driven from several threads at
        # once; everything runs on this one worker, in order.
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="sherpa"
        )

    # ---------- lifecycle ----------

    async def load(self) -> None:
        for name, path in self._paths.items():
            if not Path(path).exists():
                raise FileNotFoundError(f"sherpa-onnx {name} not found at {path}")
        # The default endpoint setting; a session with other values gets its
        # own recogniser on first use.
        await self._recognizer(0.8, 25.0)

    async def unload(self) -> None:
        def _drop() -> None:
            self._recognizers.clear()
        try:
            await asyncio.get_event_loop().run_in_executor(self._executor, _drop)
        except RuntimeError:
            self._recognizers.clear()
        self._executor.shutdown(wait=False)

    async def _recognizer(self, trailing_silence_s: float, max_utterance_s: float) -> Any:
        key = (round(trailing_silence_s, 2), round(max_utterance_s, 1))
        rec = self._recognizers.get(key)
        if rec is not None:
            return rec

        def _build() -> Any:
            # Imported here: the binding is an optional dependency, and a
            # missing one should fail the backend, not the engine.
            import sherpa_onnx

            return sherpa_onnx.OnlineRecognizer.from_transducer(
                tokens=self._paths["tokens"],
                encoder=self._paths["encoder"],
                decoder=self._paths["decoder"],
                joiner=self._paths["joiner"],
                num_threads=self._num_threads,
                sample_rate=SAMPLE_RATE,
                feature_dim=80,
                enable_endpoint_detection=True,
                rule1_min_trailing_silence=_SILENT_UTTERANCE_S,
                rule2_min_trailing_silence=trailing_silence_s,
                rule3_min_utterance_length=max_utterance_s,
                decoding_method=self._decoding_method,
                provider=self._provider,
            )

        log.info(
            "Loading sherpa-onnx recogniser from %s (threads=%d, endpoint %.1fs/%.0fs)",
            self._paths["encoder"], self._num_threads, trailing_silence_s, max_utterance_s,
        )
        rec = await asyncio.get_event_loop().run_in_executor(self._executor, _build)
        self._recognizers[key] = rec
        return rec

    # ---------- STTBackend ----------

    def set_language_policy(self, policy: LanguagePolicy) -> None:
        # No language id here: the model is bilingual and writes what it
        # hears. The policy's main language labels lines with no CJK in them.
        self._policy = policy

    def open_stream(self, *, min_silence_ms: int, max_segment_ms: int) -> STTStream:
        return _Stream(self, min_silence_ms / 1000.0, max_segment_ms / 1000.0)

    async def transcribe_segment(
        self,
        segment: AudioSegment,
        *,
        src_lang: str | None = None,
        on_partial: PartialCallback | None = None,
        context: str = "",
    ) -> TranscriptEvent:
        """Whole-segment use of a streaming model: feed it all, flush, read."""
        rec = await self._recognizer(0.8, 25.0)
        audio = np.frombuffer(segment.pcm, dtype=np.int16).astype(np.float32) / 32768.0

        def _run() -> str:
            s = rec.create_stream()
            s.accept_waveform(SAMPLE_RATE, audio)
            s.accept_waveform(SAMPLE_RATE, np.zeros(int(SAMPLE_RATE * _TAIL_PAD_S), dtype=np.float32))
            s.input_finished()
            while rec.is_ready(s):
                rec.decode_stream(s)
            return rec.get_result(s)

        raw = await asyncio.get_event_loop().run_in_executor(self._executor, _run)
        text = tidy_text(raw)
        main = self._policy.main if self._policy else (src_lang if src_lang not in (None, "", "auto") else None)
        return TranscriptEvent(
            type="final", segment_id=segment.segment_id, text=text,
            lang=_guess_lang(text, main), t0=segment.t0, t1=segment.t1,
        )

    def lang_for(self, text: str) -> str:
        return _guess_lang(text, self._policy.main if self._policy else None)

    @property
    def info(self) -> BackendInfo:
        return BackendInfo(
            name="sherpa_onnx",
            version="sherpa-onnx",
            model=Path(self._paths["encoder"]).parent.name or Path(self._paths["encoder"]).stem,
            device=self._provider,
            supported_languages=["zh", "en"],
            capabilities={"streaming": True, "threads": self._num_threads},
        )


class _Stream(STTStream):
    def __init__(self, backend: SherpaOnnxBackend, trailing_silence_s: float, max_utterance_s: float) -> None:
        self._b = backend
        self._rules = (trailing_silence_s, max_utterance_s)
        self._rec: Any = None
        self._s: Any = None

    async def _ensure(self) -> None:
        if self._s is None:
            self._rec = await self._b._recognizer(*self._rules)
            rec = self._rec
            self._s = await asyncio.get_event_loop().run_in_executor(self._b._executor, rec.create_stream)

    def _read(self) -> StreamUpdate:
        rec, s = self._rec, self._s
        while rec.is_ready(s):
            rec.decode_stream(s)
        res = rec.get_result_all(s)
        # Timestamps restart at each reset, so the first one is the offset of
        # the first token within this utterance.
        start = float(res.timestamps[0]) if res.timestamps else None
        return StreamUpdate(text=tidy_text(res.text), endpoint=bool(rec.is_endpoint(s)), start_offset_s=start)

    async def feed(self, pcm: bytes) -> StreamUpdate:
        await self._ensure()
        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0

        def _run() -> StreamUpdate:
            self._s.accept_waveform(SAMPLE_RATE, audio)
            return self._read()

        return await asyncio.get_event_loop().run_in_executor(self._b._executor, _run)

    async def flush(self) -> StreamUpdate:
        await self._ensure()

        def _run() -> StreamUpdate:
            self._s.accept_waveform(SAMPLE_RATE, np.zeros(int(SAMPLE_RATE * _TAIL_PAD_S), dtype=np.float32))
            self._s.input_finished()
            upd = self._read()
            return StreamUpdate(text=upd.text, endpoint=True, start_offset_s=upd.start_offset_s)

        return await asyncio.get_event_loop().run_in_executor(self._b._executor, _run)

    async def reset(self) -> None:
        if self._s is None:
            return
        rec, s = self._rec, self._s

        def _run() -> None:
            rec.reset(s)

        await asyncio.get_event_loop().run_in_executor(self._b._executor, _run)

    async def close(self) -> None:
        self._s = None
