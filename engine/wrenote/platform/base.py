"""Platform adapter contract.

Everything OS-specific in the engine lives behind :class:`PlatformAdapter`:
system-audio capture, screen/window capture, hardware probing (for the compute
runtime selection), single-instance locking, and locating bundled helper
binaries. ``core/*`` and the API never test ``sys.platform`` themselves — they
ask the adapter, and an adapter that lacks a capability simply reports it and
returns ``None`` / empty, so the engine degrades gracefully instead of failing.

Adding a platform therefore means one new module in this package (plus a
packaging recipe); nothing else in the engine changes.

This module deliberately imports nothing from ``wrenote.core`` so the layering
stays one-directional: ``core`` depends on ``platform``, never the reverse.
"""
from __future__ import annotations

import abc
import logging
import os
import platform as _stdplatform
import sys
import threading
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import IO, Any

log = logging.getLogger(__name__)

SAMPLE_RATE = 16_000
_BUFFER_CAP_BYTES = SAMPLE_RATE * 2 * 2  # ~2 s of 16 kHz mono s16le; drop older if behind

CaptureTargets = dict[str, list[dict[str, Any]]]


def no_targets() -> CaptureTargets:
    """Fresh empty target listing (never share a mutable module-level dict)."""
    return {"displays": [], "windows": []}


# ---------- Hardware description ----------


@dataclass(frozen=True)
class GpuInfo:
    """One GPU as seen by the OS. ``vram_mb`` is ``None`` when unknown or when
    the GPU shares memory with the CPU (Apple Silicon, most iGPUs)."""

    vendor: str  # "nvidia" | "amd" | "intel" | "apple" | "unknown"
    name: str
    vram_mb: int | None = None
    unified_memory: bool = False
    # Vendor driver version, when the probe can see it ("566.36"). The compute
    # selector needs it: a runtime pack ships the CUDA *runtime*, but the
    # *driver* comes from the user's machine and can be too old for it.
    driver_version: str | None = None


#: English rendering of every :class:`AcceleratorNote` code. The *client* owns
#: the text a user reads (see ``clients/web/src/i18n/``) — this table exists so
#: logs and any non-localized API consumer still get a sentence, and so the
#: codes have one obvious place to be read off.
NOTE_TEXT: dict[str, str] = {
    "gpu_ready": "{gpu}",
    "gpu_ready_driver": "{gpu} · driver {driver}",
    "no_driver": "{gpu} found, but no driver",
    "driver_too_old": "{gpu} · driver {driver} is older than the {min} CUDA 12 needs",
    "low_vram": "{gpu} has under {gb} GB of VRAM",
    "no_vulkan_loader": "{gpu} found, but no Vulkan driver (vulkan-1.dll)",
    "metal_builtin": "{gpu} · Metal, built in",
    "intel_mac": "Intel Mac — no Metal build is shipped",
    "cpu_always": "Always available",
    "cpu_no_gpu": "No usable GPU detected",
}


@dataclass(frozen=True)
class AcceleratorNote:
    """Why a runtime variant is (or isn't) usable on this machine.

    Carried as a ``code`` plus ``params`` rather than a sentence: the setup
    wizard shows this to a person, and human-facing text belongs to the client,
    which knows their language. :attr:`detail` is the English rendering, for
    logs and for clients that don't localize.

    An unexplained recommendation is one nobody dares click, and an accelerator
    that is silently missing looks like a bug — so ``usable`` variants appear in
    :attr:`HardwareInfo.accelerators` and the rest carry their blocker here.
    """

    variant: str
    usable: bool
    code: str  # key into NOTE_TEXT, e.g. "driver_too_old"
    params: dict[str, str] = field(default_factory=dict)

    @property
    def detail(self) -> str:
        return NOTE_TEXT.get(self.code, self.code).format(**self.params)

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "detail": self.detail}


@dataclass(frozen=True)
class HardwareInfo:
    """What the compute runtime selector needs to know about this machine.

    ``accelerators`` is the ordered list of runtime *variants* this hardware
    could use, best first, always ending in ``"cpu"`` (which every platform can
    run). It is a candidate list, not a promise that a runtime pack exists —
    :mod:`wrenote.core.runtimes` intersects it with the packs actually
    available for this OS/arch.
    """

    os: str  # "darwin" | "win32" | "linux" | ...
    arch: str  # "arm64" | "x86_64" | ...
    cpu_count: int
    ram_mb: int | None
    gpus: tuple[GpuInfo, ...]
    npu: str | None  # "intel" | "amd" | "qualcomm" | None — informational for now
    accelerators: tuple[str, ...]
    #: Human-readable verdict per variant the platform considered, usable or not.
    notes: tuple[AcceleratorNote, ...] = ()

    def note_for(self, variant: str) -> AcceleratorNote | None:
        return next((n for n in self.notes if n.variant == variant), None)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["gpus"] = [asdict(g) for g in self.gpus]
        d["accelerators"] = list(self.accelerators)
        d["notes"] = [n.to_dict() for n in self.notes]
        return d


