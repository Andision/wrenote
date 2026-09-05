"""VAD backend abstract interface.

Per design.v1.1 §4.2.2. VAD only classifies frames; segment boundary and
segment_id management live in the Pipeline (see core/pipeline.py).
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from ..core.events import AudioChunk, BackendInfo


class VADBackend(ABC):
    """Voice activity detection backend.

    Lifecycle:
        Same as :class:`STTBackend` — no I/O in ``__init__``; ``await load()``
        before calling :meth:`is_speech`.

    Performance:
        ``is_speech`` is invoked for every audio frame (~100 ms). Implementations
        should execute in well under 5 ms per call. Heavier implementations
        must wrap blocking work via ``asyncio.to_thread``.
    """

    @abstractmethod
    async def load(self) -> None:
        ...

    @abstractmethod
    async def is_speech(self, chunk: AudioChunk) -> bool:
        """Return ``True`` if this frame contains speech."""

    @property
    @abstractmethod
    def info(self) -> BackendInfo:
        ...
