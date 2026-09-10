"""System audio: mixing it into the mic, or being the whole recording.

A :class:`SystemAudioSource` (see :mod:`wrenote.platform.base`) produces 16 kHz
mono s16le PCM of the system output. Which one exists — ScreenCaptureKit on
macOS, WASAPI loopback on Windows, none elsewhere — is the platform adapter's
call. Two ways to consume it:

* :class:`SystemAudioMixer` mixes it into the live mic stream, driven by
  mic-frame arrival so the pipeline keeps the mic's real-time cadence.
* :class:`SystemAudioPump` *is* the cadence, for a session with no mic —
  "record the meeting, not me". The clock has to come from somewhere, and
  with the mic off the only real-time thing left is the system source itself,
  so the pump reads a frame every :data:`FRAME_MS` and pads with silence when
  the source is momentarily behind. Padding rather than stalling keeps the
  transcript's timeline honest: a gap in the audio *is* silence.

Exact sample-sync isn't needed for transcription. Falls back to mic-only when
no source is available (unsupported platform / missing permission / missing lib).
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

import numpy as np

from ..platform import SystemAudioSource, get_platform
from ..platform.base import SAMPLE_RATE

__all__ = [
    "FRAME_BYTES",
    "FRAME_MS",
    "SAMPLE_RATE",
    "SystemAudioMixer",
    "SystemAudioPump",
    "SystemAudioSource",
    "make_system_audio_source",
]

log = logging.getLogger(__name__)

#: The mic worklet batches 100 ms (clients/web/src/lib/audio-worklet.ts), and
#: the VAD is tuned to that; a mic-less session should look the same to the
#: pipeline as one with a very quiet microphone.
FRAME_MS = 100
FRAME_BYTES = SAMPLE_RATE * 2 * FRAME_MS // 1000


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


class SystemAudioPump:
    """Feeds the pipeline from the system source alone, on its own clock.

    ``on_frame`` gets one :data:`FRAME_BYTES` frame every :data:`FRAME_MS`,
    and is whatever the mic frames would have gone through — the pipeline and
    the WAV writer. Paused sessions stop feeding, exactly as the client stops
    sending mic frames when it pauses.
    """

    def __init__(self, on_frame: Callable[[bytes], Awaitable[None]]) -> None:
        self._source: SystemAudioSource | None = make_system_audio_source()
        self._on_frame = on_frame
        self._task: asyncio.Task[None] | None = None
        self._paused = False

    async def start(self) -> bool:
        if self._source is None:
            log.info("no system-audio source for platform %s", get_platform().name)
            return False
        if not await self._source.start():
            return False
        self._task = asyncio.create_task(self._run(), name="syscap.pump")
        return True

    def set_paused(self, paused: bool) -> None:
        self._paused = paused

    async def _run(self) -> None:
        # A monotonic schedule rather than `sleep(FRAME_MS)`: the latter
        # accumulates the loop's own overhead into drift, and an hour of
        # meeting is 36,000 frames to drift over.
        period = FRAME_MS / 1000
        next_at = asyncio.get_running_loop().time()
        silence = bytes(FRAME_BYTES)
        try:
            while True:
                next_at += period
                await asyncio.sleep(max(0.0, next_at - asyncio.get_running_loop().time()))
                if self._paused or self._source is None:
                    continue
                frame = self._source.read(FRAME_BYTES)
                if len(frame) < FRAME_BYTES:
                    frame += silence[: FRAME_BYTES - len(frame)]
                await self._on_frame(frame)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("system-audio pump stopped")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        if self._source is not None:
            await self._source.stop()
