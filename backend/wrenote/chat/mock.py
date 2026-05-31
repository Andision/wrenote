"""Mock chat backend — emits a canned response token-by-token.

Useful for frontend dev / smoke tests without loading the 2.8GB Qwen model.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence

from ..core.events import BackendInfo
from ..core.registry import register_chat
from .base import ChatBackend, ChatMessage


@register_chat("mock")
class MockChat(ChatBackend):
    def __init__(self, *, delay_ms: int = 20, **_ignored: object) -> None:
        # Swallow extra kwargs so a backend-override (e.g. env var flipping
        # `chat.backend: mock` while the YAML still has `model_path` etc.)
        # doesn't blow up with "unexpected keyword argument".
        self._delay_s = delay_ms / 1000.0
        self._loaded = False

    async def load(self) -> None:
        self._loaded = True

    async def unload(self) -> None:
        self._loaded = False

    async def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        max_tokens: int = 1024,
        temperature: float = 0.7,
    ) -> AsyncIterator[str]:
        if not self._loaded:
            raise RuntimeError("MockChat not loaded; call load() first")
        last_user = next(
            (m.content for m in reversed(messages) if m.role == "user"),
            "",
        )
        reply = (
            f"[mock] I see {len(messages)} messages in the history; the last "
            f"user message was {last_user[:40]!r}. The real Qwen3.5 backend "
            "would actually answer here."
        )

        async def _iter() -> AsyncIterator[str]:
            for word in reply.split():
                await asyncio.sleep(self._delay_s)
                yield word + " "

        return _iter()

    @property
    def info(self) -> BackendInfo:
        return BackendInfo(
            name="mock_chat",
            version="0.1",
            model="mock",
            device="cpu",
        )
