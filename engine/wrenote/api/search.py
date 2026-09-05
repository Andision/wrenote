"""Full-text search across the library."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from ..core import search as search_mod
from ..core.store import Store
from ..deps import get_store
from ._common import SAFE_SESSION_ID

router = APIRouter()


@router.get("/search")
async def search(
    q: str = Query(..., min_length=1, max_length=200),
    limit: int = Query(50, ge=1, le=200),
    session_id: str | None = None,
    store: Store = Depends(get_store),
) -> dict[str, Any]:
    """Segments whose original or translation contains ``q``, best first,
    with their full text (the client highlights ``q`` itself), plus
    sessions whose title does. ``session_id`` narrows to one session.
    """
    if session_id is not None and not SAFE_SESSION_ID.match(session_id):
        raise HTTPException(status_code=400, detail="invalid session_id")
    q = " ".join(q.split())
    if not q:
        raise HTTPException(status_code=400, detail="empty query")
    match = search_mod.phrase_query(q)
    like = search_mod.like_pattern(q) if match is None else None
    rows = await store.search_segments(
        match or "", limit=limit, session_id=session_id, like=like
    )
    sessions = (
        await store.search_session_titles(search_mod.like_pattern(q), limit=20)
        if session_id is None else []
    )
    return {
        "query": q,
        "segments": [
            {
                "session_id": r["session_id"],
                "session_title": r["session_title"],
                "session_created_at": r["session_created_at"],
                "segment_id": r["segment_id"],
                "ord": r["ord"],
                "started_at": r["started_at"],
                "speaker": r["speaker"],
                "orig_text": r["orig_text"] or "",
                "trans_text": r["trans_text"] or "",
            }
            for r in rows
        ],
        "sessions": sessions,
    }
