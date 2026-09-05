"""Meeting minutes per session — read, (re)generate as a job, render."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse

from ..core import minutes as minutes_mod
from ..core.jobs import JobRegistry
from ..core.store import Store
from ..deps import get_jobs, get_models, get_store
from ..model_manager import ModelManager
from ._common import safe_session_id

log = logging.getLogger(__name__)
router = APIRouter()

_LANG_OK = set("abcdefghijklmnopqrstuvwxyz-")


def _lang(raw: Any, default: str) -> str:
    lang = str(raw or default).strip().lower()
    if not lang or len(lang) > 10 or set(lang) - _LANG_OK:
        raise HTTPException(status_code=400, detail="invalid lang")
    return lang


@router.get("/sessions/{session_id}/minutes")
async def get_minutes(
    session_id: str,
    store: Store = Depends(get_store),
    jobs: JobRegistry = Depends(get_jobs),
) -> dict[str, Any]:
    """Every language's minutes for the session, each with ``stale`` (the
    transcript changed since), plus the running job's id if one is writing."""
    sid = safe_session_id(session_id)
    session = await store.get_session(sid)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    current = minutes_mod.transcript_hash(session.get("segments") or [])
    rows = await store.list_minutes(sid)
    job = jobs.active_for(sid, kind="minutes")
    return {
        "minutes": [minutes_mod.row_to_public(r, current) for r in rows],
        "job_id": job.id if job else None,
        "job_lang": (job.result or {}).get("lang") if job else None,
    }


@router.post("/sessions/{session_id}/minutes")
async def write_minutes(
    session_id: str,
    request: Request,
    store: Store = Depends(get_store),
    models: ModelManager = Depends(get_models),
    registry: JobRegistry = Depends(get_jobs),
) -> dict[str, str]:
    """(Re)write the minutes in ``lang`` (body ``{"lang": "zh"}``; default the
    session's target language). Returns ``{job_id}``; 409 while one is running."""
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
    lang = _lang(body.get("lang"), str(session.get("tgt_lang") or "en"))
    if not minutes_mod.transcript_lines(session.get("segments") or []):
        raise HTTPException(status_code=400, detail="no_transcript")
    if registry.active_for(sid, kind="minutes") is not None:
        raise HTTPException(status_code=409, detail="busy")

    job = registry.create(kind="minutes", phases=list(minutes_mod.MINUTES_PHASES), session_id=sid)
    # The language is on the job from the start, so a client that finds the
    # job through GET knows which tab it is for.
    job.result = {"lang": lang}

    async def runner() -> None:
        try:
            result = await minutes_mod.run_job(
                job_id=job.id,
                registry=registry,
                session=session,
                lang=lang,
                backend_loader=models.ensure_chat_loaded,
                store=store,
            )
            registry.complete(job.id, result=result)
        except Exception as e:
            log.exception("minutes job %s failed", job.id)
            registry.fail(job.id, f"{type(e).__name__}: {e}")

    task = asyncio.create_task(runner(), name=f"minutes-{job.id[:8]}")
    _background.add(task)
    task.add_done_callback(_background.discard)
    return {"job_id": job.id}


@router.delete("/sessions/{session_id}/minutes")
async def delete_minutes(
    session_id: str, lang: str, store: Store = Depends(get_store)
) -> dict[str, str]:
    sid = safe_session_id(session_id)
    removed = await store.delete_minutes(sid, _lang(lang, ""))
    return {"status": "ok" if removed else "not_found"}


@router.get("/sessions/{session_id}/minutes/markdown")
async def minutes_markdown(
    session_id: str, lang: str, store: Store = Depends(get_store)
) -> PlainTextResponse:
    """The minutes in ``lang`` rendered as Markdown, titled with the session."""
    sid = safe_session_id(session_id)
    lang = _lang(lang, "")
    session = await store.get_session(sid)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    row = next((r for r in await store.list_minutes(sid) if r["lang"] == lang), None)
    if row is None:
        raise HTTPException(status_code=404, detail="minutes not found")
    doc = minutes_mod.row_to_public(row, "")["content"]
    text = minutes_mod.to_markdown(doc, lang, title=session.get("title") or "Untitled session")
    return PlainTextResponse(text, media_type="text/markdown; charset=utf-8")


_background: set[asyncio.Task[None]] = set()
