"""Whole-file transcription — the offline half of speech-to-text.

The live pipeline transcribes what the VAD hands it, one utterance at a time,
because the user is waiting. Once the audio is all on disk there is no reason
to keep those cuts: Whisper does better with its own 30-second windows and
the context it carries between them. Two callers want exactly that pass —
:mod:`.upload` (files the user brought) and :mod:`.refine` (the recording of a
session that just ended) — so the pass lives here, once, and they differ only
in where the PCM comes from and what happens to the rows.

Times come out in seconds; whisper.cpp's centiseconds stop at this boundary.

Only one such pass runs at a time (see :func:`whole_file_slot`). It is the
heaviest thing the engine does — its own whisper.cpp context, its own copy of
the weights, ``n_threads=8`` of compute — and it used to go straight to the
default executor, which has ``cpu_count + 4`` workers. Three recordings queued
after a morning of meetings therefore ran *concurrently*: 24 compute threads on
8 cores and three copies of the model in memory, so all three crawled and none
finished. Serialised they take the same total time and the first one is done in
a third of it.
"""
from __future__ import annotations

import asyncio
import logging
import re
import wave
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import numpy as np

log = logging.getLogger(__name__)

SAMPLE_RATE = 16000

#: How many whole-file passes may run at once, and the gate that holds them
#: to it. Built on first use, because a Semaphore made at import time is fine
#: in 3.10+ but the limit comes from the config, which is read later.
_pass_limit = 1
_pass_gate: asyncio.Semaphore | None = None


def set_pass_limit(limit: int) -> None:
    """Set the whole-file pass concurrency (``session.max_parallel_passes``).

    Called once at startup. Raising it is a deliberate choice for a machine
    with cores to spare; 1 is right for a laptop, and is why a queued
    recording reports ``pending`` rather than looking stuck at ``processing``.
    """
    global _pass_limit, _pass_gate
    limit = max(1, int(limit))
    if _pass_gate is not None and limit != _pass_limit:
        log.warning("whole-file pass limit changed after the gate was built; ignoring")
        return
    _pass_limit = limit


def _gate() -> asyncio.Semaphore:
    global _pass_gate
    if _pass_gate is None:
        _pass_gate = asyncio.Semaphore(_pass_limit)
    return _pass_gate


@asynccontextmanager
async def whole_file_slot(on_wait: Callable[[], None] | None = None) -> AsyncIterator[None]:
    """Hold a slot for one whole-file pass, waiting for a free one.

    ``on_wait`` fires only when there is nothing free — the caller uses it to
    say so (a ``pending`` session, a log line), which is the difference
    between "queued behind another recording" and "started and stuck".
    """
    gate = _gate()
    if gate.locked() and on_wait is not None:
        on_wait()
    async with gate:
        yield


def read_wav_pcm(path: Path) -> bytes:
    """The int16 mono PCM of a recording WavWriter (or upload) wrote.

    Anything else — a stereo file, another rate — is a bug upstream, not a
    case to resample around; say so.
    """
    with wave.open(str(path), "rb") as wf:
        if wf.getnchannels() != 1 or wf.getsampwidth() != 2 or wf.getframerate() != SAMPLE_RATE:
            raise ValueError(
                f"{path.name}: expected 16 kHz mono int16, got "
                f"{wf.getframerate()} Hz × {wf.getnchannels()} ch × {wf.getsampwidth() * 8}-bit"
            )
        return wf.readframes(wf.getnframes())


def normalize_src_lang(src: str | None) -> str | None:
    """`None` / "" / "auto" → None (let Whisper auto-detect)."""
    if not src:
        return None
    s = src.strip().lower()
    return None if s in ("", "auto") else s


# Uploads are post-processed later for speaker labels, so preserving dialogue
# turns is more important than creating long reading paragraphs. Whisper.cpp's
# raw segments are already reasonably short; additionally, talk-show / subtitle
# style transcripts often use " -Next speaker" inside one raw segment. Split on
# that marker so the diarizer can label each turn independently. We deliberately
# do not merge adjacent raw segments here: doing so mixes speakers before the
# speaker pass gets a chance to separate them.
_DIALOGUE_TURN_RE = re.compile(r"\s+-(?!-)")


#: A row ends here, if it is long enough to be worth ending.
_TERMINAL = ".?!…。？！"
#: Below this, a row that ends in a full stop still joins the next one:
#: "Yeah." is a sentence and is not a transcript row.
_MIN_ROW_CHARS = 40
#: Hard caps, so a speaker who never pauses doesn't produce one giant row.
#: 24 s is about as far back as clicking a line should ever jump you.
_MAX_ROW_S = 24.0
_MAX_ROW_CHARS = 220


