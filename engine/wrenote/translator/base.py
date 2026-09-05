"""Translator backend abstract interface.

Per design.v1.1 §4.2.3.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence

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
        context: Sequence[str] = (),
    ) -> str:
        """Translate ``text`` from ``src`` language to ``tgt``.

        ``context`` is the source text that came just before ``text`` (oldest
        first). A sentence on its own loses what "it", a bare number or a
        dropped subject refer to; the previous line or two usually settles
        it. Backends that build a prompt include it marked as not-to-translate;
        others may ignore it. Only ``text`` is translated either way.

        Raises :class:`asyncio.TimeoutError` if the call exceeds ``timeout_s``.
        Pipeline catches this and emits an ErrorEvent with code
        ``TRANSLATION_TIMEOUT`` (see design.v1.1 §5.4).
        """

    def set_glossary(self, pairs: list[tuple[str, str]]) -> None:  # noqa: B027 — optional hook
        """Pin ``(term, translation)`` renderings for consistency.

        Default no-op; backends that build a prompt (llama_cpp) override.
        """

    @property
    @abstractmethod
    def info(self) -> BackendInfo:
        ...
