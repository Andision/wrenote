"""Full-screen recording to MP4 via ffmpeg.

This module owns only the screen *video* (captured silently by ffmpeg). The
session's audio + transcription reuse the existing record + upload pipeline; on
stop we mux the screen video with the captured audio WAV into the final MP4.

  macOS    -> avfoundation screen device (needs Screen Recording permission)
  Windows  -> gdigrab desktop

Cross-platform note: the bundled ffmpeg must include libx264 (it does on the
conda / GitHub-runner / imageio builds we use).
"""
from __future__ import annotations

import asyncio
import logging
import re
import shutil
import sys
from pathlib import Path

log = logging.getLogger(__name__)

_DEVNULL = asyncio.subprocess.DEVNULL
_PIPE = asyncio.subprocess.PIPE


def _ffmpeg() -> str:
    return shutil.which("ffmpeg") or "ffmpeg"


async def _mac_screen_index() -> str | None:
    """Parse the avfoundation device list for the 'Capture screen' input index."""
    proc = await asyncio.create_subprocess_exec(
        _ffmpeg(), "-f", "avfoundation", "-list_devices", "true", "-i", "",
        stdout=_DEVNULL, stderr=_PIPE,
    )
    _, err = await proc.communicate()
    for line in err.decode(errors="ignore").splitlines():
        m = re.search(r"\[(\d+)\]\s+Capture screen", line)
        if m:
            return m.group(1)
    return None


class ScreenRecorder:
    """Records the full screen (video only) to a file until stopped."""

    def __init__(self) -> None:
        self._proc: asyncio.subprocess.Process | None = None

    async def start(self, video_path: Path) -> bool:
        ff = _ffmpeg()
        common_out = [
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(video_path)
        ]
        if sys.platform == "darwin":
            idx = await _mac_screen_index()
            if idx is None:
                log.warning("no avfoundation screen device found")
                return False
            args = [ff, "-y", "-f", "avfoundation", "-capture_cursor", "1",
                    "-framerate", "25", "-i", f"{idx}:none", *common_out]
        elif sys.platform == "win32":
            args = [ff, "-y", "-f", "gdigrab", "-framerate", "25", "-i", "desktop", *common_out]
        else:
            log.info("screen recording not supported on %s", sys.platform)
            return False
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *args, stdin=_PIPE, stdout=_DEVNULL, stderr=_PIPE
            )
        except Exception:
            log.exception("failed to start screen recording")
            return False
        log.info("screen recording -> %s", video_path)
        return True

    async def stop(self) -> None:
        """Ask ffmpeg to finalize the file (write 'q' to stdin), then wait."""
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
