"""Shared route helpers: request-input validation.

Lives below the routers (and is import-cycle-free) so both ``api/*`` and the
WebSocket handler can use these without importing ``server``. Model lazy-loading
now lives in :class:`wrenote.model_manager.ModelManager` (``app.state.models``).
"""
from __future__ import annotations

import re
from typing import Any

from fastapi import HTTPException

from ..core.store import Store

# Session/conversation IDs must be filesystem-safe; we accept UUIDs and
# slug-like strings only, to avoid path-traversal via the recording endpoints.
SAFE_SESSION_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


def safe_session_id(session_id: str) -> str:
    """Reject anything that could escape the recordings dir."""
    if not SAFE_SESSION_ID.match(session_id):
        raise HTTPException(status_code=400, detail="invalid session id")
    return session_id


def safe_conversation_id(conversation_id: str) -> str:
    if not SAFE_SESSION_ID.match(conversation_id):
        raise HTTPException(status_code=400, detail="invalid conversation id")
    return conversation_id


async def require_conversation(store: Store, sid: str, cid: str) -> dict[str, Any]:
    """Fetch a conversation, 404-ing unless it belongs to this session."""
    conv = await store.get_conversation(cid)
    if conv is None or conv["session_id"] != sid:
        raise HTTPException(status_code=404, detail="conversation not found")
    return conv
