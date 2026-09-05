"""Transcript segment editing.

Layer 1: edit a segment's text (correct STT errors / tweak a translation).
Editing the original marks its translation stale so the Translate action will
refresh it. (Manual re-segmentation — split/merge — will land here too.)
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from ..core import resegment
from ..core.store import Store
from ..deps import get_store
from ._common import safe_session_id

router = APIRouter()


async def _resegment(store: Store, sid: str, transform) -> dict[str, object]:
    """Fetch → apply a resegment transform → persist via replace_segments."""
    session = await store.get_session(sid)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    try:
        new_segments = transform(session.get("segments", []))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    n = await store.replace_segments(sid, new_segments)
    return {"status": "ok", "n_segments": n}


@router.patch("/sessions/{session_id}/segments/{segment_id}")
async def edit_segment(
    session_id: str,
    segment_id: str,
    request: Request,
    store: Store = Depends(get_store),
) -> dict[str, str]:
    """Body: ``{"orig_text"?: str, "trans_text"?: str}`` — at least one. Editing
    ``orig_text`` flags the translation ``stale``; editing ``trans_text`` is a
    manual ``final`` override."""
    sid = safe_session_id(session_id)
    seg = safe_session_id(segment_id)  # same filesystem-safe charset
    body = await request.json()
    orig = body.get("orig_text")
    trans = body.get("trans_text")
    if orig is None and trans is None:
        raise HTTPException(status_code=400, detail="orig_text or trans_text required")
    if orig is not None and not isinstance(orig, str):
        raise HTTPException(status_code=400, detail="orig_text must be a string")
    if trans is not None and not isinstance(trans, str):
        raise HTTPException(status_code=400, detail="trans_text must be a string")
    ok = await store.update_segment_text(
        sid,
        seg,
        orig_text=orig.strip() if isinstance(orig, str) else None,
        trans_text=trans.strip() if isinstance(trans, str) else None,
    )
    if not ok:
        raise HTTPException(status_code=404, detail="segment not found")
    return {"status": "ok"}


@router.post("/sessions/{session_id}/segments/{segment_id}/split")
async def split_segment(
    session_id: str,
    segment_id: str,
    request: Request,
    store: Store = Depends(get_store),
) -> dict[str, object]:
    """Body: ``{"offset": int}`` — split the original text at that char offset
    into two segments (translation reset; rerun Translate to refresh)."""
    sid = safe_session_id(session_id)
    seg = safe_session_id(segment_id)
    body = await request.json()
    offset = body.get("offset")
    if not isinstance(offset, int) or offset < 0:
        raise HTTPException(status_code=400, detail="offset (non-negative int) required")
    return await _resegment(store, sid, lambda segs: resegment.split_segment(segs, seg, offset))


@router.post("/sessions/{session_id}/segments/{segment_id}/merge")
async def merge_segment(
    session_id: str,
    segment_id: str,
    store: Store = Depends(get_store),
) -> dict[str, object]:
    """Merge this segment with the one that follows it."""
    sid = safe_session_id(session_id)
    seg = safe_session_id(segment_id)
    return await _resegment(store, sid, lambda segs: resegment.merge_with_next(segs, seg))
