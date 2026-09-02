"""Offline speaker diarization + manual speaker labeling (per-session)."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from ..core import glossary
from ..core.config import Config
from ..core.diarize import diarize_session
from ..core.jobs import JobRegistry, Phase
from ..core.recording import resolve_recording_path
from ..core.registry import make_translator
from ..core.store import Store
from ..core.translation import (
    has_real_translations,
    translate_segments_for_session,
    translation_candidates,
)
from ..deps import get_config, get_jobs, get_models, get_store
from ..model_manager import ModelManager
from ._common import safe_session_id

log = logging.getLogger(__name__)
router = APIRouter()

_DIARIZE_PHASES = [
    Phase("load_model", 0.05),
    Phase("embed_and_cluster", 0.90),
    Phase("persist", 0.05),
]

_DIARIZE_RETRANSLATE_PHASES = [
    Phase("load_model", 0.05),
    Phase("embed_and_cluster", 0.60),
    Phase("persist", 0.05),
    Phase("load_translator", 0.05),
    Phase("translate", 0.25),
]


@router.post("/sessions/{session_id}/diarize")
async def diarize_endpoint(
    session_id: str,
    store: Store = Depends(get_store),
    registry: JobRegistry = Depends(get_jobs),
    models: ModelManager = Depends(get_models),
    cfg: Config = Depends(get_config),
) -> dict[str, str]:
    """Kick off offline diarization as a job. Returns ``{job_id}``
    immediately; subscribe to ``/jobs/{job_id}/stream`` for progress."""
    sid = safe_session_id(session_id)
    session = await store.get_session(sid)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    wav = resolve_recording_path(sid)
    if not wav.exists():
        raise HTTPException(
            status_code=400,
            detail="no recording on file for this session",
        )
    segments = session.get("segments", [])
    should_retranslate = has_real_translations(segments)

    job = registry.create(
        kind="diarize",
        phases=list(
            _DIARIZE_RETRANSLATE_PHASES
            if should_retranslate
            else _DIARIZE_PHASES
        ),
    )

    async def runner() -> None:
        translator: Any | None = None
        try:
            if not segments:
                registry.complete(
                    job.id, result={"session_id": sid, "n_speakers": 0, "labels": {}}
                )
                return

            # ---- Phase 0: load speaker model ----
            registry.advance(job.id, phase_idx=0, phase_inner=0.0,
                             log_line="Loading speaker model")
            speaker = await models.ensure_diarize_loaded()
            registry.advance(job.id, phase_inner=1.0)

            # ---- Phase 1: embed + cluster ----
            registry.advance(job.id, phase_idx=1, phase_inner=0.0,
                             log_line="Embedding segments")
            def on_progress(frac: float, log_line: str | None = None) -> None:
                registry.advance(job.id, phase_inner=frac, log_line=log_line)
            result = await diarize_session(
                wav_path=wav,
                segments=segments,
                speaker=speaker,
                on_progress=on_progress,
            )
            labels = result.labels
            resegmented = result.segments

            # ---- Phase 2: persist speaker-aware segments ----
            registry.advance(job.id, phase_idx=2, phase_inner=0.0,
                             log_line="Writing speaker-aware segments")
            if resegmented:
                await store.replace_segments(sid, resegmented)
            elif labels:
                await store.set_segment_speakers(sid, labels)
            registry.advance(job.id, phase_inner=1.0)

            translated = 0
            if should_retranslate and resegmented:
                tgt_lang = str(session.get("tgt_lang") or "zh")

                # ---- Phase 3: load translator only when the old transcript
                # had real translations. STT-only sessions stay diarize-only.
                registry.advance(
                    job.id,
                    phase_idx=3,
                    phase_inner=0.0,
                    log_line="Loading translator for resegmented transcript",
                )
                translator = make_translator(
                    cfg.translator.backend,
                    cfg.translator.params,
                )
                glossary.apply_to_backends(await store.list_glossary(), translator=translator)
                await translator.load()
                registry.advance(job.id, phase_inner=1.0)

                # ---- Phase 4: translate every new row because old
                # translations no longer align after boundaries/text changed.
                registry.advance(
                    job.id,
                    phase_idx=4,
                    phase_inner=0.0,
                    log_line="Retranslating speaker-aware segments",
                )
                candidates = translation_candidates(
                    resegmented,
                    only_missing=False,
                )
                translated = await translate_segments_for_session(
                    store=store,
                    session_id=sid,
                    session=session,
                    segments=candidates,
                    translator=translator,
                    tgt_lang=tgt_lang,
                    registry=registry,
                    job_id=job.id,
                )

            distinct = {v for v in labels.values() if v.startswith("Speaker ")}
            registry.complete(
                job.id,
                result={
                    "session_id": sid,
                    "n_speakers": len(distinct),
                    "n_labeled": len(labels),
                    "n_segments": len(resegmented) if resegmented else len(segments),
                    "resegmented": bool(resegmented),
                    "retranslated": bool(should_retranslate and resegmented),
                    "translated": translated,
                    "labels": labels,
                },
            )
        except Exception as e:
            log.exception("diarize job %s failed", job.id)
            registry.fail(job.id, f"{type(e).__name__}: {e}")
        finally:
            if translator is not None:
                try:
                    await translator.unload()
                except Exception:
                    pass

    asyncio.create_task(runner(), name=f"diarize-{job.id[:8]}")
    return {"job_id": job.id}


@router.patch("/sessions/{session_id}/speakers")
async def rename_speaker(
    session_id: str, request: Request, store: Store = Depends(get_store)
) -> dict[str, int]:
    """Body: ``{"from": "Speaker 1", "to": "Alice"}``. Renames every segment
    whose ``speaker`` matches ``from`` to ``to``. Returns count updated."""
    sid = safe_session_id(session_id)
    body = await request.json()
    old = (body.get("from") or "").strip()
    new = (body.get("to") or "").strip()
    if not old or not new:
        raise HTTPException(status_code=400, detail="from and to are required")
    if old == new:
        return {"updated": 0}
    n = await store.rename_speaker(sid, old, new)
    return {"updated": n}


@router.post("/sessions/{session_id}/segments/speaker")
async def assign_segment_speaker(
    session_id: str, request: Request, store: Store = Depends(get_store)
) -> dict[str, int]:
    """Body: ``{"segmentIds": [...], "speaker": "Alice"}``. Assigns a speaker
    to specific segments — used to label segments diarization left
    unidentified, without cascading across the whole session."""
    sid = safe_session_id(session_id)
    body = await request.json()
    seg_ids = body.get("segmentIds") or []
    speaker = (body.get("speaker") or "").strip()
    if not isinstance(seg_ids, list) or not seg_ids or not speaker:
        raise HTTPException(
            status_code=400, detail="segmentIds and speaker are required"
        )
    labels = {str(s): speaker for s in seg_ids}
    n = await store.set_segment_speakers(sid, labels)
    return {"updated": n}
