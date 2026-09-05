"""The post-recording pass: transcribe the whole recording again, properly.

Live transcription is a compromise the user hears in real time: the VAD
decides where utterances end, a segment that hits the length cap is cut
wherever it happens to be, and Whisper sees each piece on its own. The
moment the recording stops, none of those constraints apply — the audio is
on disk in one file, and Whisper does its best work on exactly that. So a
finished session goes through the same whole-file pass an upload gets, and
the rows it produces replace the live ones.

Session status tells the client what is going on (see
:data:`wrenote.core.store.SESSION_STATUSES`): ``processing`` while this
runs, ``ready`` when the new rows are in, ``failed`` — with the old rows
still there — if it dies. The rows are swapped in one transaction at the
very end, so at no point does the user see an empty transcript.

What carries over: speaker labels, by time overlap (the user may have
renamed "Speaker 2" to a colleague, and that must survive). What doesn't:
text edits to the live rows — a fresh transcription is the point. The
client asks before a manual re-run on a session that was already refined.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import glossary
from .batch import normalize_src_lang, read_wav_pcm, transcribe_pcm
from .catalogue import ModelCatalogue, resolve
from .config import Config
from .jobs import JobRegistry, Phase
from .recording import resolve_recording_path
from .registry import make_translator
from .store import Store
from .translation import context_before, translate_one_for_segment

log = logging.getLogger(__name__)

REFINE_PHASES_TRANSLATE = [
    Phase("load_audio", 0.03),
    Phase("transcribe", 0.55),
    Phase("load_translator", 0.05),
    Phase("translate", 0.32),
    Phase("persist", 0.05),
]
REFINE_PHASES_NO_TRANSLATE = [
    Phase("load_audio", 0.05),
    Phase("transcribe", 0.88),
    Phase("persist", 0.07),
]

# Row ids the pass writes. Distinct from the live ``<uuid>`` rows and the
# upload's ``u-`` rows so a transcript's origin is legible in the DB.
_ROW_PREFIX = "r-"


class RefineError(RuntimeError):
    """A precondition failed; ``code`` is what the client shows."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def carry_speakers(
    old_rows: Sequence[dict[str, Any]],
    new_rows: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Give each new row the speaker of the old row it overlaps most in time.

    A new row that overlaps nothing labelled keeps no speaker. Returns the
    new rows (copies) with ``speaker`` set; the input is not modified.
    """
    labelled = [
        (float(r["started_at"]), float(r["ended_at"]), r["speaker"])
        for r in old_rows
        if r.get("speaker")
    ]
    out: list[dict[str, Any]] = []
    for row in new_rows:
        row = dict(row)
        if labelled:
            t0, t1 = float(row["started_at"]), float(row["ended_at"])
            best: str | None = None
            best_overlap = 0.0
            for s0, s1, label in labelled:
                overlap = min(t1, s1) - max(t0, s0)
                if overlap > best_overlap:
                    best_overlap, best = overlap, label
            row["speaker"] = best
        out.append(row)
    return out


async def refine_session(
    *,
    job_id: str,
    registry: JobRegistry,
    session: dict[str, Any],
    wav_path: Path,
    whisper_model_path: str,
    translator: Any | None,
    translate: bool,
    store: Store,
    glossary_entries: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Transcribe ``wav_path`` and replace ``session``'s rows with the result.

    The caller owns the job's terminal state and the session's ``processing``
    → ``ready``/``failed`` transition; this advances phases and raises.
    """
    sid = session["id"]
    old_rows: list[dict[str, Any]] = list(session.get("segments") or [])
    tgt_lang = str(session.get("tgt_lang") or "zh")
    requested_lang = normalize_src_lang(session.get("src_lang"))
    entries = glossary_entries or []

    def advance(phase_idx: int, inner: float = 0.0, line: str | None = None) -> None:
        registry.advance(job_id, phase_idx=phase_idx, phase_inner=inner, log_line=line)

    def tick(inner: float, line: str | None = None) -> None:
        registry.advance(job_id, phase_inner=inner, log_line=line)

    # ---- load_audio ----
    advance(0, 0.0, "Reading recording")
    pcm = await asyncio.get_event_loop().run_in_executor(None, read_wav_pcm, wav_path)
    if not pcm:
        raise RefineError("empty_recording")
    duration_s = len(pcm) / 2 / 16000
    tick(1.0, f"Recording: {duration_s:.1f}s")

    # ---- transcribe ----
    advance(1, 0.0, "Transcribing the whole recording")
    rows_raw = await transcribe_pcm(
        pcm,
        model_path=whisper_model_path,
        language=requested_lang,
        initial_prompt=glossary.stt_initial_prompt(entries),
    )
    tick(1.0, f"{len(rows_raw)} segments")
    fallback_lang = requested_lang or "en"
    new_rows: list[dict[str, Any]] = [
        {
            "segment_id": f"{_ROW_PREFIX}{i:04d}",
            "ord": i,
            "started_at": t0,
            "ended_at": t1,
            "orig_text": text,
            "orig_status": "final",
            "orig_lang": fallback_lang,
            "trans_text": "",
            "trans_status": "skipped",
            "trans_lang": tgt_lang,
        }
        for i, (text, t0, t1) in enumerate(rows_raw)
    ]
    new_rows = carry_speakers(old_rows, new_rows)

    # ---- translate ----
    translated = 0
    if translate:
        if translator is None:
            raise ValueError("translate=True but no translator provided")
        advance(2, 0.0, "Loading translator")
        await translator.load()
        tick(1.0)
        advance(3, 0.0, "Translating")
        texts = [r["orig_text"] for r in new_rows]
        total = max(1, len(new_rows))
        for i, row in enumerate(new_rows):
            text, status = await translate_one_for_segment(
                translator=translator,
                text=row["orig_text"],
                audio_lang=row["orig_lang"],
                tgt_lang=tgt_lang,
                context=context_before(texts, i),
            )
            row["trans_text"] = text
            row["trans_status"] = status
            if status == "final":
                translated += 1
            tick(
                (i + 1) / total,
                f"Translated {i + 1}/{total}" if (i + 1) % 5 == 0 or i + 1 == total else None,
            )
        persist_idx = 4
    else:
        persist_idx = 2

    # ---- persist: one transaction, so the old rows stand until the new are in ----
    advance(persist_idx, 0.0, "Writing transcript")
    await store.replace_segments(sid, new_rows)
    await store.update_session_duration(sid, duration_s)
    await store.mark_refined(sid, datetime.now(UTC).isoformat())
    tick(1.0)
    return {
        "session_id": sid,
        "n_segments": len(new_rows),
        "translated": translated,
        "duration_s": duration_s,
    }


