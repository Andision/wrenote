"""Mock translator backend.

Returns the input text with a configurable prefix. Useful for pipeline tests
that don't need actual translation quality.
"""
from __future__ import annotations

import asyncio

from ..core.events import BackendInfo
from ..core.registry import register_translator
from .base import TranslatorBackend


@register_translator("mock")
class MockTranslatorBackend(TranslatorBackend):
    def __init__(
        self,
        *,
        delay_s: float = 0.3,
        prefix: str = "[TRANSLATED]",
    ) -> None:
        self._delay_s = delay_s
        self._prefix = prefix
        self._loaded = False

    async def load(self) -> None:
        self._loaded = True

    async def unload(self) -> None:
        self._loaded = False

    async def translate(
        self,
        text: str,
        *,
        src: str,
        tgt: str,
        timeout_s: float = 10.0,
    ) -> str:
        if not self._loaded:
            raise RuntimeError("MockTranslatorBackend not loaded; call load() first")
        await asyncio.wait_for(asyncio.sleep(self._delay_s), timeout=timeout_s)
        return f"{self._prefix} {text}"

    @property
    def info(self) -> BackendInfo:
        return BackendInfo(
            name="mock_translator",
            version="0.1",
            model="mock",
            device="cpu",
            supported_languages=["en", "zh"],
        )
