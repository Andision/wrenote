"""Mock STT backend.

Returns a fake transcript after a configurable delay. Used to validate the
pipeline plumbing without needing a real model. Optionally emits growing-buffer
partials, which is helpful when stress-testing the P1-c partial pathway.
"""
from __future__ import annotations

import asyncio

from ..core.events import AudioSegment, BackendInfo, TranscriptEvent
from ..core.registry import register_stt
from .base import PartialCallback, STTBackend


@register_stt("mock")
class MockSTTBackend(STTBackend):
    def __init__(
        self,
        *,
        delay_s: float = 0.2,
        text: str | None = None,
        partial_count: int = 0,
    ) -> None:
        """
        Args:
            delay_s: Simulated inference time per segment.
            text: Static text to return. If None, generates ``[mock] N.NNs``.
            partial_count: If >0 and ``on_partial`` is provided, emit N
                growing partials spaced through the delay before the final.
        """
        self._delay_s = delay_s
        self._static_text = text
        self._partial_count = partial_count
        self._loaded = False

    async def load(self) -> None:
        self._loaded = True

    async def unload(self) -> None:
        self._loaded = False

    async def transcribe_segment(
        self,
        segment: AudioSegment,
        *,
        src_lang: str | None = None,
        on_partial: PartialCallback | None = None,
    ) -> TranscriptEvent:
        if not self._loaded:
            raise RuntimeError("MockSTTBackend not loaded; call load() first")

        text = self._static_text or f"[mock] {(segment.t1 - segment.t0):.2f}s"
        lang = src_lang or "en"

        if on_partial and self._partial_count > 0:
            step = self._delay_s / (self._partial_count + 1)
            for i in range(self._partial_count):
                await asyncio.sleep(step)
                cutoff = max(1, len(text) * (i + 1) // (self._partial_count + 1))
                await on_partial(
                    TranscriptEvent(
                        type="partial",
                        segment_id=segment.segment_id,
                        text=text[:cutoff],
                        lang=lang,
                    )
                )
            await asyncio.sleep(step)
        else:
            await asyncio.sleep(self._delay_s)

        return TranscriptEvent(
            type="final",
            segment_id=segment.segment_id,
            text=text,
            lang=lang,
            t0=segment.t0,
            t1=segment.t1,
        )

    @property
    def info(self) -> BackendInfo:
        return BackendInfo(
            name="mock_stt",
            version="0.1",
            model="mock",
            device="cpu",
            supported_languages=["en", "zh"],
            capabilities={"partial": self._partial_count > 0},
        )
