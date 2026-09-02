"""Platform adapter layer: selection, graceful degradation, hardware probing.

The adapters for macOS / Windows are constructed on every host (their probes
are guarded), but only the behaviour that doesn't need the real OS is asserted
here. The pure helper functions that decide runtime-variant preference are
unit-tested directly — that logic is what the compute runtime selector relies on.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from wrenote import platform as plat
from wrenote.core import screenrec, syscap
from wrenote.platform.base import Capabilities, GpuInfo, HardwareInfo, PlatformAdapter
from wrenote.platform.darwin import DarwinPlatform
from wrenote.platform.generic import GenericPlatform, vendor_from_name
from wrenote.platform.win32 import (
    WindowsPlatform,
    accelerators_for,
    gpus_from_video_controllers,
    merge_gpu_lists,
    npu_from_pnp_names,
)


@pytest.fixture(autouse=True)
def _reset_platform():
    yield
    plat.set_platform(None)


# ---------- selection ----------


def test_get_platform_matches_running_os():
    adapter = plat.get_platform()
    expected = {"darwin": DarwinPlatform, "win32": WindowsPlatform}.get(
        sys.platform, GenericPlatform
    )
    assert isinstance(adapter, expected)
    assert adapter.name == sys.platform


def test_make_platform_by_name():
    assert isinstance(plat.make_platform("darwin"), DarwinPlatform)
    assert isinstance(plat.make_platform("win32"), WindowsPlatform)
    assert isinstance(plat.make_platform("linux"), GenericPlatform)
    assert isinstance(plat.make_platform("freebsd14"), GenericPlatform)


def test_set_platform_overrides_singleton():
    fake = GenericPlatform()
    plat.set_platform(fake)
    assert plat.get_platform() is fake
    plat.set_platform(None)
    assert plat.get_platform() is not fake


# ---------- graceful degradation on a capability-free platform ----------


class _Bare(PlatformAdapter):
    """The minimum a new platform must write: a name. Everything else degrades."""

    name = "bare"


@pytest.mark.asyncio
async def test_bare_adapter_degrades_everywhere(tmp_path: Path):
    bare = _Bare()
    assert bare.capabilities == Capabilities(False, False, False)
    assert bare.make_system_audio_source() is None
    assert await bare.list_capture_targets() == {"displays": [], "windows": []}
    assert await bare.screen_record_command(tmp_path / "v.mp4", None) is None
    hw = bare.probe_hardware()
    assert hw.accelerators == ("cpu",)
    assert hw.os == "bare"
    assert bare.bundled_binary("definitely-not-a-real-helper") is None


@pytest.mark.asyncio
async def test_core_modules_route_through_adapter(tmp_path: Path):
    plat.set_platform(_Bare())
    mixer = syscap.SystemAudioMixer()
    assert await mixer.start() is False
    pcm = b"\x01\x00" * 160
    assert await mixer.mix(pcm) == pcm  # passthrough when no source
    await mixer.stop()

    assert await screenrec.list_targets() == {"displays": [], "windows": []}
    rec = screenrec.ScreenRecorder()
    assert await rec.start(tmp_path / "v.mp4", None) is False
    await rec.stop()  # no-op when nothing started


# ---------- hardware description ----------


def test_hardware_info_serialises_and_ends_with_cpu():
    hw = plat.get_platform().probe_hardware()
    d = hw.to_dict()
    assert set(d) >= {"os", "arch", "cpu_count", "ram_mb", "gpus", "npu", "accelerators"}
    assert d["accelerators"][-1] == "cpu"
    assert isinstance(d["gpus"], list)
    assert hw.cpu_count >= 1
    # cached
    assert plat.get_platform().probe_hardware() is hw


def test_darwin_accelerators_by_arch():
    mac = DarwinPlatform()
    apple = (GpuInfo(vendor="apple", name="Apple M3", unified_memory=True),)
    assert mac._accelerators(apple, "arm64") == ("metal", "cpu")
    assert mac._accelerators((), "x86_64") == ("cpu",)


def test_windows_accelerator_preference():
    nvidia = GpuInfo(vendor="nvidia", name="NVIDIA GeForce RTX 4070", vram_mb=12288)
    amd = GpuInfo(vendor="amd", name="AMD Radeon RX 7800 XT", vram_mb=16384)
    intel = GpuInfo(vendor="intel", name="Intel(R) Iris(R) Xe Graphics", vram_mb=1024)
    assert accelerators_for([nvidia], vulkan_available=True) == ("cuda", "vulkan", "cpu")
    assert accelerators_for([nvidia], vulkan_available=False) == ("cuda", "cpu")
    assert accelerators_for([amd], vulkan_available=True) == ("vulkan", "cpu")
    assert accelerators_for([intel], vulkan_available=True) == ("vulkan", "cpu")
    assert accelerators_for([intel, nvidia], vulkan_available=True) == ("cuda", "vulkan", "cpu")
    assert accelerators_for([], vulkan_available=True) == ("cpu",)


def test_windows_wmi_rows_to_gpus():
    rows = [
        {"Name": "NVIDIA GeForce RTX 3060", "AdapterRAM": 4293918720},
        {"Name": "Intel(R) UHD Graphics 770", "AdapterRAM": 1073741824},
        {"Name": "Microsoft Basic Display Adapter", "AdapterRAM": 0},
    ]
    gpus = gpus_from_video_controllers(rows)
    assert [g.vendor for g in gpus] == ["nvidia", "intel"]
    assert gpus[0].vram_mb == 4095  # 32-bit WMI cap — nvidia-smi is preferred
    assert gpus[1].unified_memory is True
    # PowerShell emits a bare object (not a list) for a single row
    assert gpus_from_video_controllers({"Name": "AMD Radeon 780M", "AdapterRAM": 512 << 20})[0].vendor == "amd"
    assert gpus_from_video_controllers(None) == []


def test_windows_merge_prefers_nvidia_smi():
    smi = [GpuInfo(vendor="nvidia", name="NVIDIA GeForce RTX 4090", vram_mb=24564)]
    wmi = [
        GpuInfo(vendor="nvidia", name="NVIDIA GeForce RTX 4090", vram_mb=4095),
        GpuInfo(vendor="intel", name="Intel(R) UHD Graphics", vram_mb=1024),
    ]
    merged = merge_gpu_lists(smi, wmi)
    assert merged[0].vram_mb == 24564
    assert [g.vendor for g in merged] == ["nvidia", "intel"]


def test_npu_detection_from_pnp_names():
    assert npu_from_pnp_names(["Intel(R) AI Boost"]) == "intel"
    assert npu_from_pnp_names(["AMD IPU Device"]) == "amd"
    assert npu_from_pnp_names(["Qualcomm(R) Hexagon(TM) NPU"]) == "qualcomm"
    assert npu_from_pnp_names(["Realtek Audio", "USB Root Hub"]) is None


def test_vendor_from_name():
    assert vendor_from_name("NVIDIA GeForce RTX 4060 Laptop GPU") == "nvidia"
    assert vendor_from_name("AMD Radeon(TM) Graphics") == "amd"
    assert vendor_from_name("Intel(R) Arc(TM) A770 Graphics") == "intel"
    assert vendor_from_name("Something Else") == "unknown"


# ---------- single-instance lock ----------


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX flock semantics")
def test_lock_file_is_exclusive(tmp_path: Path):
    adapter = plat.get_platform()
    path = tmp_path / "wrenote.lock"
    first = open(path, "w")  # noqa: SIM115
    second = open(path, "w")  # noqa: SIM115
    try:
        adapter.lock_file(first)
        with pytest.raises(OSError):
            adapter.lock_file(second)
    finally:
        first.close()
        second.close()


def test_hardware_info_is_frozen():
    hw = HardwareInfo(os="x", arch="x86_64", cpu_count=1, ram_mb=None, gpus=(), npu=None,
                      accelerators=("cpu",))
    with pytest.raises(AttributeError):
        hw.os = "y"  # type: ignore[misc]
