"""Pipeline smoke test.

Drives mock backends through the pipeline with a controlled audio feed.
Verifies: speech_start → final → translation arrive in client_events.
"""
import asyncio

import wrenote  # noqa: F401  -- registers backends
from wrenote.core.pipeline import Pipeline, SessionParams
from wrenote.core.registry import make_stt, make_translator, make_vad


async def main() -> None:
    stt = make_stt("mock", {"delay_s": 0.05})
    vad = make_vad("disabled")  # always-speech VAD
    translator = make_translator("mock", {"delay_s": 0.05})

    # max_segment_ms small so always-speech VAD eventually force-closes the segment
    params = SessionParams(
        src_lang="en",
        tgt_lang="zh",
        min_silence_ms=100,
        max_segment_ms=300,
    )
    pipeline = Pipeline(stt, vad, translator, params)
    await pipeline.start()

    collected: list = []

    async def collect() -> None:
        async for ev in pipeline.client_event_stream():
            collected.append(ev)

    collect_task = asyncio.create_task(collect())

    # Feed 5 chunks 100ms apart; force-close should fire ~300ms in
    silent_pcm = b"\x00" * 3200  # 100ms @ 16kHz int16
    for _ in range(5):
        await pipeline.feed_audio(silent_pcm)
        await asyncio.sleep(0.1)

    # Let processing drain
    await asyncio.sleep(0.5)

    collect_task.cancel()
    try:
        await collect_task
    except asyncio.CancelledError:
        pass

    print(f"queue_status at end: {pipeline.queue_status()}")
    print(f"collected {len(collected)} events:")
    for ev in collected:
        kind = type(ev).__name__
        type_field = getattr(ev, "type", "?")
        seg = getattr(ev, "segment_id", "-")
        text = getattr(ev, "text", "")
        print(f"  {kind:20} type={type_field!r:18} seg={str(seg)[:8]}.. text={text!r}")

    types = [(type(ev).__name__, getattr(ev, "type", None)) for ev in collected]
    assert ("VADEvent", "speech_start") in types, f"no speech_start; got: {types}"
    assert ("VADEvent", "speech_end") in types, f"no speech_end; got: {types}"
    assert ("TranscriptEvent", "final") in types, f"no final; got: {types}"
    assert ("TranslationEvent", "translation") in types, f"no translation; got: {types}"

    await pipeline.stop()
    print("\nPipeline smoke OK")


if __name__ == "__main__":
    asyncio.run(main())
