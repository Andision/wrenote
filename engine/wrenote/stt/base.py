"""STT backend abstract interface.

Per design.v1.1 §4.2.1.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable

from ..core.events import AudioSegment, BackendInfo, TranscriptEvent

PartialCallback = Callable[[TranscriptEvent], Awaitable[None]]


class STTBackend(ABC):
    """Speech-to-text backend.

    Lifecycle:
        ``__init__`` only stores configuration — no I/O.
        Callers must ``await load()`` before calling :meth:`transcribe_segment`.
        ``unload()`` may be followed by another ``load()`` (hot reload).

    Blocking work:
        Real inference is almost always a blocking C-extension call. Each
        implementation MUST wrap such work in ``asyncio.to_thread`` (or an
        equivalent executor) so the FastAPI event loop is not blocked.
    """

    @abstractmethod
    async def load(self) -> None:
        """Load the model into memory; raise on failure."""

    @abstractmethod
    async def unload(self) -> None:
        """Release the model. Idempotent (safe to call multiple times)."""

    @abstractmethod
    async def transcribe_segment(
        self,
        segment: AudioSegment,
        *,
        src_lang: str | None = None,
        on_partial: PartialCallback | None = None,
    ) -> TranscriptEvent:
        """Transcribe a complete VAD-segmented audio segment.

        Returns the *final* :class:`TranscriptEvent` (``type='final'``).

        If ``on_partial`` is provided, the implementation MAY invoke it during
        decoding to emit growing-buffer partials (``type='partial'``). P1-a
        implementations may ignore ``on_partial`` and return only the final
        event; growing-buffer support is a P1-c upgrade.

        Chunking strategy is a backend-internal detail.
        """

    def set_initial_prompt(self, prompt: str) -> None:  # noqa: B027 — optional hook, no-op default
        """Soft-bias decoding toward a custom vocabulary (glossary terms).

        Default no-op; backends with a decoder prompt (Whisper) override.
        Set per session before :meth:`transcribe_segment`.
        """

    @property
    @abstractmethod
    def info(self) -> BackendInfo:
        """Backend metadata (name, model, device, etc.)."""