def merge_whisper_segments(
    segs: list[Any],
) -> list[tuple[str, float, float]]:
    """Convert whisper.cpp segments into transcript rows, joined into sentences.

    Returns a list of ``(text, t0_s, t1_s)``. Times are converted from
    centiseconds (whisper.cpp native unit) to seconds at this boundary —
    callers downstream don't have to think about it.

    The joining is the point, and it did not use to happen: this function
    only ever *split*, so a 28-minute meeting came out as 830 rows with a
    median of 16 characters — "Yeah.", "okay", "we" — and sentences cut in
    half. Whisper emits a segment per utterance, and in a meeting most
    utterances are backchannel; a transcript row is not the same unit.

    Measured on a real 28-minute meeting: 199 rows per 10 minutes become 86,
    the median row goes from 29 to 65 characters, and rows of twelve
    characters or fewer go from 50 to none.
    """
    rows: list[tuple[str, float, float, bool]] = []

    for s in segs:
        text = (s.text or "").strip()
        if not text:
            continue
        t0 = float(s.t0) / 100.0
        t1 = float(s.t1) / 100.0
        parts = _split_dialogue_turns(text)
        if len(parts) == 1 or t1 <= t0:
            rows.append((parts[0], t0, t1, False))
            continue

        weights = [max(1, len(p)) for p in parts]
        total = sum(weights)
        cur = t0
        for i, (part, weight) in enumerate(zip(parts, weights, strict=True)):
            nxt = t1 if i == len(parts) - 1 else cur + (t1 - t0) * weight / total
            # `turn=True`: a "-A -B" split is a change of speaker, and merging
            # across one would put two people in a row.
            rows.append((part, cur, nxt, i > 0))
            cur = nxt

    return _join_into_sentences(rows)


def _join_into_sentences(
    rows: list[tuple[str, float, float, bool]],
) -> list[tuple[str, float, float]]:
    """Join consecutive rows until one reads as a finished thought."""
    out: list[tuple[str, float, float]] = []
    text = ""
    start = 0.0
    end = 0.0
    for part, t0, t1, is_turn in rows:
        if not text:
            text, start, end = part, t0, t1
            continue
        joined = f"{text} {part}"
        finished = text.rstrip().endswith(tuple(_TERMINAL)) and len(text) >= _MIN_ROW_CHARS
        too_long = (t1 - start) > _MAX_ROW_S or len(joined) > _MAX_ROW_CHARS
        if is_turn or finished or too_long:
            out.append((text, start, end))
            text, start, end = part, t0, t1
        else:
            text, end = joined, t1
    if text:
        out.append((text, start, end))
    return out


def _split_dialogue_turns(text: str) -> list[str]:
    """Split subtitle-style "-A -B" speaker turns inside one Whisper segment."""
    parts = []
    for raw in _DIALOGUE_TURN_RE.split(text):
        part = raw.strip()
        if part.startswith("-"):
            part = part[1:].strip()
        if part:
            parts.append(part)
    return parts or [text.strip()]


def transcribe_pcm_sync(
    pcm: bytes,
    *,
    model_path: str,
    language: str | None,
    initial_prompt: str = "",
    n_threads: int = 8,
) -> list[tuple[str, float, float]]:
    """Run Whisper over a whole recording. Blocking — see :func:`transcribe_pcm`.

    Loads its own model: this runs in a job next to whatever the live
    pipeline holds, and sharing a whisper.cpp context across threads is not
    an option. ``initial_prompt`` is the glossary bias (see core.glossary);
    Whisper itself carries the previous window's text into the next, so
    context between windows needs nothing from us here.
    """
    from pywhispercpp.model import Model

    audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    model = Model(
        model_path,
        n_threads=n_threads,
        print_realtime=False,
        print_progress=False,
        print_timestamps=False,
    )
    kwargs: dict[str, Any] = {"language": language if language is not None else "auto"}
    # No `max_len`. It is a *subtitle* setting — it exists so a caption fits
    # on screen — and it chops a segment at 80 characters wherever it lands,
    # so the tail becomes its own row: "…sent to the cloud fair first then" /
    # "we" / "build a cloud fair tunnel…" is a real example from a real
    # meeting. Whisper's own segment boundaries are utterances, and
    # `merge_whisper_segments` joins those into sentences.
    kwargs["token_timestamps"] = True
    if initial_prompt:
        kwargs["initial_prompt"] = initial_prompt
    raw = list(model.transcribe(audio, **kwargs))
    rows = merge_whisper_segments(raw)
    log.info("whole-file transcription: %d raw segments → %d rows", len(raw), len(rows))
    return rows


async def transcribe_pcm(pcm: bytes, **kwargs: Any) -> list[tuple[str, float, float]]:
    """:func:`transcribe_pcm_sync` off the event loop.

    Callers that want to report the wait take :func:`whole_file_slot`
    themselves and pass ``queued=False``; the default acquires it here, so no
    path can reach whisper.cpp without going through the gate.
    """
    queued = bool(kwargs.pop("queued", True))
    loop = asyncio.get_event_loop()
    if not queued:
        return await loop.run_in_executor(None, lambda: transcribe_pcm_sync(pcm, **kwargs))
    async with whole_file_slot():
        return await loop.run_in_executor(None, lambda: transcribe_pcm_sync(pcm, **kwargs))
