"""Session CRUD endpoints (SQLite-backed)."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from ..core.recording import resolve_recording_path
from ..core.store import Store
from ..deps import get_store
from ._common import safe_session_id

log = logging.getLogger(__name__)
router = APIRouter()


@router.get("/sessions")
async def list_sessions(store: Store = Depends(get_store)) -> dict[str, Any]:
    return {"sessions": await store.list_sessions()}


@router.get("/sessions/{session_id}")
async def get_session(session_id: str, store: Store = Depends(get_store)) -> dict[str, Any]:
    sid = safe_session_id(session_id)
    sess = await store.get_session(sid)
    if sess is None:
        raise HTTPException(status_code=404, detail="session not found")
    return sess


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
async def delete_session(session_id: str, store: Store = Depends(get_store)) -> dict[str, str]:
    """Delete the session row (cascades to segments) AND the WAV file."""
    sid = safe_session_id(session_id)
    existed = await store.delete_session(sid)
    # Always try to remove the WAV — file may exist without a DB row if
    # a previous run died mid-session.
    wav = resolve_recording_path(sid)
    if wav.exists():
        try:
            wav.unlink()
        except Exception:
            log.exception("failed to remove recording %s", wav)
    return {"status": "ok" if existed else "not_found"}
