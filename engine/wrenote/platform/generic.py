"""Fallback adapter for platforms without native integration (Linux, BSD, …).

No system audio, no screen capture; hardware probing is best-effort via
``nvidia-smi``. Used both as the real adapter on unsupported OSes and as the
base for tests that need a predictable, capability-free platform.
"""
from __future__ import annotations

import logging
import shutil
import subprocess

from .base import AcceleratorNote, GpuInfo, PlatformAdapter

log = logging.getLogger(__name__)


def probe_nvidia_smi(timeout_s: float = 5.0) -> list[GpuInfo]:
    """GPUs reported by ``nvidia-smi``; empty when it is absent or fails.
    Shared by the Windows and generic adapters (it is the most accurate VRAM
    source when an NVIDIA driver is installed)."""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return []
    try:
        out = subprocess.run(
            [exe, "--query-gpu=name,memory.total,driver_version",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=timeout_s, check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        log.debug("nvidia-smi probe failed", exc_info=True)
        return []
    gpus: list[GpuInfo] = []
    for line in out.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if not parts or not parts[0]:
            continue
        vram = None
        if len(parts) > 1:
            try:
                vram = int(float(parts[1]))
            except ValueError:
                vram = None
        driver = parts[2] if len(parts) > 2 and parts[2] not in ("", "[N/A]") else None
        gpus.append(GpuInfo(vendor="nvidia", name=parts[0], vram_mb=vram, driver_version=driver))
    return gpus


def vendor_from_name(name: str) -> str:
    n = name.lower()
    if "nvidia" in n or "geforce" in n or "quadro" in n or "rtx" in n:
        return "nvidia"
    if "amd" in n or "radeon" in n:
        return "amd"
    if "intel" in n or "arc(tm)" in n or "iris" in n or "uhd graphics" in n:
        return "intel"
    if "apple" in n:
        return "apple"
    return "unknown"


class GenericPlatform(PlatformAdapter):
    name = "linux"
    packaging_dir = "linux"

    def __init__(self) -> None:
        super().__init__()
        import sys

        # Report the real OS name (linux / freebsd / …) rather than "generic".
        self.name = sys.platform

    def _probe_gpus(self) -> list[GpuInfo]:
        return probe_nvidia_smi()

    def _accelerators(self, gpus: tuple[GpuInfo, ...], arch: str) -> tuple[str, ...]:
        if any(g.vendor == "nvidia" for g in gpus):
            return ("cuda", "vulkan", "cpu")
        if any(g.vendor in ("amd", "intel") for g in gpus):
            return ("vulkan", "cpu")
        return ("cpu",)

    def _accelerator_notes(
        self, gpus: tuple[GpuInfo, ...], arch: str
    ) -> list[AcceleratorNote]:
        gpu = next((g for g in gpus if g.vendor in ("nvidia", "amd", "intel")), None)
        detail = f"{gpu.vendor.upper()} {gpu.name}" if gpu else "no discrete GPU detected"
        return [AcceleratorNote(variant="cpu", usable=True, detail=detail)]
