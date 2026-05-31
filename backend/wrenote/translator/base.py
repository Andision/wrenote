"""Translator backend abstract interface.

Per design.v1.1 §4.2.3.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from ..core.events import BackendInfo


class TranslatorBackend(ABC):
    """Text-to-text translation backend.

    Lifecycle and blocking-call rules match :class:`STTBackend`.
    """

    @abstractmethod
    async def load(self) -> None:
        ...

    @abstractmethod
    async def unload(self) -> None:
        ...

    @abstractmethod
    async def translate(
        self,
        text: str,
        *,
        src: str,
        tgt: str,
        timeout_s: float = 10.0,
    ) -> str:
        """Translate ``text`` from ``src`` language to ``tgt``.

        Raises :class:`asyncio.TimeoutError` if the call exceeds ``timeout_s``.
        Pipeline catches this and emits an ErrorEvent with code
        ``TRANSLATION_TIMEOUT`` (see design.v1.1 §5.4).
        """

    @property
    @abstractmethod
    def info(self) -> BackendInfo:
        ...
