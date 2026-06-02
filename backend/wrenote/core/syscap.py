"""System-audio capture + mixing (macOS, via the bundled ScreenCaptureKit helper).

The ``syscap`` helper streams 16 kHz mono s16le PCM of the system output on its
stdout. We buffer it and mix it into the live mic stream — driven by mic-frame
arrival, so the pipeline keeps the mic's real-time cadence and the system audio
rides along. Exact sample-sync isn't needed for transcription.

macOS only for now (ScreenCaptureKit). Requires the Screen Recording permission,
which macOS attributes to the host .app (the helper is spawned as its child).
Windows (WASAPI loopback) will plug in here later behind the same interface.
"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

_BUFFER_CAP_BYTES = 16_000 * 2 * 2  # ~2 s of 16 kHz mono s16le; drop older if behind


def helper_path() -> Path | None:
    """Locate the syscap binary: bundled (frozen) or in the dev source tree."""
    candidates: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates += [Path(meipass) / "syscap", Path(sys.executable).resolve().parent / "syscap"]
    candidates.append(
        Path(__file__).resolve().parent.parent.parent / "packaging" / "macos" / "syscap"
    )
    return next((c for c in candidates if c.exists()), None)


class SystemAudioMixer:
    """Spawns the syscap helper and mixes its PCM into mic frames."""

    def __init__(self) -> None:
        self._proc: asyncio.subprocess.Process | None = None
        self._buf = bytearray()
        self._lock = asyncio.Lock()
        self._reader: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None

    async def start(self) -> bool:
        """Launch the helper. Returns False if unavailable (caller falls back to mic-only)."""
        if sys.platform != "darwin":
            log.info("system-audio capture not supported on %s yet", sys.platform)
            return False
        helper = helper_path()
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
        log.info("system-audio capture started (%s)", helper)
        return True

    async def _read_loop(self) -> None:
        assert self._proc and self._proc.stdout
        try:
            while True:
                chunk = await self._proc.stdout.read(8192)
                if not chunk:
                    break
                async with self._lock:
                    self._buf.extend(chunk)
                    if len(self._buf) > _BUFFER_CAP_BYTES:
                        del self._buf[: len(self._buf) - _BUFFER_CAP_BYTES]
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

    async def mix(self, mic_pcm: bytes) -> bytes:
        """Add the next equal-length slice of system audio onto a mic frame."""
        async with self._lock:
            n = min(len(mic_pcm), len(self._buf))
            sys_bytes = bytes(self._buf[:n])
            del self._buf[:n]
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
        if self._reader:
            self._reader.cancel()
        if self._stderr_task:
            self._stderr_task.cancel()
        if self._proc:
            try:
                if self._proc.stdin:
                    self._proc.stdin.close()  # EOF → helper exits cleanly
            except Exception:
                pass
            try:
                self._proc.terminate()
            except ProcessLookupError:
                pass
            except Exception:
                log.exception("error stopping syscap")
        self._proc = None
