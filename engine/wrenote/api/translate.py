"""Retroactive (re)translation of an existing session — async job."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from ..core import glossary
from ..core.config import Config
from ..core.jobs import JobRegistry, Phase
from ..core.registry import make_translator
from ..core.store import Store
from ..core.translation import translate_segments_for_session, translation_candidates
from ..deps import get_config, get_jobs, get_store
from ._common import safe_session_id

log = logging.getLogger(__name__)
router = APIRouter()

_TRANSLATE_PHASES = [
    Phase("load_translator", 0.05),
    Phase("translate", 0.95),
]


@router.post("/sessions/{session_id}/translate")
async def translate_session(
    session_id: str,
    request: Request,
    store: Store = Depends(get_store),
    cfg: Config = Depends(get_config),
    registry: JobRegistry = Depends(get_jobs),
) -> dict[str, str]:
    """Retroactively translate any segments missing a translation.

    Useful for STT-only sessions: user records without translation, then
    later wants the translated version. Runs as a job; subscribe to
    ``/jobs/{job_id}/stream``. Body (optional): ``{"tgt_lang": "..."}``
    overrides the session's target lang.
    """
    sid = safe_session_id(session_id)
    body: dict[str, Any] = {}
    raw = await request.body()
    if raw:
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            pass

    session = await store.get_session(sid)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")

    tgt_lang = str(body.get("tgt_lang") or session["tgt_lang"] or "zh")
    # retranslate=True re-does every segment (replacing existing translations);
    # the default only fills in segments that are missing a translation.
    retranslate = bool(body.get("retranslate"))
    job = registry.create(kind="translate", phases=list(_TRANSLATE_PHASES))

    async def runner() -> None:
        translator = make_translator(cfg.translator.backend, cfg.translator.params)
        glossary.apply_to_backends(await store.list_glossary(), translator=translator)
        try:
            registry.advance(job.id, phase_idx=0, log_line="Loading translator")
            await translator.load()
            registry.advance(job.id, phase_inner=1.0)
            registry.advance(job.id, phase_idx=1, log_line="Translating")

            segs = session["segments"]
            # Default: only segments missing a real translation. When the
            # caller passes retranslate=True, every segment is a candidate and
            # existing translations get replaced.
            candidates = translation_candidates(segs, only_missing=not retranslate)
            done = await translate_segments_for_session(
                store=store,
                session_id=sid,
                session=session,
                segments=candidates,
                translator=translator,
                tgt_lang=tgt_lang,
                registry=registry,
                job_id=job.id,
            )

            await store.update_session_duration(sid, session.get("duration_s", 0.0))
            registry.complete(
                job.id,
                result={"session_id": sid, "tgt_lang": tgt_lang, "translated": done},
            )
        except Exception as e:
            log.exception("translate job %s failed", job.id)
            registry.fail(job.id, f"{type(e).__name__}: {e}")
        finally:
            try:
                await translator.unload()
            except Exception:
                pass

    task = asyncio.create_task(runner(), name=f"translate-{job.id[:8]}")
    # Hold a reference: the event loop keeps only a weak one, so an
    # unreferenced task can be collected mid-flight (RUF006).
    _background.add(task)
    task.add_done_callback(_background.discard)
    return {"job_id": job.id}


_background: set[asyncio.Task[None]] = set()
