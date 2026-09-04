"""Windows adapter.

* System audio  -> WASAPI loopback via the ``soundcard`` library.
* Screen/window -> ffmpeg ``gdigrab`` (``title=<window>`` for a window,
                   ``desktop`` otherwise); Win32 ``EnumWindows`` for the picker.
                   (Windows.Graphics.Capture is the proper long-term API — see
                   docs/plans/WINDOW_CAPTURE.md; gdigrab is the pragmatic v1.)
* Compute       -> CUDA on NVIDIA, Vulkan on AMD / Intel / NVIDIA, CPU fallback.
                   NPUs (Intel AI Boost, AMD Ryzen AI) are detected and reported
                   but not used: there is no ggml backend for them yet.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import shutil
import subprocess
import threading
from pathlib import Path
from typing import IO, Any

import numpy as np

from .base import (
    SAMPLE_RATE,
    AcceleratorNote,
    Capabilities,
    CaptureTargets,
    GpuInfo,
    PlatformAdapter,
    SystemAudioSource,
    no_targets,
)
from .generic import probe_nvidia_smi, vendor_from_name

log = logging.getLogger(__name__)


def _ffmpeg() -> str:
    return shutil.which("ffmpeg") or "ffmpeg"


def _x264_out(video_path: Path) -> list[str]:
    return ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(video_path)]


# ---------- System audio: WASAPI loopback ----------


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
        # WASAPI is a COM API, and COM must be initialized per-thread. This runs
        # on a worker thread that never inherits the main thread's init, so
        # without an explicit CoInitializeEx the very first soundcard call fails
        # with CO_E_NOTINITIALIZED — silently, in frozen Windows builds, leaving
        # the mic recording fine but no system audio. ctypes avoids a pywin32 dep.
        com_ready = False
        try:
            import ctypes

            # COINIT_APARTMENTTHREADED = 0x2; S_OK/S_FALSE both mean usable.
            hr = ctypes.windll.ole32.CoInitializeEx(None, 0x2)  # type: ignore[attr-defined]
            com_ready = hr in (0, 1)
        except Exception:
            log.exception("CoInitializeEx failed; system audio may not start")
        try:
            import soundcard as sc

            speaker = sc.default_speaker()
            # Match by stable device id, not the display name (name matching is
            # fuzzy and can pick the wrong endpoint or raise IndexError).
            loopback = sc.get_microphone(speaker.id, include_loopback=True)
            with loopback.recorder(samplerate=SAMPLE_RATE, channels=1) as rec:
                while self._running:
                    frames = rec.record(numframes=1600)  # ~0.1 s, float32 [-1, 1]
                    mono = frames[:, 0] if frames.ndim > 1 else frames
                    pcm = (np.clip(mono, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()
                    self._feed(pcm)
        except Exception:
            log.exception("WASAPI loopback capture error; system audio stopped")
            self._running = False
        finally:
            if com_ready:
                with contextlib.suppress(Exception):
                    ctypes.windll.ole32.CoUninitialize()  # type: ignore[attr-defined]

    async def stop(self) -> None:
        self._running = False
        if self._thread:
            await asyncio.to_thread(self._thread.join, 1.0)
        self._thread = None


# ---------- Hardware probing helpers (pure functions, unit-testable) ----------


def _powershell_json(script: str, timeout_s: float = 8.0) -> Any:
    """Run a PowerShell snippet that ends in ``ConvertTo-Json`` and parse it.
    Returns ``None`` on any failure (no PowerShell, timeout, bad JSON)."""
    exe = shutil.which("powershell") or shutil.which("pwsh")
    if not exe:
        return None
    try:
        out = subprocess.run(
            [exe, "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=timeout_s, check=False,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        log.debug("powershell probe failed", exc_info=True)
        return None
    if not out:
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return None


def gpus_from_video_controllers(rows: Any) -> list[GpuInfo]:
    """Turn ``Win32_VideoController`` rows (``Name``, ``AdapterRAM``) into GpuInfo.
    ``AdapterRAM`` is a 32-bit field and caps at 4 GB — callers should prefer
    ``nvidia-smi`` numbers when available."""
    if rows is None:
        return []
    if isinstance(rows, dict):
        rows = [rows]
    gpus: list[GpuInfo] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("Name") or "").strip()
        if not name:
            continue
        vendor = vendor_from_name(name)
        if vendor == "unknown" and ("basic display" in name.lower() or "remote" in name.lower()):
            continue  # RDP / fallback adapters, not compute devices
        ram = row.get("AdapterRAM")
        vram = None
        if isinstance(ram, (int, float)) and ram > 0:
            vram = int(ram / (1024 * 1024))
        gpus.append(GpuInfo(vendor=vendor, name=name, vram_mb=vram,
                            unified_memory=vendor == "intel" and vram is not None and vram <= 2048))
    return gpus


def npu_from_pnp_names(names: list[str]) -> str | None:
    """Recognise NPU devices from ``Win32_PnPEntity`` names."""
    for raw in names:
        n = raw.lower()
        if "ai boost" in n or ("intel" in n and "npu" in n):
            return "intel"
        if ("amd" in n and ("ipu" in n or "npu" in n)) or "ryzen ai" in n:
            return "amd"
        if "hexagon" in n or ("qualcomm" in n and "npu" in n):
            return "qualcomm"
    return None


def merge_gpu_lists(primary: list[GpuInfo], secondary: list[GpuInfo]) -> list[GpuInfo]:
    """Prefer ``primary`` (nvidia-smi) entries; add ``secondary`` (WMI) GPUs whose
    vendor isn't already covered, so a laptop's iGPU still shows up next to
    its NVIDIA dGPU."""
    covered = {g.vendor for g in primary}
    return primary + [g for g in secondary if g.vendor not in covered]


#: Minimum NVIDIA driver for the CUDA runtime our pack ships. The pack carries
#: cudart/cuBLAS 12.4, so the user needs no CUDA *Toolkit* — but the driver is
#: theirs, and CUDA 12.x minor-version compatibility puts the floor at the
#: R525 branch (527.41 on Windows). Below that ggml-cuda loads and fails.
MIN_NVIDIA_DRIVER = 527

#: Below this much VRAM, offloading to the GPU buys little and risks OOM on a
#: model that would have fit in RAM; keep such machines on the CPU runtime.
MIN_GPU_VRAM_MB = 2048


def driver_major(version: str | None) -> int | None:
    """Major component of an NVIDIA driver version ("566.36" -> 566)."""
    if not version:
        return None
    head = version.strip().split(".", 1)[0]
    try:
        return int(head)
    except ValueError:
        return None


def evaluate_accelerators(
    gpus: tuple[GpuInfo, ...] | list[GpuInfo],
    *,
    vulkan_available: bool,
    nvcuda_present: bool,
) -> tuple[tuple[str, ...], list[AcceleratorNote]]:
    """Windows runtime-variant ranking **and** the reason for each verdict.

    CUDA needs the NVIDIA driver (``nvcuda.dll``), not the CUDA Toolkit — the
    runtime pack ships cudart and cuBLAS itself — so the checks are: an NVIDIA
    GPU, a driver new enough for CUDA 12, and enough VRAM to be worth it.
    Vulkan covers any vendor's GPU (NVIDIA included, as the lighter option and
    the fallback when CUDA can't load). CPU is last and always usable.
    """
    out: list[str] = []
    notes: list[AcceleratorNote] = []

    nvidia = [g for g in gpus if g.vendor == "nvidia"]
    if nvidia:
        gpu = nvidia[0]
        major = driver_major(gpu.driver_version)
        shown = f"{gpu.name} · driver {gpu.driver_version}" if gpu.driver_version else gpu.name
        if not nvcuda_present and major is None:
            notes.append(AcceleratorNote("cuda", False, f"{gpu.name} found, but no NVIDIA driver"))
        elif major is not None and major < MIN_NVIDIA_DRIVER:
            notes.append(AcceleratorNote(
                "cuda", False,
                f"{shown} is older than the {MIN_NVIDIA_DRIVER} CUDA 12 needs — update it to use CUDA",
            ))
        elif gpu.vram_mb is not None and gpu.vram_mb < MIN_GPU_VRAM_MB:
            notes.append(AcceleratorNote(
                "cuda", False, f"{gpu.name} has under {MIN_GPU_VRAM_MB // 1024} GB of VRAM"))
        else:
            out.append("cuda")
            notes.append(AcceleratorNote("cuda", True, shown))

    gpu_vendors = [g for g in gpus if g.vendor in ("nvidia", "amd", "intel")]
    if gpu_vendors:
        gpu = gpu_vendors[0]
        if vulkan_available:
            out.append("vulkan")
            notes.append(AcceleratorNote("vulkan", True, gpu.name))
        else:
            notes.append(AcceleratorNote(
                "vulkan", False, f"{gpu.name} found, but no Vulkan driver (vulkan-1.dll)"))

    out.append("cpu")
    notes.append(AcceleratorNote(
        "cpu", True, "always available" if gpu_vendors else "no usable GPU detected"))
    return tuple(out), notes


def accelerators_for(
    gpus: tuple[GpuInfo, ...] | list[GpuInfo], *, vulkan_available: bool,
    nvcuda_present: bool = True,
) -> tuple[str, ...]:
    """Just the ranking from :func:`evaluate_accelerators`."""
    return evaluate_accelerators(
        gpus, vulkan_available=vulkan_available, nvcuda_present=nvcuda_present
    )[0]


# ---------- Adapter ----------


class WindowsPlatform(PlatformAdapter):
    name = "win32"
    packaging_dir = "windows"

    @property
    def capabilities(self) -> Capabilities:
        try:
            import soundcard  # noqa: F401

            system_audio = True
        except Exception:
            system_audio = False
        ffmpeg = shutil.which("ffmpeg") is not None
        return Capabilities(system_audio=system_audio, screen_capture=ffmpeg, window_capture=ffmpeg)

    # --- audio ---

    def make_system_audio_source(self) -> SystemAudioSource | None:
        return WindowsSystemAudioSource()

    # --- screen ---

    async def list_capture_targets(self) -> CaptureTargets:
        """Enumerate visible top-level windows (Win32 EnumWindows) + the primary
        display. Guarded so an import/ctypes error degrades to empty."""
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32  # type: ignore[attr-defined]
            windows: list[dict[str, Any]] = []

            @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)  # type: ignore[attr-defined]
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
            return no_targets()

    async def screen_record_command(
        self, video_path: Path, target: dict[str, Any] | None
    ) -> list[str] | None:
        t = target or {}
        src = f"title={t['title']}" if t.get("type") == "window" and t.get("title") else "desktop"
        return [_ffmpeg(), "-y", "-f", "gdigrab", "-framerate", "25", "-i", src,
                *_x264_out(video_path)]

    # --- hardware ---

    def _probe_gpus(self) -> list[GpuInfo]:
        wmi = gpus_from_video_controllers(_powershell_json(
            "Get-CimInstance Win32_VideoController | Select-Object Name,AdapterRAM | ConvertTo-Json"
        ))
        return merge_gpu_lists(probe_nvidia_smi(), wmi)

    def _probe_npu(self) -> str | None:
        rows = _powershell_json(
            "Get-CimInstance Win32_PnPEntity | Where-Object { $_.Name -match 'NPU|AI Boost|IPU|"
            "Hexagon|Ryzen AI' } | Select-Object -ExpandProperty Name | ConvertTo-Json"
        )
        if rows is None:
            return None
        names = [rows] if isinstance(rows, str) else [str(r) for r in rows if r]
        return npu_from_pnp_names(names)

    def _probe_ram_mb(self) -> int | None:
        rows = _powershell_json(
            "Get-CimInstance Win32_ComputerSystem | Select-Object TotalPhysicalMemory | ConvertTo-Json"
        )
        if isinstance(rows, dict):
            total = rows.get("TotalPhysicalMemory")
            if isinstance(total, (int, float)) and total > 0:
                return int(total / (1024 * 1024))
        return super()._probe_ram_mb()

    def _accelerators(self, gpus: tuple[GpuInfo, ...], arch: str) -> tuple[str, ...]:
        return self._evaluate(gpus)[0]

    def _accelerator_notes(
        self, gpus: tuple[GpuInfo, ...], arch: str
    ) -> list[AcceleratorNote]:
        return self._evaluate(gpus)[1]

    def _evaluate(
        self, gpus: tuple[GpuInfo, ...]
    ) -> tuple[tuple[str, ...], list[AcceleratorNote]]:
        return evaluate_accelerators(
            gpus,
            vulkan_available=self._system32_has("vulkan-1.dll"),
            nvcuda_present=self._system32_has("nvcuda.dll"),
        )

    @staticmethod
    def _system32_has(dll: str) -> bool:
        """Is a driver-installed library present? Drivers land in System32, so
        this is how we tell "has an NVIDIA/Vulkan driver" from "has the SDK"."""
        system_root = os.environ.get("SYSTEMROOT", r"C:\Windows")
        return (Path(system_root) / "System32" / dll).exists()

    # --- process ---

    def lock_file(self, handle: IO[Any]) -> None:
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)  # type: ignore[attr-defined]
