"""Chat backend abstract interface.

Async streaming: ``chat()`` yields text chunks as the model produces them,
so the UI can render token-by-token. The backend is one model instance
shared across sessions on the same server process (Qwen3.5-4B is too big to
spin up per-session). Concurrency: one in-flight chat at a time, enforced
internally by a single-threaded executor like the translator does.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Literal

from ..core.events import BackendInfo

ChatRole = Literal["system", "user", "assistant"]


@dataclass
class ChatMessage:
    role: ChatRole
    content: str


class ChatBackend(ABC):
    """Streaming chat backend.

    Lifecycle and blocking-call rules match :class:`TranslatorBackend`:
    ``load()`` brings the model into memory; ``unload()`` releases it;
    inference runs on a dedicated worker thread to avoid blocking the
    asyncio loop and to serialize Metal access.
    """

    @abstractmethod
    async def load(self) -> None:
        ...

    @abstractmethod
    async def unload(self) -> None:
        ...

    @abstractmethod
    def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        max_tokens: int = 1024,
        temperature: float = 0.7,
    ) -> AsyncIterator[str]:
        """Stream the assistant reply for the given message history.

        Yields raw text chunks (usually a few characters each) in order.
        The caller is responsible for joining them and persisting the final
        message. Implementations must not buffer the whole response; the
        UX depends on first-token-out being fast.
        """

    @property
    @abstractmethod
    def info(self) -> BackendInfo:
        ...
