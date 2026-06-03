"""Shared route helpers: request-input validation and model lazy-loading.

Lives below the routers (and is import-cycle-free) so both ``api/*`` and the
WebSocket handler can use these without importing ``server``. The two
``ensure_*`` helpers will fold into a ``core.model_manager.ModelManager`` in a
later phase; for now they stay here as the single home shared by the chat and
diarize routers.
"""
from __future__ import annotations

import re
from typing import Any

from fastapi import FastAPI, HTTPException

from ..core.store import Store
from ..speaker.base import SpeakerBackend

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


async def ensure_chat_loaded(app: FastAPI) -> None:
    """Idempotent lazy-load of the chat model. Serialized so concurrent
    first-requests don't try to load twice."""
    if app.state.chat_loaded:
        return
    async with app.state.chat_load_lock:
        if app.state.chat_loaded:
            return
        await app.state.chat_backend.load()
        app.state.chat_loaded = True


async def ensure_diarize_loaded(app: FastAPI) -> SpeakerBackend:
    """Lazy-load the speaker embedding model for offline diarization."""
    if app.state.diarize_speaker is None:
        raise HTTPException(
            status_code=503, detail="speaker backend disabled in config"
        )
    if app.state.diarize_loaded:
        return app.state.diarize_speaker
    async with app.state.diarize_load_lock:
        if not app.state.diarize_loaded:
            await app.state.diarize_speaker.load()
            app.state.diarize_loaded = True
    return app.state.diarize_speaker
