"""System-audio capture + mixing, behind a platform-abstracted source.

A :class:`SystemAudioSource` produces 16 kHz mono s16le PCM of the system
output; :class:`SystemAudioMixer` mixes it into the live mic stream (driven by
mic-frame arrival, so the pipeline keeps the mic's real-time cadence). The
source is platform-specific and pluggable — this is also the seam a future
screen+audio recorder hangs off of:

* macOS   -> ScreenCaptureKit via the bundled ``syscap`` helper
* Windows -> WASAPI loopback via the ``soundcard`` library

Exact sample-sync isn't needed for transcription. Falls back to mic-only when no
source is available (unsupported platform / missing permission / missing lib).
"""
from __future__ import annotations

import abc
import asyncio
import logging
import sys
import threading
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

SAMPLE_RATE = 16_000
_BUFFER_CAP_BYTES = SAMPLE_RATE * 2 * 2  # ~2 s of 16 kHz mono s16le; drop older if behind


class SystemAudioSource(abc.ABC):
    """Produces system-output PCM (16 kHz mono s16le). Thread-safe buffer so a
    blocking native producer (helper subprocess or capture thread) and the async
    mixer can share it without an event-loop hop."""

    def __init__(self) -> None:
        self._buf = bytearray()
        self._lock = threading.Lock()

    def _feed(self, data: bytes) -> None:
        with self._lock:
            self._buf.extend(data)
            if len(self._buf) > _BUFFER_CAP_BYTES:
                del self._buf[: len(self._buf) - _BUFFER_CAP_BYTES]

    def read(self, n: int) -> bytes:
        """Pop up to ``n`` bytes of buffered system PCM."""
        with self._lock:
            out = bytes(self._buf[:n])
            del self._buf[:n]
            return out

    @abc.abstractmethod
    async def start(self) -> bool:
        """Begin capture. Returns False if unavailable (caller goes mic-only)."""

    @abc.abstractmethod
    async def stop(self) -> None: ...


# ---------- macOS: ScreenCaptureKit helper ----------


def _mac_helper_path() -> Path | None:
    candidates: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates += [Path(meipass) / "syscap", Path(sys.executable).resolve().parent / "syscap"]
    candidates.append(
        Path(__file__).resolve().parent.parent.parent / "packaging" / "macos" / "syscap"
    )
    return next((c for c in candidates if c.exists()), None)


class MacSystemAudioSource(SystemAudioSource):
    def __init__(self) -> None:
        super().__init__()
        self._proc: asyncio.subprocess.Process | None = None
        self._reader: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None

    async def start(self) -> bool:
        helper = _mac_helper_path()
        if helper is None:
            log.warning("syscap helper not found; system audio disabled")
            return False
        try:
            self._proc = await asyncio.create_subprocess_exec(
                str(helper),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except Exception:
            log.exception("failed to launch syscap helper")
            return False
        self._reader = asyncio.create_task(self._read_loop())
        self._stderr_task = asyncio.create_task(self._log_stderr())
        log.info("system-audio capture started (macOS ScreenCaptureKit: %s)", helper)
        return True

    async def _read_loop(self) -> None:
        assert self._proc and self._proc.stdout
        try:
            while True:
                chunk = await self._proc.stdout.read(8192)
                if not chunk:
                    break
                self._feed(chunk)
        except asyncio.CancelledError:
            pass
        except Exception:
            log.exception("syscap read loop error")

    async def _log_stderr(self) -> None:
        assert self._proc and self._proc.stderr
        try:
            async for line in self._proc.stderr:
                log.info("syscap: %s", line.decode(errors="ignore").strip())
        except Exception:
            pass

    async def stop(self) -> None:
        for t in (self._reader, self._stderr_task):
            if t:
                t.cancel()
        if self._proc:
            try:
                if self._proc.stdin:
                    self._proc.stdin.close()  # EOF → helper exits cleanly
                self._proc.terminate()
            except ProcessLookupError:
                pass
            except Exception:
                log.exception("error stopping syscap")
        self._proc = None


# ---------- Windows: WASAPI loopback ----------


class WindowsSystemAudioSource(SystemAudioSource):
    def __init__(self) -> None:
        super().__init__()
        self._running = False
        self._thread: threading.Thread | None = None

    async def start(self) -> bool:
        try:
            import soundcard  # noqa: F401
        except Exception:
            log.warning("soundcard not available; system audio disabled on Windows")
            return False
        self._running = True
        self._thread = threading.Thread(target=self._capture, name="wasapi-loopback", daemon=True)
        self._thread.start()
        log.info("system-audio capture started (Windows WASAPI loopback)")
        return True

    def _capture(self) -> None:
        try:
            import soundcard as sc

            speaker = sc.default_speaker()
            loopback = sc.get_microphone(speaker.name, include_loopback=True)
            with loopback.recorder(samplerate=SAMPLE_RATE, channels=1) as rec:
                while self._running:
                    frames = rec.record(numframes=1600)  # ~0.1 s, float32 [-1, 1]
                    mono = frames[:, 0] if frames.ndim > 1 else frames
                    pcm = (np.clip(mono, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()
                    self._feed(pcm)
        except Exception:
            log.exception("WASAPI loopback capture error; system audio stopped")
            self._running = False

    async def stop(self) -> None:
        self._running = False
        if self._thread:
            await asyncio.to_thread(self._thread.join, 1.0)
        self._thread = None


def make_system_audio_source() -> SystemAudioSource | None:
    if sys.platform == "darwin":
        return MacSystemAudioSource()
    if sys.platform == "win32":
        return WindowsSystemAudioSource()
    return None


# ---------- Mixer (platform-agnostic) ----------


class SystemAudioMixer:
    """Mixes a SystemAudioSource into mic frames before the pipeline."""

    def __init__(self) -> None:
        self._source: SystemAudioSource | None = make_system_audio_source()

    async def start(self) -> bool:
        if self._source is None:
            log.info("no system-audio source for platform %s", sys.platform)
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
