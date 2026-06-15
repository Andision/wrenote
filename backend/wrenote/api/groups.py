"""Session groups (sidebar folders)."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from ..core.store import Store
from ..deps import get_store
from ._common import safe_session_id

router = APIRouter()


@router.get("/groups")
async def list_groups(store: Store = Depends(get_store)) -> dict[str, Any]:
    return {"groups": await store.list_groups()}


@router.post("/groups")
async def create_group(request: Request, store: Store = Depends(get_store)) -> dict[str, Any]:
    try:
        body = await request.json()
    except Exception:
        body = {}
    name = (body.get("name") or "New group").strip() if isinstance(body, dict) else "New group"
    existing = await store.list_groups()
    gid = uuid.uuid4().hex
    now = datetime.now(UTC).isoformat()
    await store.create_group(
        group_id=gid, name=name or "New group", created_at=now, position=len(existing)
    )
    return {"group": {"id": gid, "name": name or "New group", "created_at": now, "position": len(existing)}}


@router.patch("/groups/{group_id}")
async def rename_group(
    group_id: str, request: Request, store: Store = Depends(get_store)
) -> dict[str, str]:
    gid = safe_session_id(group_id)
    body = await request.json()
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name required")
    await store.rename_group(gid, name)
    return {"status": "ok"}


@router.delete("/groups/{group_id}")
async def delete_group(group_id: str, store: Store = Depends(get_store)) -> dict[str, str]:
    gid = safe_session_id(group_id)
    existed = await store.delete_group(gid)
    return {"status": "ok" if existed else "not_found"}


@router.patch("/sessions/{session_id}/group")
async def set_session_group(
    session_id: str, request: Request, store: Store = Depends(get_store)
) -> dict[str, str]:
    """Body: ``{"groupId": "<id>"|null}``. Move a session into a group (or out
    of all groups when null)."""
    sid = safe_session_id(session_id)
    body = await request.json()
    group_id = body.get("groupId")
    if group_id is not None and not isinstance(group_id, str):
        raise HTTPException(status_code=400, detail="groupId must be a string or null")
    gid = safe_session_id(group_id) if group_id else None
    await store.set_session_group(sid, gid)
    return {"status": "ok"}
