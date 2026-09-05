"""Compute runtime manager: selection policy, persistence, activation, budgeting.

Uses a fake platform adapter so the hardware ranking is deterministic on any
host, and a tmp runtimes_dir so nothing touches ~/.wrenote.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from wrenote.core.config import ComputeConfig
from wrenote.core.runtimes import (
    DEFAULT_PACK_MODULES,
    PACK_MANIFEST,
    VARIANTS,
    PackFinder,
    RuntimeManager,
    RuntimeUnavailable,
    builtin_variant_for,
)
from wrenote.platform.base import GpuInfo, HardwareInfo, PlatformAdapter


class FakePlatform(PlatformAdapter):
    """A Windows-like box with an NVIDIA card unless told otherwise."""

    name = "win32"

    def __init__(self, accelerators=("cuda", "vulkan", "cpu"), gpus=None, arch="x86_64"):
        super().__init__()
        self._acc = tuple(accelerators)
        self._gpus = tuple(gpus or (GpuInfo(vendor="nvidia", name="RTX 4070", vram_mb=12288),))
        self._arch = arch

    def probe_hardware(self) -> HardwareInfo:
        return HardwareInfo(
            os=self.name, arch=self._arch, cpu_count=8, ram_mb=32768,
            gpus=self._gpus, npu=None, accelerators=self._acc,
        )


def _install_pack(root: Path, variant: str) -> Path:
    d = root / variant
    (d / "site-packages").mkdir(parents=True)
    (d / PACK_MANIFEST).write_text(json.dumps({"variant": variant}))
    return d


def _mgr(tmp_path: Path, platform=None, **cfg) -> RuntimeManager:
    cfg.setdefault("runtimes_dir", str(tmp_path / "runtimes"))
    cfg.setdefault("runtimes_index_url", "")  # never touch the network here
    return RuntimeManager(ComputeConfig(**cfg), platform or FakePlatform())


# ---------- built-in variant ----------


def test_builtin_variant_by_platform_tag(monkeypatch):
    monkeypatch.delenv("WRENOTE_BUILTIN_RUNTIME", raising=False)
    assert builtin_variant_for("darwin-arm64") == "metal"
    assert builtin_variant_for("win32-x86_64") == "cpu"
    assert builtin_variant_for("linux-x86_64") == "cpu"
    monkeypatch.setenv("WRENOTE_BUILTIN_RUNTIME", "vulkan")
    assert builtin_variant_for("win32-x86_64") == "vulkan"
    monkeypatch.setenv("WRENOTE_BUILTIN_RUNTIME", "bogus")
    assert builtin_variant_for("win32-x86_64") == "cpu"


# ---------- candidates + selection ----------


def test_auto_prefers_hardware_ranking_but_falls_back_to_builtin(tmp_path):
    m = _mgr(tmp_path)
    assert m.builtin == "cpu"
    assert m.candidates() == ("cuda", "vulkan", "cpu")
    sel = m.select()
    # nothing downloaded yet → the built-in CPU runtime, and we say why
    assert sel.variant == "cpu"
    assert sel.reason == "fallback"
    assert sel.skipped == ("cuda", "vulkan")


def test_installed_pack_is_selected_by_hardware(tmp_path):
    _install_pack(tmp_path / "runtimes", "vulkan")
    m = _mgr(tmp_path)
    sel = m.select()
    assert sel.variant == "vulkan"
    assert sel.reason == "hardware"
    assert sel.skipped == ("cuda",)


def test_pinned_accelerator_goes_first_and_keeps_builtin_last(tmp_path):
    _install_pack(tmp_path / "runtimes", "vulkan")
    _install_pack(tmp_path / "runtimes", "cuda")
    m = _mgr(tmp_path, accelerator="vulkan")
    assert m.candidates() == ("vulkan", "cpu")
    sel = m.select()
    assert (sel.variant, sel.reason) == ("vulkan", "pinned")


def test_pin_cpu_ignores_gpus(tmp_path):
    _install_pack(tmp_path / "runtimes", "cuda")
    m = _mgr(tmp_path, accelerator="cpu")
    assert m.candidates() == ("cpu",)
    assert m.select().reason == "builtin"


def test_unknown_pin_is_rejected(tmp_path):
    with pytest.raises(ValueError, match=r"compute\.accelerator"):
        _mgr(tmp_path, accelerator="npu").candidates()


def test_metal_builtin_on_apple_silicon(tmp_path):
    mac = FakePlatform(accelerators=("metal", "cpu"), arch="arm64",
                       gpus=(GpuInfo(vendor="apple", name="Apple M3", unified_memory=True),))
    mac.name = "darwin"
    m = _mgr(tmp_path, mac)
    assert m.builtin == "metal"
    assert m.pack("metal").builtin and m.pack("metal").installed
    sel = m.select()
    assert (sel.variant, sel.reason) == ("metal", "builtin")


# ---------- bad-variant persistence ----------


def test_mark_bad_persists_and_skips_next_launch(tmp_path):
    root = tmp_path / "runtimes"
    _install_pack(root, "cuda")
    _install_pack(root, "vulkan")
    m = _mgr(tmp_path)
    assert m.select().variant == "cuda"
    m.mark_bad("cuda", "CUDA driver too old")
    assert m.select().variant == "vulkan"
    assert json.loads((root / "state.json").read_text())["bad"] == {"cuda": "CUDA driver too old"}

    # a fresh manager (= next launch) remembers
    m2 = _mgr(tmp_path)
    assert m2.candidates() == ("vulkan", "cpu")
    assert m2.bad == {"cuda": "CUDA driver too old"}
    m2.clear_bad()
    assert _mgr(tmp_path).candidates() == ("cuda", "vulkan", "cpu")


def test_builtin_cannot_be_marked_bad(tmp_path):
    m = _mgr(tmp_path)
    m.mark_bad("cpu", "???")
    assert m.bad == {}
    assert "cpu" in m.candidates()


def test_corrupt_state_file_is_ignored(tmp_path):
    root = tmp_path / "runtimes"
    root.mkdir()
    (root / "state.json").write_text("{not json")
    assert _mgr(tmp_path).bad == {}


# ---------- ensure + activate ----------


def test_ensure_returns_installed_or_raises(tmp_path):
    m = _mgr(tmp_path)
    assert m.ensure("cpu").builtin
    with pytest.raises(RuntimeUnavailable, match="no runtime index configured"):
        m.ensure("cuda")
    _install_pack(tmp_path / "runtimes", "cuda")
    assert m.ensure("cuda").installed
    with pytest.raises(ValueError):
        m.pack("opencl")


def test_activate_routes_pack_modules_once(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "meta_path", list(sys.meta_path))
    d = _install_pack(tmp_path / "runtimes", "vulkan")
    m = _mgr(tmp_path)
    sel = m.activate()
    assert sel.variant == "vulkan"
    finder = sys.meta_path[0]
    assert isinstance(finder, PackFinder)
    assert finder.site == d / "site-packages"
    # no "modules" in the manifest → the default native module names are routed
    assert finder.modules == frozenset(DEFAULT_PACK_MODULES)
    # idempotent
    assert m.activate() is sel
    assert sum(isinstance(f, PackFinder) for f in sys.meta_path) == 1
    assert m.active is sel
    m.deactivate()
    assert not any(isinstance(f, PackFinder) for f in sys.meta_path)


def test_activate_builtin_leaves_import_system_alone(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "meta_path", list(sys.meta_path))
    before = list(sys.meta_path)
    m = _mgr(tmp_path)
    assert m.activate().variant == "cpu"
    assert sys.meta_path == before


# ---------- VRAM budgeting ----------


def test_gpu_layers_budget(tmp_path):
    _install_pack(tmp_path / "runtimes", "cuda")
    m = _mgr(tmp_path)  # RTX 4070, 12288 MB → budget 11059
    m.activate()
    assert m.vram_budget_mb() == int(12288 * 0.9)
    assert m.gpu_layers_for(2500) == -1  # fits
    assert m.gpu_layers_for(2500, already_allocated_mb=9000) == 0  # doesn't fit anymore
    assert m.gpu_layers_for(20000) == 0


def test_gpu_layers_cpu_runtime_and_overrides(tmp_path):
    m = _mgr(tmp_path)  # no packs → cpu
    m.activate()
    assert m.gpu_layers_for(1000) == 0
    assert _mgr(tmp_path, gpu_layers=12).gpu_layers_for(1000) == 12
    assert _mgr(tmp_path, vram_budget_mb=2000).vram_budget_mb() == 2000


def test_unified_memory_is_unbudgeted(tmp_path):
    mac = FakePlatform(accelerators=("metal", "cpu"), arch="arm64",
                       gpus=(GpuInfo(vendor="apple", name="Apple M3", unified_memory=True),))
    mac.name = "darwin"
    m = _mgr(tmp_path, mac)
    m.activate()
    assert m.vram_budget_mb() is None
    assert m.gpu_layers_for(4000) == -1


# ---------- status + API ----------


def test_status_shape(tmp_path):
    m = _mgr(tmp_path)
    m.activate()
    st = m.status()
    assert st["platform_tag"] == "win32-x86_64"
    assert st["builtin"] == "cpu" and st["active"] == "cpu"
    assert st["candidates"] == ["cuda", "vulkan", "cpu"]
    assert st["selection"]["variant"] == "cpu"
    assert [p["variant"] for p in st["packs"]] == list(VARIANTS)
    assert st["hardware"]["gpus"][0]["vendor"] == "nvidia"
    assert set(st["capabilities"]) == {"system_audio", "screen_capture", "window_capture"}
    json.dumps(st)  # must be JSON-serialisable for the API


def test_compute_status_endpoint(client):
    r = client.get("/v1/compute/status")
    assert r.status_code == 200
    body = r.json()
    assert body["active"] == body["selection"]["variant"]
    assert body["candidates"][-1] == body["builtin"] or "cpu" in body["candidates"]
    info = client.get("/v1/info").json()
    assert info["compute"]["active"] == body["active"]
    assert set(info["platform"]) == {"name", "capabilities"}
