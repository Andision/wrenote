"""Screen / window recording to MP4 (platform-agnostic process lifecycle).

Records the *video* of a chosen target (a specific window, a display, or — as a
legacy fallback — the whole screen). The session's audio + transcription reuse
the existing record + upload pipeline; on stop we mux the silent video with the
captured audio WAV into the final MP4.

Target enumeration and the recorder command line come from the platform
adapter (:mod:`wrenote.platform`): ScreenCaptureKit helpers on macOS, ffmpeg
``gdigrab`` on Windows, nothing elsewhere. This module only owns the recorder
process (start / finalize) and the ffmpeg mux, which are the same everywhere.

A capture *target* is ``{"type": "window"|"display"|"screen", "id": ..,
"title": ..}`` (or ``None`` for legacy full-screen). Enumerate available targets
with :func:`list_targets`.

Cross-platform note: the bundled ffmpeg must include libx264 (it does on the
conda / GitHub-runner / imageio builds we use).
"""
from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path
from typing import Any

from ..platform import CaptureTargets, get_platform

log = logging.getLogger(__name__)

_DEVNULL = asyncio.subprocess.DEVNULL
_PIPE = asyncio.subprocess.PIPE


def _ffmpeg() -> str:
    return shutil.which("ffmpeg") or "ffmpeg"


# ---------- Enumeration ----------


async def list_targets() -> CaptureTargets:
    """Return ``{"displays": [...], "windows": [...]}`` of capturable targets.

    Each entry: ``{id, title, app?, width, height, type}``. Empty lists when
    capture isn't available on this platform / helper is missing / permission
    not yet granted.
    """
    return await get_platform().list_capture_targets()


# ---------- Recording ----------


class ScreenRecorder:
    """Records a target's video to a file until stopped."""

    def __init__(self) -> None:
        self._proc: asyncio.subprocess.Process | None = None

    async def start(self, video_path: Path, target: dict[str, Any] | None = None) -> bool:
        """Start recording ``target`` (or the full screen when ``target`` is None)."""
        plat = get_platform()
        args = await plat.screen_record_command(video_path, target)
        if args is None:
            log.info("screen recording not available on %s for target=%s", plat.name, target)
            return False
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *args, stdin=_PIPE, stdout=_DEVNULL, stderr=_PIPE
            )
        except Exception:
            log.exception("failed to start screen recording")
            return False
        log.info("screen recording -> %s (target=%s)", video_path, target)
        return True

    async def stop(self) -> None:
        """Finalize the file. For ffmpeg we write 'q' to stdin; for the screencap
        helper, closing stdin is what triggers a clean finalize (it ignores the
        'q' byte and stops on EOF), so the same sequence works for both."""
        if self._proc is None:
            return
        proc, self._proc = self._proc, None
        try:
            if proc.stdin:
                proc.stdin.write(b"q")
                await proc.stdin.drain()
                proc.stdin.close()
            await asyncio.wait_for(proc.wait(), timeout=10.0)
        except Exception:
            try:
                proc.terminate()
            except ProcessLookupError:
                pass
            except Exception:
                log.exception("error stopping screen recording")


async def mux(video_path: Path, audio_path: Path, out_path: Path) -> bool:
    """Combine a (silent) screen video with the captured audio WAV into an MP4."""
    proc = await asyncio.create_subprocess_exec(
        _ffmpeg(), "-y", "-i", str(video_path), "-i", str(audio_path),
        "-c:v", "copy", "-c:a", "aac", "-shortest", str(out_path),
        stdout=_DEVNULL, stderr=_PIPE,
    )
    _, err = await proc.communicate()
    if proc.returncode != 0:
        log.error("mux failed: %s", err.decode(errors="ignore")[-300:])
        return False
    return True
