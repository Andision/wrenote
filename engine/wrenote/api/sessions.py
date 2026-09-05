"""Session CRUD endpoints (SQLite-backed)."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse

from ..core import export as export_mod
from ..core import minutes as minutes_mod
from ..core.jobs import JobRegistry
from ..core.recording import resolve_recording_path
from ..core.store import Store
from ..deps import get_jobs, get_recordings_dir, get_store
from ._common import SAFE_SESSION_ID, safe_session_id

log = logging.getLogger(__name__)
router = APIRouter()


def _with_job(row: dict[str, Any], jobs: JobRegistry) -> dict[str, Any]:
    """A session in ``processing`` names the job doing it, so a client that
    finds one (after a reload, or on another tab) can follow its progress."""
    job = jobs.active_for(row["id"]) if row.get("status") == "processing" else None
    row["job_id"] = job.id if job is not None else None
    return row


def _decode_cursor(cursor: str) -> tuple[str, str]:
    """``<created_at>|<id>`` of the last row the client has."""
    created_at, sep, sid = cursor.rpartition("|")
    if not sep or not created_at or not SAFE_SESSION_ID.match(sid):
        raise HTTPException(status_code=400, detail="invalid cursor")
    return created_at, sid


@router.get("/sessions")
async def list_sessions(
    limit: int = Query(0, ge=0, le=500),
    cursor: str | None = None,
    store: Store = Depends(get_store),
    jobs: JobRegistry = Depends(get_jobs),
) -> dict[str, Any]:
    """Newest first. ``limit`` 0 = all (the default, for older clients);
    otherwise a page, with ``next_cursor`` to pass back for the one after —
    null when this was the last."""
    before = _decode_cursor(cursor) if cursor else None
    if limit == 0:
        rows = await store.list_sessions(before=before)
        next_cursor = None
    else:
        rows = await store.list_sessions(limit=limit + 1, before=before)
        more = len(rows) > limit
        rows = rows[:limit]
        next_cursor = f"{rows[-1]['created_at']}|{rows[-1]['id']}" if more and rows else None
    return {"sessions": [_with_job(r, jobs) for r in rows], "next_cursor": next_cursor}


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
    minutes: str = "",
    store: Store = Depends(get_store),
) -> PlainTextResponse:
    """Export the transcript as text. ``fmt`` = md|txt|srt|vtt;
    ``content`` = original|translation|both. ``minutes`` = a language code
    puts that language's minutes before the transcript (md and txt only;
    404 when the session has none in that language). Returned as text so
    the frontend can copy it or save it client-side with a chosen filename."""
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
    if minutes:
        if fmt not in ("md", "txt"):
            raise HTTPException(status_code=400, detail="minutes only in md or txt")
        row = next((r for r in await store.list_minutes(sid) if r["lang"] == minutes), None)
        if row is None:
            raise HTTPException(status_code=404, detail="minutes not found")
        doc = minutes_mod.row_to_public(row, "")["content"]
        text = export_mod.with_minutes(text, minutes_mod.to_markdown(doc, minutes), fmt)
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
