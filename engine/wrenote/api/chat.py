"""Chat conversations + messages (per-session threads) and LLM title suggestion."""
from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from ..chat.base import ChatMessage
from ..core.store import Store
from ..core.transcript import build_chat_system_prompt, build_transcript_snapshot
from ..deps import get_models, get_store
from ..model_manager import ModelManager
from ._common import (
    require_conversation,
    safe_conversation_id,
    safe_session_id,
)

log = logging.getLogger(__name__)
router = APIRouter()


@router.get("/sessions/{session_id}/conversations")
async def list_conversations(
    session_id: str, store: Store = Depends(get_store)
) -> dict[str, Any]:
    sid = safe_session_id(session_id)
    return {"conversations": await store.list_conversations(sid)}


@router.post("/sessions/{session_id}/conversations")
async def create_conversation(
    session_id: str, request: Request, store: Store = Depends(get_store)
) -> dict[str, Any]:
    sid = safe_session_id(session_id)
    if await store.get_session(sid) is None:
        raise HTTPException(status_code=404, detail="session not found")
    try:
        body = await request.json()
    except Exception:
        body = {}
    title = (body.get("title") or "").strip() if isinstance(body, dict) else ""
    conv_id = uuid.uuid4().hex
    now = datetime.now(UTC).isoformat()
    await store.create_conversation(
        conversation_id=conv_id, session_id=sid, title=title, created_at=now,
    )
    conv = await store.get_conversation(conv_id)
    return {"conversation": {**(conv or {}), "message_count": 0}}


@router.patch("/sessions/{session_id}/conversations/{conversation_id}")
async def rename_conversation(
    session_id: str, conversation_id: str, request: Request,
    store: Store = Depends(get_store),
) -> dict[str, str]:
    sid = safe_session_id(session_id)
    cid = safe_conversation_id(conversation_id)
    await require_conversation(store, sid, cid)
    body = await request.json()
    title = (body.get("title") or "").strip()
    await store.rename_conversation(cid, title)
    return {"status": "ok"}


@router.delete("/sessions/{session_id}/conversations/{conversation_id}")
async def delete_conversation(
    session_id: str, conversation_id: str, store: Store = Depends(get_store)
) -> dict[str, str]:
    sid = safe_session_id(session_id)
    cid = safe_conversation_id(conversation_id)
    await require_conversation(store, sid, cid)
    await store.delete_conversation(cid)
    return {"status": "ok"}


@router.get("/sessions/{session_id}/conversations/{conversation_id}/chat")
async def list_conversation_chat(
    session_id: str, conversation_id: str, store: Store = Depends(get_store)
) -> dict[str, Any]:
    sid = safe_session_id(session_id)
    cid = safe_conversation_id(conversation_id)
    await require_conversation(store, sid, cid)
    return {"messages": await store.list_chat_messages(cid)}


@router.delete("/sessions/{session_id}/conversations/{conversation_id}/chat")
async def clear_conversation_chat(
    session_id: str, conversation_id: str, store: Store = Depends(get_store)
) -> dict[str, str]:
    sid = safe_session_id(session_id)
    cid = safe_conversation_id(conversation_id)
    await require_conversation(store, sid, cid)
    await store.clear_chat(cid)
    return {"status": "ok"}


@router.post("/sessions/{session_id}/conversations/{conversation_id}/chat")
async def post_conversation_chat(
    session_id: str, conversation_id: str, request: Request,
    store: Store = Depends(get_store),
    models: ModelManager = Depends(get_models),
) -> StreamingResponse:
    """Stream the assistant reply for the user's next message in a thread.

    Body: ``{"text": "..."}``. Response: ``text/plain`` chunks. The server
    snapshots the session transcript at request time, prepends it as a
    system message, appends this conversation's prior history, then the new
    user message. Both user and assistant messages are persisted (user
    up-front, assistant after the stream completes), and the conversation's
    ``updated_at`` is bumped so it floats to the top of the thread list.
    """
    sid = safe_session_id(session_id)
    cid = safe_conversation_id(conversation_id)
    body = await request.json()
    text = (body.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text required")

    session = await store.get_session(sid)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    conv = await require_conversation(store, sid, cid)

    history_rows = await store.list_chat_messages(cid)

    # Lazy-load the model on first chat in this server's lifetime.
    backend = await models.ensure_chat_loaded()

    system_msg = ChatMessage(
        role="system",
        content=build_chat_system_prompt(session.get("segments", [])),
    )
    history = [ChatMessage(role=r["role"], content=r["content"]) for r in history_rows]
    user_msg = ChatMessage(role="user", content=text)
    messages = [system_msg, *history, user_msg]

    # Persist the user turn up-front so a dropped connection still leaves
    # the question visible next time the panel loads.
    now = datetime.now(UTC).isoformat()
    await store.append_chat_message(
        conversation_id=cid, role="user", content=text, created_at=now,
    )
    await store.touch_conversation(cid, now)
    # Give an untitled thread a label from its first user message.
    if not (conv.get("title") or "").strip():
        derived = text.strip().splitlines()[0][:48]
        await store.rename_conversation(cid, derived)

    async def stream() -> Any:
        accumulated: list[str] = []
        try:
            chunks = await backend.chat(messages)
            async for piece in chunks:
                accumulated.append(piece)
                yield piece
        except Exception as e:
            log.exception("chat stream errored")
            err = f"\n\n[ERROR] {type(e).__name__}: {e}"
            accumulated.append(err)
            yield err
        finally:
            full = "".join(accumulated)
            if full.strip():
                try:
                    ts = datetime.now(UTC).isoformat()
                    await store.append_chat_message(
                        conversation_id=cid,
                        role="assistant",
                        content=full,
                        created_at=ts,
                    )
                    await store.touch_conversation(cid, ts)
                except Exception:
                    log.exception("failed to persist assistant message")

    return StreamingResponse(stream(), media_type="text/plain; charset=utf-8")


_TITLE_SYSTEM = (
    "You write a short, specific title for a transcript — 3 to 6 words, in the "
    "transcript's own language. No quotes, no trailing punctuation, no prefix "
    "like 'Title:'. Reply with the title only."
)


@router.post("/sessions/{session_id}/title/suggest")
async def suggest_title(
    session_id: str,
    store: Store = Depends(get_store),
    models: ModelManager = Depends(get_models),
) -> dict[str, str]:
    """Summarize a concise title for the session from its transcript using the
    chat model, persist it, and return it. Best-effort: if there's nothing to
    summarize the existing title is returned unchanged."""
    sid = safe_session_id(session_id)
    session = await store.get_session(sid)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")

    transcript, _ = build_transcript_snapshot(session.get("segments", []))
    current = session.get("title", "")
    if not transcript.strip():
        return {"title": current}

    backend = await models.ensure_chat_loaded()
    messages = [
        ChatMessage(role="system", content=_TITLE_SYSTEM),
        ChatMessage(
            role="user",
            content=f"Transcript:\n\n{transcript}\n\nTitle:",
        ),
    ]
    try:
        parts: list[str] = []
        chunks = await backend.chat(messages)
        async for piece in chunks:
            parts.append(piece)
        raw = "".join(parts).strip().strip('"').strip("'")
        title = raw.splitlines()[0].strip()[:80] if raw else ""
    except Exception:
        log.exception("title suggestion failed")
        title = ""

    if title:
        await store.update_session_title(sid, title)
        return {"title": title}
    return {"title": current}