@dataclass(frozen=True)
class Capabilities:
    """Feature flags a client can use to hide unavailable controls."""

    system_audio: bool  # can mix system output into the mic stream
    screen_capture: bool  # can record a display / the full screen
    window_capture: bool  # can record one specific window

    def to_dict(self) -> dict[str, bool]:
        return asdict(self)


# ---------- System-audio source contract ----------


class SystemAudioSource(abc.ABC):
    """Produces system-output PCM (16 kHz mono s16le).

    Thread-safe buffer so a blocking native producer (helper subprocess or
    capture thread) and the async mixer can share it without an event-loop hop.
    """

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


# ---------- Adapter ----------


def _machine_arch() -> str:
    m = _stdplatform.machine().lower()
    if m in ("arm64", "aarch64"):
        return "arm64"
    if m in ("x86_64", "amd64"):
        return "x86_64"
    return m or "unknown"


def _total_ram_mb() -> int | None:
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        return int(pages * page_size / (1024 * 1024))
    except (AttributeError, ValueError, OSError):
        return None


class PlatformAdapter:
    """Base adapter. Every method has a safe "unsupported" default so a new
    platform can start from ``class X(PlatformAdapter): name = "x"`` and add
    capabilities incrementally."""

    name: str = "generic"

    def __init__(self) -> None:
        self._hardware: HardwareInfo | None = None

    # --- capabilities -----------------------------------------------------

    @property
    def capabilities(self) -> Capabilities:
        return Capabilities(system_audio=False, screen_capture=False, window_capture=False)

    # --- audio ------------------------------------------------------------

    def make_system_audio_source(self) -> SystemAudioSource | None:
        """A source for the system output mix, or ``None`` when unsupported."""
        return None

    # --- screen / window capture -----------------------------------------

    async def list_capture_targets(self) -> CaptureTargets:
        """``{"displays": [...], "windows": [...]}`` of capturable targets.

        Each entry: ``{id, title, app?, width, height, type}``. Empty when
        unsupported / helper missing / permission not yet granted.
        """
        return no_targets()

    async def screen_record_command(
        self, video_path: Path, target: dict[str, Any] | None
    ) -> list[str] | None:
        """Command line of a process that records ``target`` (``None`` = full
        screen) to ``video_path`` until its stdin is closed. ``None`` when the
        platform can't record this target. Process lifecycle is owned by
        :class:`wrenote.core.screenrec.ScreenRecorder`."""
        return None

    # --- hardware ---------------------------------------------------------

    def probe_hardware(self) -> HardwareInfo:
        """Describe this machine (cached; probing may shell out)."""
        if self._hardware is None:
            arch = _machine_arch()
            gpus = tuple(self._probe_gpus())
            self._hardware = HardwareInfo(
                os=self.name,
                arch=arch,
                cpu_count=os.cpu_count() or 1,
                ram_mb=self._probe_ram_mb(),
                gpus=gpus,
                npu=self._probe_npu(),
                accelerators=self._accelerators(gpus, arch),
                notes=tuple(self._accelerator_notes(gpus, arch)),
            )
        return self._hardware

    def _probe_gpus(self) -> list[GpuInfo]:
        return []

    def _probe_npu(self) -> str | None:
        return None

    def _probe_ram_mb(self) -> int | None:
        return _total_ram_mb()

    def _accelerators(self, gpus: tuple[GpuInfo, ...], arch: str) -> tuple[str, ...]:
        """Candidate runtime variants for this hardware, best first, ending in ``cpu``."""
        return ("cpu",)

    def _accelerator_notes(
        self, gpus: tuple[GpuInfo, ...], arch: str
    ) -> Sequence[AcceleratorNote]:
        """One line per variant this platform considered — what was detected, or
        what blocks it. Optional: a platform with nothing to explain returns ()."""
        return ()

    # --- process ----------------------------------------------------------

    def lock_file(self, handle: IO[Any]) -> None:
        """Take an exclusive, non-blocking lock on ``handle``; raise ``OSError``
        if another process holds it. POSIX default; Windows overrides."""
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    # --- bundled binaries -------------------------------------------------

    def bundled_binary(self, name: str) -> Path | None:
        """Locate a helper binary shipped with the engine.

        Frozen (PyInstaller): next to the bundle data (``_MEIPASS``) or the
        executable. Dev: the repo's ``packaging/<platform-dir>/`` folder.
        """
        candidates: list[Path] = []
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates += [Path(meipass) / name, Path(sys.executable).resolve().parent / name]
        for root in _repo_roots():
            candidates.append(root / "packaging" / self.packaging_dir / name)
        return next((c for c in candidates if c.exists()), None)

    #: Sub-folder of ``packaging/`` holding this platform's helpers.
    packaging_dir: str = "generic"


def _repo_roots() -> list[Path]:
    """Directories that may contain ``packaging/`` in a source checkout: the
    engine package's parent (``engine/``) and the repo root above it."""
    here = Path(__file__).resolve()
    return [here.parents[2], here.parents[3]]
