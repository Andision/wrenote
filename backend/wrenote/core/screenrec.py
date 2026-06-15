"""Screen / window recording to MP4.

Records the *video* of a chosen target (a specific window, a display, or — as a
legacy fallback — the whole screen). The session's audio + transcription reuse
the existing record + upload pipeline; on stop we mux the silent video with the
captured audio WAV into the final MP4.

  macOS    -> ScreenCaptureKit via the bundled `screencap` helper (window/display);
              ffmpeg avfoundation full-screen as the no-target fallback.
  Windows  -> ffmpeg gdigrab: `title=<window>` for a window, `desktop` otherwise.
              (Windows.Graphics.Capture is the proper long-term API — see
              WINDOW_CAPTURE.md; gdigrab-title is the pragmatic v1.)

A capture *target* is ``{"type": "window"|"display"|"screen", "id": ..,
"title": ..}`` (or ``None`` for legacy full-screen). Enumerate available targets
with :func:`list_targets`.

Cross-platform note: the bundled ffmpeg must include libx264 (it does on the
conda / GitHub-runner / imageio builds we use).
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import sys
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_DEVNULL = asyncio.subprocess.DEVNULL
_PIPE = asyncio.subprocess.PIPE


def _ffmpeg() -> str:
    return shutil.which("ffmpeg") or "ffmpeg"


def _mac_screencap_helper() -> Path | None:
    """Locate the bundled ScreenCaptureKit `screencap` helper (mirrors syscap)."""
    candidates: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates += [
            Path(meipass) / "screencap",
            Path(sys.executable).resolve().parent / "screencap",
        ]
    candidates.append(
        Path(__file__).resolve().parent.parent.parent / "packaging" / "macos" / "screencap"
    )
    return next((c for c in candidates if c.exists()), None)


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


# ---------- Enumeration ----------


async def list_targets() -> dict[str, list[dict[str, Any]]]:
    """Return ``{"displays": [...], "windows": [...]}`` of capturable targets.

    Each entry: ``{id, title, app?, width, height, type}``. Empty lists when
    capture isn't available on this platform / helper is missing / permission
    not yet granted.
    """
    if sys.platform == "darwin":
        return await _mac_list_targets()
    if sys.platform == "win32":
        return _win_list_targets()
    return {"displays": [], "windows": []}


async def _mac_list_targets() -> dict[str, list[dict[str, Any]]]:
    helper = _mac_screencap_helper()
    if helper is None:
        log.warning("screencap helper not found; window capture unavailable")
        return {"displays": [], "windows": []}
    try:
        proc = await asyncio.create_subprocess_exec(
            str(helper), "--list", stdout=_PIPE, stderr=_PIPE
        )
        out, err = await proc.communicate()
    except Exception:
        log.exception("failed to run screencap --list")
        return {"displays": [], "windows": []}
    if proc.returncode != 0:
        # Most commonly the Screen Recording permission hasn't been granted yet.
        log.warning("screencap --list failed: %s", err.decode(errors="ignore")[-300:])
        return {"displays": [], "windows": []}
    try:
        data = json.loads(out.decode() or "{}")
    except json.JSONDecodeError:
        log.exception("screencap --list returned invalid JSON")
        return {"displays": [], "windows": []}
    displays = data.get("displays", []) or []
    windows = data.get("windows", []) or []
    for d in displays:
        d["type"] = "display"
    for w in windows:
        w["type"] = "window"
    return {"displays": displays, "windows": windows}


def _win_list_targets() -> dict[str, list[dict[str, Any]]]:
    """Enumerate visible top-level windows (Win32 EnumWindows) + the primary display.

    UNTESTED on non-Windows; guarded so an import/ctypes error degrades to empty.
    """
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        windows: list[dict[str, Any]] = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def _cb(hwnd: int, _lparam: int) -> bool:
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length == 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            title = buf.value
            if not title:
                return True
            rect = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            w = rect.right - rect.left
            h = rect.bottom - rect.top
            if w < 80 or h < 80:
                return True
            windows.append(
                {"id": int(hwnd), "title": title, "app": "", "width": w, "height": h,
                 "type": "window"}
            )
            return True

        user32.EnumWindows(_cb, 0)
        sw = user32.GetSystemMetrics(0)
        sh = user32.GetSystemMetrics(1)
        displays = [
            {"id": 0, "title": "Primary display", "width": sw, "height": sh, "type": "display"}
        ]
        return {"displays": displays, "windows": windows}
    except Exception:
        log.exception("window enumeration failed")
        return {"displays": [], "windows": []}


# ---------- Recording ----------


class ScreenRecorder:
    """Records a target's video to a file until stopped."""

    def __init__(self) -> None:
        self._proc: asyncio.subprocess.Process | None = None

    async def start(self, video_path: Path, target: dict[str, Any] | None = None) -> bool:
        """Start recording ``target`` (or the full screen when ``target`` is None)."""
        if sys.platform == "darwin":
            args = await self._mac_args(video_path, target)
        elif sys.platform == "win32":
            args = self._win_args(video_path, target)
        else:
            log.info("screen recording not supported on %s", sys.platform)
            return False
        if args is None:
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

    async def _mac_args(
        self, video_path: Path, target: dict[str, Any] | None
    ) -> list[str] | None:
        ttype = (target or {}).get("type")
        if ttype in ("window", "display"):
            helper = _mac_screencap_helper()
            if helper is None:
                log.warning("screencap helper not found; cannot record %s", ttype)
                return None
            flag = "--window" if ttype == "window" else "--display"
            return [str(helper), flag, str(target["id"]), "--out", str(video_path)]
        # Legacy full-screen via ffmpeg avfoundation.
        idx = await _mac_screen_index()
        if idx is None:
            log.warning("no avfoundation screen device found")
            return None
        return [_ffmpeg(), "-y", "-f", "avfoundation", "-capture_cursor", "1",
                "-framerate", "25", "-i", f"{idx}:none", *_x264_out(video_path)]

    def _win_args(self, video_path: Path, target: dict[str, Any] | None) -> list[str]:
        ff = _ffmpeg()
        t = target or {}
        src = f"title={t['title']}" if t.get("type") == "window" and t.get("title") else "desktop"
        return [ff, "-y", "-f", "gdigrab", "-framerate", "25", "-i", src, *_x264_out(video_path)]

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


def _x264_out(video_path: Path) -> list[str]:
    return ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(video_path)]


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
