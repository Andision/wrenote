"""Whole-file transcription — the offline half of speech-to-text.

The live pipeline transcribes what the VAD hands it, one utterance at a time,
because the user is waiting. Once the audio is all on disk there is no reason
to keep those cuts: Whisper does better with its own 30-second windows and
the context it carries between them. Two callers want exactly that pass —
:mod:`.upload` (files the user brought) and :mod:`.refine` (the recording of a
session that just ended) — so the pass lives here, once, and they differ only
in where the PCM comes from and what happens to the rows.

Times come out in seconds; whisper.cpp's centiseconds stop at this boundary.
"""
from __future__ import annotations

import asyncio
import logging
import re
import wave
from pathlib import Path
from typing import Any

import numpy as np

log = logging.getLogger(__name__)

SAMPLE_RATE = 16000


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


def merge_whisper_segments(
    segs: list[Any],
) -> list[tuple[str, float, float]]:
    """Convert whisper.cpp segments into dialogue-friendly transcript rows.

    Returns a list of ``(text, t0_s, t1_s)``. Times are converted from
    centiseconds (whisper.cpp native unit) to seconds at this boundary —
    callers downstream don't have to think about it.
    """
    out: list[tuple[str, float, float]] = []

    for s in segs:
        text = (s.text or "").strip()
        if not text:
            continue
        t0 = float(s.t0) / 100.0
        t1 = float(s.t1) / 100.0
        parts = _split_dialogue_turns(text)
        if len(parts) == 1 or t1 <= t0:
            out.append((parts[0], t0, t1))
            continue

        weights = [max(1, len(p)) for p in parts]
        total = sum(weights)
        cur = t0
        for i, (part, weight) in enumerate(zip(parts, weights, strict=True)):
            nxt = t1 if i == len(parts) - 1 else cur + (t1 - t0) * weight / total
            out.append((part, cur, nxt))
            cur = nxt
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
    # Keep the rows close to Whisper's detected turns: the speaker post-pass
    # labels smaller rows better, and the client groups them for display.
    kwargs["max_len"] = 80
    kwargs["split_on_word"] = True
    kwargs["token_timestamps"] = True
    if initial_prompt:
        kwargs["initial_prompt"] = initial_prompt
    raw = list(model.transcribe(audio, **kwargs))
    rows = merge_whisper_segments(raw)
    log.info("whole-file transcription: %d raw segments → %d rows", len(raw), len(rows))
    return rows


async def transcribe_pcm(pcm: bytes, **kwargs: Any) -> list[tuple[str, float, float]]:
    """:func:`transcribe_pcm_sync` off the event loop."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: transcribe_pcm_sync(pcm, **kwargs))
