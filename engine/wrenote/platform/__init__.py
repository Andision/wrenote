"""Platform adapters: the one place the engine knows which OS it runs on.

``get_platform()`` returns the adapter for the running OS (cached). Tests can
swap it with :func:`set_platform` to exercise a capability-free or fake
platform on any host.
"""
from __future__ import annotations

import sys

from .base import (
    AcceleratorNote,
    Capabilities,
    CaptureTargets,
    GpuInfo,
    HardwareInfo,
    PlatformAdapter,
    SystemAudioSource,
)

__all__ = [
    "AcceleratorNote",
    "Capabilities",
    "CaptureTargets",
    "GpuInfo",
    "HardwareInfo",
    "PlatformAdapter",
    "SystemAudioSource",
    "get_platform",
    "make_platform",
    "set_platform",
]

_current: PlatformAdapter | None = None


def make_platform(name: str | None = None) -> PlatformAdapter:
    """Construct the adapter for ``name`` (``sys.platform`` value; default: this OS)."""
    name = name or sys.platform
    if name == "darwin":
        from .darwin import DarwinPlatform

        return DarwinPlatform()
    if name == "win32":
        from .win32 import WindowsPlatform

        return WindowsPlatform()
    from .generic import GenericPlatform

    return GenericPlatform()


def get_platform() -> PlatformAdapter:
    """The process-wide adapter for the running OS."""
    global _current
    if _current is None:
        _current = make_platform()
    return _current


def set_platform(adapter: PlatformAdapter | None) -> None:
    """Override (or, with ``None``, reset) the process-wide adapter. Tests only."""
    global _current
    _current = adapter
