"""Passthrough VAD: every frame is reported as speech.

Useful when:

* Running unit tests that drive segment boundaries manually.
* Push-to-talk style clients where the client owns the segment start/end.

Caveat: with this VAD alone, the Pipeline will never observe ``speech_end``
and segments never close. Combine with a Pipeline that supports forced
segment boundaries (planned for a later iteration) or use only in tests
that emit explicit boundaries.
"""
from __future__ import annotations

from ..core.events import AudioChunk, BackendInfo
from ..core.registry import register_vad
from .base import VADBackend


@register_vad("disabled")
class DisabledVAD(VADBackend):
    async def load(self) -> None:
        return None

    async def is_speech(self, chunk: AudioChunk) -> bool:
        return True

    @property
    def info(self) -> BackendInfo:
        return BackendInfo(
            name="disabled_vad",
            version="0.1",
            model="passthrough",
            device="cpu",
        )
