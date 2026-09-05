"""Where to cut the audio, and what to carry across the cut.

Pure functions the live pipeline calls at segment boundaries. Nothing here
touches a backend or the event loop, so each rule has a test that runs on a
few arrays.

Two rules live here:

* :func:`find_cut_point` — a segment that reaches the length cap has to be
  cut somewhere, and "right now" is the one place guaranteed to be
  mid-word. The quietest moment in the last few seconds is almost always
  between words or at a breath; cut there and hand the rest to the next
  segment, so nothing is transcribed twice and no word is split.
* :func:`context_tail` — what the recogniser is told about the previous
  segment, so a sentence the VAD split still decodes as one thought.
"""
from __future__ import annotations

import numpy as np

SAMPLE_RATE = 16000
BYTES_PER_SAMPLE = 2

# Whisper reads about 224 prompt tokens and trims from the front; the
# glossary shares that budget, so this stays well under it. ~200 characters
# is the figure whisper_streaming settled on for the same purpose.
CONTEXT_TAIL_CHARS = 200


def find_cut_point(
    pcm: bytes,
    *,
    tail_ms: int = 4000,
    frame_ms: int = 100,
    sample_rate: int = SAMPLE_RATE,
) -> int:
    """Byte offset at which to split ``pcm`` — the middle of its quietest frame.

    Only the last ``tail_ms`` is considered: the point is to shorten the
    segment by as little as needed to land on a gap, not to find the quietest
    spot in the whole utterance. Energy is per-frame RMS of the int16 samples;
    ties go to the earliest frame, so a run of silence is cut at its start
    and the silence itself travels with the next segment (where the VAD will
    drop it soon enough). The offset is sample-aligned. Audio shorter than
    two frames is not cut (returns ``len(pcm)``).
    """
    if not pcm:
        return 0
    frame_bytes = max(BYTES_PER_SAMPLE, (sample_rate * frame_ms // 1000) * BYTES_PER_SAMPLE)
    n_frames_total = len(pcm) // frame_bytes
    if n_frames_total < 2:
        return len(pcm)
    tail_frames = max(1, (tail_ms // frame_ms))
    first = max(0, n_frames_total - tail_frames)
    # Never cut at frame 0: the segment would be empty and the whole buffer
    # would just move to the next one.
    first = max(first, 1)
    samples = np.frombuffer(pcm[: n_frames_total * frame_bytes], dtype=np.int16)
    frames = samples.reshape(n_frames_total, -1).astype(np.float32)
    rms = np.sqrt(np.mean(frames * frames, axis=1))
    quietest = first + int(np.argmin(rms[first:]))
    cut = quietest * frame_bytes + frame_bytes // 2
    # Sample alignment (int16): an odd byte offset would shear every sample after it.
    cut -= cut % BYTES_PER_SAMPLE
    return min(cut, len(pcm))


def context_tail(text: str, max_chars: int = CONTEXT_TAIL_CHARS) -> str:
    """The end of ``text`` that fits the prompt budget, cut on a word boundary.

    Whole words only (spaces mark them; CJK text has none and is cut by
    character, which is fine — every character is a unit there).
    """
    text = " ".join(text.split())
    if len(text) <= max_chars:
        return text
    tail = text[-max_chars:]
    space = tail.find(" ")
    # Drop the partial first word when there is a word boundary to cut on.
    if 0 < space < max_chars // 2:
        tail = tail[space + 1 :]
    return tail
