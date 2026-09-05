"""Batch-transcribe uploaded audio/video files into a regular session.

User uploads N files in some order. Each is decoded to 16k mono int16 PCM
via ffmpeg, the streams are concatenated, written as the session's WAV
(same path WavWriter would use for a live session), then run through
Whisper once. Whisper's own segmentation drives the segment rows; the
translator hits each segment whose lang differs from the target. The
result is indistinguishable from a live session — same DB shape, same
WAV file path, same chat / diarize endpoints.

The original uploaded files are *not* kept: only the extracted+concatenated
WAV survives. (Originals are usually huge — video especially — and the
user has them locally.)
"""
from __future__ import annotations

import asyncio
import logging
import wave
from datetime import UTC, datetime
from pathlib import Path

from ..translator.base import TranslatorBackend
from .batch import merge_whisper_segments, normalize_src_lang, transcribe_pcm
from .jobs import JobRegistry, Phase
from .recording import resolve_recording_path
from .store import Store
from .translation import context_before, translate_one_for_segment

__all__ = ["merge_whisper_segments", "process_upload"]

log = logging.getLogger(__name__)


async def decode_file_to_pcm(path: Path, sample_rate: int = 16000) -> bytes:
    """Decode any ffmpeg-readable file to mono int16 LE PCM bytes."""
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-v", "error",
        "-i", str(path),
        "-f", "s16le", "-ac", "1", "-ar", str(sample_rate),
        "-",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg failed on {path.name}: {err.decode(errors='ignore').strip()}"
        )
    return out


def _write_wav(path: Path, pcm: bytes, sample_rate: int = 16000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)


# Phase weights, tuned from M1 Max measurements: transcribe dominates,
# then translate, then everything else. Translator is dropped from the
# total when transcribe-only mode is selected.
UPLOAD_PHASES_TRANSLATE = [
    Phase("decode", 0.08),
    Phase("write_wav", 0.02),
    Phase("load_models", 0.05),
    Phase("transcribe", 0.55),
    Phase("translate", 0.28),
    Phase("finalize", 0.02),
]
UPLOAD_PHASES_NO_TRANSLATE = [
    Phase("decode", 0.10),
    Phase("write_wav", 0.03),
    Phase("load_stt", 0.07),
    Phase("transcribe", 0.78),
    Phase("finalize", 0.02),
]


async def process_upload(
    *,
    job_id: str,
    registry: JobRegistry,
    session_id: str,
    title: str,
    src_lang: str | None,
    tgt_lang: str,
    translate: bool,
    file_paths: list[Path],
    whisper_model_path: str,
    translator: TranslatorBackend | None,
    store: Store,
    recordings_dir: Path,
    sample_rate: int = 16000,
    initial_prompt: str = "",
) -> None:
    """Decode → concat → transcribe → translate → persist, reporting
    progress into the job registry. Caller owns lifecycle (registry.fail
    / registry.complete) — this function only advances phases and raises
    on hard errors. ``initial_prompt`` is the glossary bias for Whisper.
    """
    if not file_paths:
        raise ValueError("no files provided")

    def advance(phase_idx: int, inner: float = 0.0, log: str | None = None) -> None:
        registry.advance(
            job_id, phase_idx=phase_idx, phase_inner=inner, log_line=log,
        )

    def tick(inner: float, log: str | None = None) -> None:
        registry.advance(job_id, phase_inner=inner, log_line=log)

    # ---- Phase 0: decode ----
    advance(0, 0.0, f"Decoding {len(file_paths)} file(s)")
    pcm_parts: list[bytes] = []
    for i, fp in enumerate(file_paths, start=1):
        tick((i - 1) / len(file_paths), f"Decoding {i}/{len(file_paths)}: {fp.name}")
        pcm = await decode_file_to_pcm(fp, sample_rate=sample_rate)
        if not pcm:
            raise RuntimeError(f"empty decode result for {fp.name}")
        pcm_parts.append(pcm)
    full_pcm = b"".join(pcm_parts)
    duration_s = len(full_pcm) / 2 / sample_rate
    tick(1.0, f"Concatenated audio: {duration_s:.1f}s")

    # ---- Phase 1: write WAV ----
    advance(1, 0.0, "Writing recording")
    wav_path = resolve_recording_path(session_id, recordings_dir=recordings_dir)
    _write_wav(wav_path, full_pcm, sample_rate=sample_rate)
    tick(1.0)

    # ---- Phase 2: load models ----
    advance(2, 0.0, "Loading STT model")
    # Translator is loaded by the caller before invoking us (see server),
    # but we still mark the phase as a discrete step so the progress bar
    # has something visible to advance through.
    requested_lang = normalize_src_lang(src_lang)
    created_at = datetime.now(UTC).isoformat()
    # `processing` from the first moment the row exists: the client's list
    # shows the upload as in progress rather than as an empty session.
    await store.upsert_session(
        session_id=session_id,
        title=title,
        created_at=created_at,
        src_lang=src_lang or "auto",
        tgt_lang=tgt_lang,
        duration_s=duration_s,
        status="processing",
    )
    tick(1.0)

    # ---- Phase 3: transcribe ----
    advance(3, 0.0, "Transcribing")
    paragraphs = await transcribe_pcm(
        full_pcm,
        model_path=whisper_model_path,
        language=requested_lang,
        initial_prompt=initial_prompt,
    )
    tick(1.0, f"Prepared {len(paragraphs)} dialogue turns")

    fallback_lang = requested_lang or "en"

    if translate:
        # ---- Phase 4: translate ----
        if translator is None:
            raise ValueError("translate=True but no translator provided")
        advance(4, 0.0, "Translating")
    else:
        # No translate phase in this weight schedule — go straight to finalize.
        advance(3, 1.0, "Transcribe-only mode: skipping translation")

    texts = [text for text, _t0, _t1 in paragraphs]
    for i, (text, t0, t1) in enumerate(paragraphs):
        sid = f"u-{i:04d}"
        seg_lang = fallback_lang

        await store.upsert_segment_orig(
            session_id=session_id,
            segment_id=sid,
            ord_=i,
            started_at=t0,
            ended_at=t1,
            orig_text=text,
            orig_status="final",
            orig_lang=seg_lang,
        )

        if not translate:
            await store.upsert_segment_trans(
                session_id=session_id,
                segment_id=sid,
                ord_=i,
                trans_text="",
                trans_status="skipped",
                trans_lang=tgt_lang,
            )
        else:
            assert translator is not None
            translated, status = await translate_one_for_segment(
                translator=translator,
                text=text,
                audio_lang=seg_lang,
                tgt_lang=tgt_lang,
                context=context_before(texts, i),
            )
            await store.upsert_segment_trans(
                session_id=session_id,
                segment_id=sid,
                ord_=i,
                trans_text=translated,
                trans_status=status,
                trans_lang=tgt_lang,
            )

        if translate and paragraphs:
            tick(
                (i + 1) / len(paragraphs),
                f"Translated {i + 1}/{len(paragraphs)}"
                if (i + 1) % 5 == 0 or i + 1 == len(paragraphs)
                else None,
            )

    # ---- Final phase: finalize ----
    final_idx = 5 if translate else 4
    advance(final_idx, 0.0, "Finalizing")
    await store.update_session_duration(session_id, duration_s)
    # An upload is a whole-file transcription by construction.
    await store.mark_refined(session_id, datetime.now(UTC).isoformat())
    tick(1.0)
