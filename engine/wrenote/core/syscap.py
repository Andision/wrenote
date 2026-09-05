"""System-audio mixing (platform-agnostic).

A :class:`SystemAudioSource` (see :mod:`wrenote.platform.base`) produces 16 kHz
mono s16le PCM of the system output; :class:`SystemAudioMixer` mixes it into
the live mic stream, driven by mic-frame arrival so the pipeline keeps the
mic's real-time cadence. Which source exists — ScreenCaptureKit on macOS,
WASAPI loopback on Windows, none elsewhere — is the platform adapter's call.

Exact sample-sync isn't needed for transcription. Falls back to mic-only when
no source is available (unsupported platform / missing permission / missing lib).
"""
from __future__ import annotations

import logging

import numpy as np

from ..platform import SystemAudioSource, get_platform
from ..platform.base import SAMPLE_RATE

__all__ = ["SAMPLE_RATE", "SystemAudioMixer", "SystemAudioSource", "make_system_audio_source"]

log = logging.getLogger(__name__)


def make_system_audio_source() -> SystemAudioSource | None:
    return get_platform().make_system_audio_source()


class SystemAudioMixer:
    """Mixes a SystemAudioSource into mic frames before the pipeline."""

    def __init__(self) -> None:
        self._source: SystemAudioSource | None = make_system_audio_source()

    async def start(self) -> bool:
        if self._source is None:
            log.info("no system-audio source for platform %s", get_platform().name)
            return False
        return await self._source.start()

    async def mix(self, mic_pcm: bytes) -> bytes:
        if self._source is None:
            return mic_pcm
        sys_bytes = self._source.read(len(mic_pcm))
        if not sys_bytes:
            return mic_pcm
        mic = np.frombuffer(mic_pcm, dtype=np.int16).astype(np.int32)
        sysa = np.frombuffer(sys_bytes, dtype=np.int16).astype(np.int32)
        m = min(mic.size, sysa.size)
        mixed = mic.copy()
        mixed[:m] += sysa[:m]
        np.clip(mixed, -32768, 32767, out=mixed)
        return mixed.astype(np.int16).tobytes()

    async def stop(self) -> None:
        if self._source is not None:
            await self._source.stop()
