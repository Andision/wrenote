"""macOS adapter.

* System audio  -> ScreenCaptureKit via the bundled ``syscap`` helper
                   (``packaging/macos/syscap.swift``), streamed over stdout.
* Screen/window -> ScreenCaptureKit via the bundled ``screencap`` helper;
                   ffmpeg ``avfoundation`` full-screen as the no-target fallback.
* Compute       -> Metal on Apple Silicon (unified memory), CPU otherwise.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .base import (
    AcceleratorNote,
    Capabilities,
    CaptureTargets,
    GpuInfo,
    PlatformAdapter,
    SystemAudioSource,
    no_targets,
)

log = logging.getLogger(__name__)

_DEVNULL = asyncio.subprocess.DEVNULL
_PIPE = asyncio.subprocess.PIPE


def _ffmpeg() -> str:
    return shutil.which("ffmpeg") or "ffmpeg"


def _x264_out(video_path: Path) -> list[str]:
    return ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(video_path)]


# ---------- System audio: ScreenCaptureKit helper ----------


class MacSystemAudioSource(SystemAudioSource):
    def __init__(self, helper: Path | None) -> None:
        super().__init__()
        self._helper = helper
        self._proc: asyncio.subprocess.Process | None = None
        self._reader: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None

    async def start(self) -> bool:
        if self._helper is None:
            log.warning("syscap helper not found; system audio disabled")
            return False
        try:
            self._proc = await asyncio.create_subprocess_exec(
                str(self._helper),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except Exception:
            log.exception("failed to launch syscap helper")
            return False
        self._reader = asyncio.create_task(self._read_loop())
        self._stderr_task = asyncio.create_task(self._log_stderr())
        log.info("system-audio capture started (macOS ScreenCaptureKit: %s)", self._helper)
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


# ---------- Adapter ----------


class DarwinPlatform(PlatformAdapter):
    name = "darwin"
    packaging_dir = "macos"

    # --- capabilities ---

    @property
    def capabilities(self) -> Capabilities:
        screencap = self.bundled_binary("screencap") is not None
        return Capabilities(
            system_audio=self.bundled_binary("syscap") is not None,
            screen_capture=screencap or shutil.which("ffmpeg") is not None,
            window_capture=screencap,
        )

    # --- audio ---

    def make_system_audio_source(self) -> SystemAudioSource | None:
        return MacSystemAudioSource(self.bundled_binary("syscap"))

    # --- screen ---

    async def list_capture_targets(self) -> CaptureTargets:
        helper = self.bundled_binary("screencap")
        if helper is None:
            log.warning("screencap helper not found; window capture unavailable")
            return no_targets()
        try:
            proc = await asyncio.create_subprocess_exec(
                str(helper), "--list", stdout=_PIPE, stderr=_PIPE
            )
            out, err = await proc.communicate()
        except Exception:
            log.exception("failed to run screencap --list")
            return no_targets()
        if proc.returncode != 0:
            # Most commonly the Screen Recording permission hasn't been granted yet.
            log.warning("screencap --list failed: %s", err.decode(errors="ignore")[-300:])
            return no_targets()
        try:
            data = json.loads(out.decode() or "{}")
        except json.JSONDecodeError:
            log.exception("screencap --list returned invalid JSON")
            return no_targets()
        displays = data.get("displays", []) or []
        windows = data.get("windows", []) or []
        for d in displays:
            d["type"] = "display"
        for w in windows:
            w["type"] = "window"
        return {"displays": displays, "windows": windows}

    async def screen_record_command(
        self, video_path: Path, target: dict[str, Any] | None
    ) -> list[str] | None:
        ttype = (target or {}).get("type")
        if ttype in ("window", "display"):
            helper = self.bundled_binary("screencap")
            if helper is None:
                log.warning("screencap helper not found; cannot record %s", ttype)
                return None
            flag = "--window" if ttype == "window" else "--display"
            return [str(helper), flag, str(target["id"]), "--out", str(video_path)]  # type: ignore[index]
        # Legacy full-screen via ffmpeg avfoundation.
        idx = await _avfoundation_screen_index()
        if idx is None:
            log.warning("no avfoundation screen device found")
            return None
        return [_ffmpeg(), "-y", "-f", "avfoundation", "-capture_cursor", "1",
                "-framerate", "25", "-i", f"{idx}:none", *_x264_out(video_path)]

    # --- hardware ---

    def _probe_gpus(self) -> list[GpuInfo]:
        try:
            brand = subprocess.run(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                capture_output=True, text=True, timeout=3, check=False,
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            brand = ""
        if "apple" in brand.lower():
            # Apple Silicon: the GPU is on-die and shares system memory.
            return [GpuInfo(vendor="apple", name=brand, vram_mb=None, unified_memory=True)]
        return []

    def _probe_ram_mb(self) -> int | None:
        try:
            out = subprocess.run(
                ["sysctl", "-n", "hw.memsize"],
                capture_output=True, text=True, timeout=3, check=False,
            ).stdout.strip()
            return int(int(out) / (1024 * 1024))
        except (OSError, ValueError, subprocess.SubprocessError):
            return super()._probe_ram_mb()

    def _accelerators(self, gpus: tuple[GpuInfo, ...], arch: str) -> tuple[str, ...]:
        # We only ship a Metal build for arm64; Intel Macs run the CPU runtime.
        if arch == "arm64":
            return ("metal", "cpu")
        return ("cpu",)

    def _accelerator_notes(
        self, gpus: tuple[GpuInfo, ...], arch: str
    ) -> list[AcceleratorNote]:
        name = gpus[0].name if gpus else "Apple Silicon"
        if arch == "arm64":
            # Metal is built into the app bundle, so there is nothing to fetch.
            return [
                AcceleratorNote("metal", True, f"{name} · Metal, built in"),
                AcceleratorNote("cpu", True, "always available"),
            ]
        return [AcceleratorNote("cpu", True, "Intel Mac — no Metal build is shipped")]


async def _avfoundation_screen_index() -> str | None:
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
