"""What the live pipeline tells its backends about the segments before the
current one, and how it cuts a segment that outgrows the cap.

Mock STT/translator/disabled VAD: every chunk is speech, so a segment only
ever closes at the cap or on flush — exactly the two paths under test.
"""
from __future__ import annotations

import asyncio

import numpy as np

from wrenote.core.events import TranscriptEvent, VADEvent
from wrenote.core.pipeline import Pipeline, SessionParams
from wrenote.stt.mock import MockSTTBackend
from wrenote.translator.mock import MockTranslatorBackend
from wrenote.vad.disabled import DisabledVAD

RATE = 16000


async def _drain(pipeline: Pipeline, *, until_finals: int, timeout: float = 5.0) -> list:
    events: list = []
    finals = 0
    async def pump():
        nonlocal finals
        async for ev in pipeline.client_event_stream():
            events.append(ev)
            if isinstance(ev, TranscriptEvent) and ev.type == "final":
                finals += 1
    task = asyncio.create_task(pump())
    deadline = asyncio.get_event_loop().time() + timeout
    while finals < until_finals and asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(0.02)
    await asyncio.sleep(0.2)  # let translations land
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    return events


async def test_finals_carry_context_to_the_next_segment_and_the_translator():
    stt = MockSTTBackend(delay_s=0, text="the quick brown fox")
    tr = MockTranslatorBackend(delay_s=0)
    p = Pipeline(
        stt=stt, vad=DisabledVAD(), translator=tr,
        params=SessionParams(src_lang="en", tgt_lang="zh", partial_interval_ms=0, translate_partials=False),
    )
    await p.start()
    try:
        chunk = np.full(RATE // 10, 100, dtype=np.int16).tobytes()  # 100 ms
        await p.feed_audio(chunk)
        await p.close_open_segment()
        await asyncio.sleep(0.1)
        await p.feed_audio(chunk)
        await p.close_open_segment()
        events = await _drain(p, until_finals=2)
    finally:
        await p.stop()

    finals = [e for e in events if isinstance(e, TranscriptEvent) and e.type == "final"]
    assert len(finals) == 2
    first, second = finals
    assert stt.contexts[first.segment_id] == ""
    assert stt.contexts[second.segment_id] == "the quick brown fox"
    # The translator saw the first final when translating the second.
    assert tr.last_context == ("the quick brown fox",)


async def test_context_can_be_switched_off():
    stt = MockSTTBackend(delay_s=0, text="hello")
    tr = MockTranslatorBackend(delay_s=0)
    p = Pipeline(
        stt=stt, vad=DisabledVAD(), translator=tr,
        params=SessionParams(
            src_lang="en", tgt_lang="zh", partial_interval_ms=0, translate_partials=False,
            stt_context_chars=0, translate_context_segments=0,
        ),
    )
    await p.start()
    try:
        chunk = np.full(RATE // 10, 100, dtype=np.int16).tobytes()
        for _ in range(2):
            await p.feed_audio(chunk)
            await p.close_open_segment()
            await asyncio.sleep(0.1)
        await _drain(p, until_finals=2)
    finally:
        await p.stop()
    assert set(stt.contexts.values()) == {""}
    assert tr.last_context == ()


async def test_cap_cuts_at_the_quiet_moment_and_carries_the_rest():
    stt = MockSTTBackend(delay_s=0)
    p = Pipeline(
        stt=stt, vad=DisabledVAD(), translator=MockTranslatorBackend(delay_s=0),
        params=SessionParams(
            src_lang="en", tgt_lang="zh", partial_interval_ms=0, translate_partials=False,
            max_segment_ms=1000,
        ),
    )
    await p.start()
    try:
        # 100 ms chunks: loud, except chunk 7 (0.7–0.8 s) which is silent.
        loud = (np.sin(np.arange(RATE // 10) * 0.3) * 8000).astype(np.int16).tobytes()
        quiet = np.zeros(RATE // 10, dtype=np.int16).tobytes()
        t0 = asyncio.get_event_loop().time()
        for i in range(12):
            await p.feed_audio(quiet if i == 7 else loud)
            # Real time has to pass: the cap is measured on server timestamps.
            await asyncio.sleep(max(0.0, t0 + (i + 1) * 0.1 - asyncio.get_event_loop().time()))
        await p.flush()
        events = await _drain(p, until_finals=2)
    finally:
        await p.stop()

    finals = [e for e in events if isinstance(e, TranscriptEvent) and e.type == "final"]
    starts = [e for e in events if isinstance(e, VADEvent) and e.type == "speech_start"]
    assert len(finals) == 2, [f.text for f in finals]
    first, second = finals
    # The cut landed inside the silent chunk, the next segment starts exactly
    # there, and the two together cover everything once.
    assert 0.7 <= first.t1 <= 0.8, first.t1
    assert second.t0 == first.t1
    assert len(starts) == 2 and starts[1].ts == first.t1
