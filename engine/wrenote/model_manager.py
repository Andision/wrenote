"""Lazy-load lifecycle for the app-lifetime models (chat + offline diarize).

Replaces the four loose ``app.state.{chat,diarize}_{loaded,load_lock}`` flags
with one object stored at ``app.state.models``. Both backends are instantiated
up-front (cheap) but their weights load on first use, serialized so concurrent
first-requests don't double-load. Lives at the app layer (not ``core``) because
``ensure_diarize_loaded`` raises an HTTP 503 when diarization is disabled.
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import HTTPException

from .chat.base import ChatBackend
from .speaker.base import SpeakerBackend

log = logging.getLogger(__name__)


class ModelManager:
    def __init__(
        self,
        *,
        chat_backend: ChatBackend,
        diarize_speaker: SpeakerBackend | None,
    ) -> None:
        self._chat_backend = chat_backend
        self._chat_loaded = False
        self._chat_lock = asyncio.Lock()
        self._diarize_speaker = diarize_speaker
        self._diarize_loaded = False
        self._diarize_lock = asyncio.Lock()

    @property
    def chat_backend(self) -> ChatBackend:
        return self._chat_backend

    async def ensure_chat_loaded(self) -> ChatBackend:
        """Idempotent lazy-load of the chat model; returns the loaded backend."""
        if not self._chat_loaded:
            async with self._chat_lock:
                if not self._chat_loaded:
                    await self._chat_backend.load()
                    self._chat_loaded = True
        return self._chat_backend

    async def ensure_diarize_loaded(self) -> SpeakerBackend:
        """Lazy-load the speaker-embedding model; 503 when disabled in config."""
        if self._diarize_speaker is None:
            raise HTTPException(
                status_code=503, detail="speaker backend disabled in config"
            )
        if not self._diarize_loaded:
            async with self._diarize_lock:
                if not self._diarize_loaded:
                    await self._diarize_speaker.load()
                    self._diarize_loaded = True
        return self._diarize_speaker

    async def aclose(self) -> None:
        """Unload whatever was loaded. Best-effort; logs and continues."""
        if self._chat_loaded:
            try:
                await self._chat_backend.unload()
            except Exception:
                log.exception("chat backend unload failed")
        if self._diarize_loaded and self._diarize_speaker is not None:
            try:
                await self._diarize_speaker.unload()
            except Exception:
                log.exception("diarize speaker unload failed")
