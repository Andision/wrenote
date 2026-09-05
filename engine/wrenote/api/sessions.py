"""Session CRUD endpoints (SQLite-backed)."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse

from ..core import export as export_mod
from ..core.jobs import JobRegistry
from ..core.recording import resolve_recording_path
from ..core.store import Store
from ..deps import get_jobs, get_recordings_dir, get_store
from ._common import safe_session_id

log = logging.getLogger(__name__)
router = APIRouter()


def _with_job(row: dict[str, Any], jobs: JobRegistry) -> dict[str, Any]:
    """A session in ``processing`` names the job doing it, so a client that
    finds one (after a reload, or on another tab) can follow its progress."""
    job = jobs.active_for(row["id"]) if row.get("status") == "processing" else None
    row["job_id"] = job.id if job is not None else None
    return row


@router.get("/sessions")
async def list_sessions(
    store: Store = Depends(get_store), jobs: JobRegistry = Depends(get_jobs)
) -> dict[str, Any]:
    return {"sessions": [_with_job(r, jobs) for r in await store.list_sessions()]}


@router.get("/sessions/{session_id}")
async def get_session(
    session_id: str,
    store: Store = Depends(get_store),
    jobs: JobRegistry = Depends(get_jobs),
) -> dict[str, Any]:
    sid = safe_session_id(session_id)
    sess = await store.get_session(sid)
    if sess is None:
        raise HTTPException(status_code=404, detail="session not found")
    return _with_job(sess, jobs)


@router.get("/sessions/{session_id}/export")
async def export_session(
    session_id: str,
    fmt: str = "md",
    content: str = "both",
    store: Store = Depends(get_store),
) -> PlainTextResponse:
    """Export the transcript as text. ``fmt`` = md|txt|srt|vtt;
    ``content`` = original|translation|both. Returned as text so the frontend
    can copy it or save it client-side with a chosen filename."""
    sid = safe_session_id(session_id)
    if content not in ("original", "translation", "both"):
        raise HTTPException(status_code=400, detail="invalid content")
    sess = await store.get_session(sid)
    if sess is None:
        raise HTTPException(status_code=404, detail="session not found")
    try:
        text, mime, _ext = export_mod.export_transcript(sess, fmt, content)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return PlainTextResponse(text, media_type=mime)


@router.patch("/sessions/{session_id}")
async def patch_session(
    session_id: str,
    request: Request,
    store: Store = Depends(get_store),
) -> dict[str, str]:
    """Currently only supports renaming. Body: ``{"title": "..."}``."""
    sid = safe_session_id(session_id)
    body = await request.json()
    title = body.get("title")
    if not isinstance(title, str) or not title.strip():
        raise HTTPException(status_code=400, detail="title required")
    await store.update_session_title(sid, title.strip())
    return {"status": "ok"}


@router.delete("/sessions/{session_id}")
async def delete_session(
    session_id: str,
    store: Store = Depends(get_store),
    recordings_dir: Path = Depends(get_recordings_dir),
) -> dict[str, str]:
    """Delete the session row (cascades to segments) AND the WAV file."""
    sid = safe_session_id(session_id)
    existed = await store.delete_session(sid)
    # Always try to remove the WAV — file may exist without a DB row if
    # a previous run died mid-session.
    wav = resolve_recording_path(sid, recordings_dir=recordings_dir)
    if wav.exists():
        try:
            wav.unlink()
        except Exception:
            log.exception("failed to remove recording %s", wav)
    return {"status": "ok" if existed else "not_found"}
