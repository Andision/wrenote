"""The streaming-native live path (core/pipeline.py _stream_loop) and the
sherpa-onnx backend's text handling.

A fake streaming recogniser stands in for the model: it answers with a
scripted utterance as audio arrives and calls the endpoint itself. What the
pipeline must produce from that is exactly what the VAD path produces —
speech_start/end, growing partials, one final per utterance with the
utterance's audio for the speaker pass — so nothing downstream can tell.
"""
from __future__ import annotations

import asyncio

import numpy as np

from wrenote.core.events import AudioSegment, BackendInfo, TranscriptEvent, VADEvent
from wrenote.core.pipeline import Pipeline, SessionParams
from wrenote.stt.base import StreamUpdate, STTBackend, STTStream
from wrenote.stt.sherpa_onnx import tidy_text
from wrenote.translator.mock import MockTranslatorBackend
from wrenote.vad.disabled import DisabledVAD

RATE = 16000
CHUNK = np.full(RATE // 10, 100, dtype=np.int16).tobytes()  # 100 ms


class ScriptedStream(STTStream):
    """Each utterance: `words` revealed one per chunk, then an endpoint chunk."""

    def __init__(self, utterances: list[list[str]]) -> None:
        self._utts = utterances
        self._u = 0
        self._i = 0
        self.resets = 0
        self.fed = 0
        self.flushed = False

    def _text(self) -> str:
        words = self._utts[self._u] if self._u < len(self._utts) else []
        return " ".join(words[: self._i])

    async def feed(self, pcm: bytes) -> StreamUpdate:
        self.fed += 1
        if self._u >= len(self._utts):
            return StreamUpdate(text="")
        words = self._utts[self._u]
        if self._i < len(words):
            self._i += 1
            return StreamUpdate(text=self._text(), start_offset_s=0.05)
        # One more chunk of silence: the utterance is over.
        return StreamUpdate(text=self._text(), endpoint=True, start_offset_s=0.05)

    async def flush(self) -> StreamUpdate:
        self.flushed = True
        return StreamUpdate(text=self._text(), endpoint=True)

    async def reset(self) -> None:
        self.resets += 1
        self._u += 1
        self._i = 0

    async def close(self) -> None:
        pass


class StreamingSTT(STTBackend):
    def __init__(self, utterances: list[list[str]]) -> None:
        self.stream = ScriptedStream(utterances)
        self.opened_with: dict[str, int] | None = None

    async def load(self) -> None:
        pass

    async def unload(self) -> None:
        pass

    async def transcribe_segment(self, segment, *, src_lang=None, on_partial=None, context=""):
        raise AssertionError("the stream loop must not call transcribe_segment")

    def open_stream(self, *, min_silence_ms: int, max_segment_ms: int) -> STTStream:
        self.opened_with = {"min_silence_ms": min_silence_ms, "max_segment_ms": max_segment_ms}
        return self.stream

    @property
    def info(self) -> BackendInfo:
        return BackendInfo(name="scripted", version="0", model="x", device="cpu")


class SpyVAD(DisabledVAD):
    def __init__(self) -> None:
        self.calls = 0

    async def is_speech(self, chunk) -> bool:
        self.calls += 1
        return True


async def _run(stt: StreamingSTT, n_chunks: int, **params) -> tuple[list, Pipeline]:
    vad = SpyVAD()
    p = Pipeline(
        stt=stt, vad=vad, translator=MockTranslatorBackend(delay_s=0),
        params=SessionParams(src_lang="zh", tgt_lang="en", partial_interval_ms=0, **params),
    )
    await p.start()
    events: list = []

    async def pump() -> None:
        async for ev in p.client_event_stream():
            events.append(ev)

    task = asyncio.create_task(pump())
    try:
        t0 = asyncio.get_event_loop().time()
        for i in range(n_chunks):
            await p.feed_audio(CHUNK)
            # Real time matters: server_ts comes from the clock.
            await asyncio.sleep(max(0.0, t0 + (i + 1) * 0.02 - asyncio.get_event_loop().time()))
        await p.flush()
        await asyncio.sleep(0.1)
    finally:
        task.cancel()
        await p.stop()
    assert vad.calls == 0, "the VAD is idle on the streaming path"
    return events, p


async def test_utterances_become_segments_with_partials_and_finals():
    stt = StreamingSTT([["昨天", "是", "monday"], ["today", "is"]])
    events, _ = await _run(stt, n_chunks=12)
    assert stt.opened_with == {"min_silence_ms": 800, "max_segment_ms": 25000}

    starts = [e for e in events if isinstance(e, VADEvent) and e.type == "speech_start"]
    ends = [e for e in events if isinstance(e, VADEvent) and e.type == "speech_end"]
    finals = [e for e in events if isinstance(e, TranscriptEvent) and e.type == "final"]
    partials = [e for e in events if isinstance(e, TranscriptEvent) and e.type == "partial"]
    assert [f.text for f in finals] == ["昨天 是 monday", "today is"]
    assert [f.lang for f in finals] == ["zh", "zh"]  # src pinned to zh, no CJK heuristic in the fake
    assert len(starts) == 2 and len(ends) == 2
    assert starts[0].segment_id == finals[0].segment_id
    # Partials only ever grow, and belong to their segment.
    first = [pt.text for pt in partials if pt.segment_id == finals[0].segment_id]
    assert first == ["昨天", "昨天 是", "昨天 是 monday"]
    # Times: the utterance starts where its first token was heard, ends at the endpoint.
    assert finals[0].t0 < finals[0].t1 <= finals[1].t0 < finals[1].t1
    assert stt.stream.resets == 2 and stt.stream.flushed


async def test_stop_flushes_the_open_utterance():
    stt = StreamingSTT([["only", "one", "utterance", "still", "going"]])
    events, _ = await _run(stt, n_chunks=3)  # stopped mid-utterance
    finals = [e for e in events if isinstance(e, TranscriptEvent) and e.type == "final"]
    assert [f.text for f in finals] == ["only one utterance"]
    assert stt.stream.flushed


async def test_speaker_pass_gets_the_utterance_audio():
    seen: list[AudioSegment] = []

    class Speaker:
        async def load(self): pass
        async def unload(self): pass
        async def embed(self, pcm):
            seen.append(pcm)
            return np.ones(8, dtype=np.float32)
        @property
        def info(self): return BackendInfo(name="spk", version="0", model="x", device="cpu")

    stt = StreamingSTT([["hello", "there"]])
    vad = DisabledVAD()
    p = Pipeline(
        stt=stt, vad=vad, translator=MockTranslatorBackend(delay_s=0), speaker=Speaker(),  # type: ignore[arg-type]
        params=SessionParams(src_lang="en", tgt_lang="zh", partial_interval_ms=0, speaker_min_audio_ms=0),
    )
    await p.start()
    events: list = []
    task = asyncio.create_task(_collect(p, events))
    try:
        for _ in range(4):
            await p.feed_audio(CHUNK)
            await asyncio.sleep(0.02)
        await p.flush()
        await asyncio.sleep(0.1)
    finally:
        task.cancel()
        await p.stop()
    finals = [e for e in events if isinstance(e, TranscriptEvent) and e.type == "final"]
    assert finals and finals[0].speaker == "Speaker 1"
    assert len(seen) == 1 and len(seen[0]) == len(CHUNK) * 3  # the utterance's chunks, not the whole session


async def _collect(p: Pipeline, into: list) -> None:
    async for ev in p.client_event_stream():
        into.append(ev)


def test_tidy_text():
    assert tidy_text("TODAY IS LIBR THE DAY AFTER TOMORROW是星期三") == "Today is libr the day after tomorrow 是星期三"
    assert tidy_text("昨天是 MONDAY") == "昨天是 monday"
    assert tidy_text("I'M   OK") == "I'm ok"
    assert tidy_text("") == ""