async def launch(
    *,
    session: dict[str, Any],
    cfg: Config,
    catalogue: ModelCatalogue,
    store: Store,
    registry: JobRegistry,
    recordings_dir: Path,
    translate: bool | None = None,
) -> str:
    """Start the pass for ``session`` in the background; returns the job id.

    Checks the preconditions the caller can't know without the disk: the
    recording exists and the STT backend can do whole files. The session is
    ``processing`` by the time this returns, so a list fetched right after
    already shows it. ``translate`` defaults to whether the live session had
    translation (a transcribe-only session stays that way).
    """
    sid = session["id"]
    if registry.active_for(sid) is not None:
        raise RefineError("busy")
    if session.get("status") == "recording":
        raise RefineError("recording")
    wav = resolve_recording_path(sid, recordings_dir=recordings_dir)
    if not wav.exists():
        raise RefineError("no_recording")
    if cfg.stt_offline.backend != "whisper_cpp":
        raise RefineError("unsupported_backend")
    model_path = str(resolve(cfg, "stt_offline", catalogue).params.get("model_path") or "")
    if not model_path:
        raise RefineError("no_model")
    if translate is None:
        translate = any(
            r.get("trans_status") in ("final", "partial") and (r.get("trans_text") or "")
            for r in session.get("segments") or []
        )

    job = registry.create(
        kind="refine",
        phases=list(REFINE_PHASES_TRANSLATE if translate else REFINE_PHASES_NO_TRANSLATE),
        session_id=sid,
    )

    async def runner() -> None:
        translator: Any | None = None
        try:
            entries = await store.list_glossary()
            if translate:
                translator = make_translator(
                    cfg.translator.backend, resolve(cfg, "translator", catalogue).params
                )
                glossary.apply_to_backends(entries, translator=translator)
            result = await refine_session(
                job_id=job.id,
                registry=registry,
                session=session,
                wav_path=wav,
                whisper_model_path=model_path,
                translator=translator,
                translate=translate,
                store=store,
                glossary_entries=entries,
            )
            registry.complete(job.id, result=result)
        except Exception as e:
            log.exception("refine job %s (session %s) failed", job.id, sid)
            detail = e.code if isinstance(e, RefineError) else f"{type(e).__name__}: {e}"
            try:
                await store.set_session_status(sid, "failed", detail=detail)
            except Exception:
                log.exception("could not record refine failure for %s", sid)
            registry.fail(job.id, detail)
        finally:
            if translator is not None:
                try:
                    await translator.unload()
                except Exception:
                    pass

    await store.set_session_status(sid, "processing")
    task = asyncio.create_task(runner(), name=f"refine-{job.id[:8]}")
    # Hold a reference: the event loop keeps only a weak one, so an
    # unreferenced task can be collected mid-flight (RUF006).
    _background.add(task)
    task.add_done_callback(_background.discard)
    return job.id


_background: set[asyncio.Task[None]] = set()
