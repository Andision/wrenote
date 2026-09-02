"""Runtime-pack install end to end, without network.

Builds a real pack with ``packaging/runtimes/build_pack.py`` (pip-installing a
tiny pure-Python distribution into it), publishes it through a ``file://``
index made by ``make_index.py``, and drives ``RuntimeManager.ensure`` +
``activate`` — including the import finder that must beat the built-in
module — plus the HTTP endpoints on top.
"""
from __future__ import annotations

import importlib
import json
import shutil
import sys
import zipfile
from pathlib import Path

import pytest

from wrenote.core import config as config_mod
from wrenote.core.config import ComputeConfig
from wrenote.core.runtimes import (
    PACK_MANIFEST,
    PackFinder,
    RuntimeManager,
    RuntimeUnavailable,
    python_tag,
)
from wrenote.platform.base import GpuInfo, HardwareInfo, PlatformAdapter

PACKAGING = Path(__file__).resolve().parents[2] / "packaging" / "runtimes"
sys.path.insert(0, str(PACKAGING))
import build_pack  # noqa: E402
import make_index  # noqa: E402

TAG = build_pack.platform_tag()
PY = python_tag()


class HostPlatform(PlatformAdapter):
    """This machine's tag, an NVIDIA card, cpu built in — so cuda/vulkan are installable."""

    def __init__(self) -> None:
        super().__init__()
        self.name = TAG.split("-")[0]

    def probe_hardware(self) -> HardwareInfo:
        os_, arch = TAG.split("-", 1)
        return HardwareInfo(
            os=os_, arch=arch, cpu_count=4, ram_mb=16384,
            gpus=(GpuInfo(vendor="nvidia", name="fake", vram_mb=8192),),
            npu=None, accelerators=("cuda", "vulkan", "cpu"),
        )


@pytest.fixture(scope="module")
def fake_dist(tmp_path_factory) -> Path:
    """A tiny local distribution named ``llama_cpp`` (the module the engine
    routes), so the pack test exercises the real module names."""
    root = tmp_path_factory.mktemp("dist") / "llama_cpp_fake"
    (root / "llama_cpp").mkdir(parents=True)
    (root / "llama_cpp" / "__init__.py").write_text('WHERE = "pack"\n')
    (root / "llama_cpp" / "sub.py").write_text('VALUE = 42\n')
    (root / "pyproject.toml").write_text(
        '[build-system]\nrequires = ["setuptools>=68"]\nbuild-backend = "setuptools.build_meta"\n'
        '[project]\nname = "llama-cpp-fake"\nversion = "0.0.1"\n'
        '[tool.setuptools.packages.find]\ninclude = ["llama_cpp*"]\n'
    )
    return root


@pytest.fixture(scope="module")
def published(tmp_path_factory, fake_dist: Path) -> tuple[Path, str]:
    """Build a 'vulkan' pack from the fake dist and index it under file://."""
    out = tmp_path_factory.mktemp("release")
    build_pack.build(
        variant="vulkan", version="test.1", specs=[str(fake_dist)], out_dir=out,
        # --no-deps + a local path; --no-build-isolation avoids a network fetch
        # of setuptools inside the sandboxed build.
        python=sys.executable,
    )
    archives = sorted(out.glob("wrenote-runtime-*.zip"))
    assert len(archives) == 1
    index = make_index.make_index(archives, out.as_uri())
    (out / "runtimes.json").write_text(json.dumps(index))
    return archives[0], (out / "runtimes.json").as_uri()


def _mgr(tmp_path: Path, index_url: str, **cfg) -> RuntimeManager:
    cfg.setdefault("runtimes_dir", str(tmp_path / "runtimes"))
    return RuntimeManager(ComputeConfig(runtimes_index_url=index_url, **cfg), HostPlatform())


# ---------- build_pack / make_index ----------


def test_pack_layout_and_manifest(published):
    archive, _ = published
    with zipfile.ZipFile(archive) as z:
        names = z.namelist()
        manifest = json.loads(z.read(PACK_MANIFEST))
    assert "site-packages/llama_cpp/__init__.py" in names
    assert not any("__pycache__" in n for n in names)
    assert manifest["schema"] == 1
    assert manifest["variant"] == "vulkan"
    assert manifest["platform_tag"] == TAG and manifest["python"] == PY
    assert manifest["modules"] == ["llama_cpp"]
    assert manifest["packages"] == {"llama_cpp_fake": "0.0.1"}
    assert archive.name == f"wrenote-runtime-vulkan-{TAG}-py{PY}.zip"
    assert (archive.parent / (archive.name + ".sha256")).read_text().split()[0] == build_pack.sha256_of(archive)


def test_index_entries(published):
    archive, index_url = published
    index = json.loads(Path(index_url.removeprefix("file://")).read_text())
    [row] = index["packs"]
    assert row["url"].endswith(archive.name) and row["url"].startswith("file://")
    assert row["sha256"] == build_pack.sha256_of(archive)
    assert row["size"] == archive.stat().st_size
    assert row["modules"] == ["llama_cpp"]


# ---------- index + ensure ----------


def test_index_filters_to_this_platform(tmp_path, published):
    _, index_url = published
    m = _mgr(tmp_path, index_url)
    rels = m.index()
    assert [r.variant for r in rels] == ["vulkan"]
    assert m.release_for("cuda") is None
    st = m.status(include_index=True)
    assert st["index"]["reachable"] is True
    by = {p["variant"]: p for p in st["packs"]}
    assert by["vulkan"]["available"] is True and by["vulkan"]["installed"] is False
    assert by["cuda"]["available"] is False
    assert by["cpu"]["available"] is True  # built-in


def test_unreachable_index_is_reported_not_raised(tmp_path):
    m = _mgr(tmp_path, (tmp_path / "missing.json").as_uri())
    with pytest.raises(RuntimeUnavailable, match="could not fetch"):
        m.index()
    st = m.status(include_index=True)
    assert st["index"]["reachable"] is False and "could not fetch" in st["index"]["error"]
    assert st["packs"][0]["available"] in (True, False)  # rows still present
    with pytest.raises(RuntimeUnavailable, match="no runtime index configured"):
        _mgr(tmp_path, "").ensure("vulkan")


def test_ensure_downloads_verifies_and_unpacks(tmp_path, published):
    _, index_url = published
    m = _mgr(tmp_path, index_url)
    m.mark_bad("vulkan", "old driver")
    seen: list[tuple[float, str]] = []
    pack = m.ensure("vulkan", lambda f, s: seen.append((f, s)))
    assert pack.installed and (pack.path / "site-packages" / "llama_cpp" / "__init__.py").exists()
    assert pack.manifest()["version"] == "test.1"
    assert seen[-1][0] == 1.0 and "installed" in seen[-1][1]
    assert [f for f, _ in seen] == sorted(f for f, _ in seen)  # monotonic
    assert not list((tmp_path / "runtimes").glob("*.partial"))
    assert m.bad == {}  # a fresh install clears the bad mark
    # second call is a no-op
    assert m.ensure("vulkan").installed
    # and selection now prefers it over the built-in
    assert m.select().variant == "vulkan"
    assert m.pack("vulkan").to_dict()["packages"] == {"llama_cpp_fake": "0.0.1"}


def test_ensure_rejects_checksum_mismatch(tmp_path, published):
    _, index_url = published
    bad = json.loads(Path(index_url.removeprefix("file://")).read_text())
    bad["packs"][0]["sha256"] = "0" * 64
    idx = tmp_path / "bad.json"
    idx.write_text(json.dumps(bad))
    m = _mgr(tmp_path, idx.as_uri())
    with pytest.raises(RuntimeUnavailable, match="checksum"):
        m.ensure("vulkan")
    assert not m.pack("vulkan").installed
    assert not list((tmp_path / "runtimes").glob("*.partial"))


def _publish_zip(tmp_path: Path, name: str, files: dict[str, str], variant: str = "vulkan") -> str:
    archive = tmp_path / name
    with zipfile.ZipFile(archive, "w") as z:
        for member, content in files.items():
            z.writestr(member, content)
    index = {"schema": 1, "packs": [{
        "variant": variant, "platform_tag": TAG, "python": PY, "version": "x",
        "url": archive.as_uri(), "sha256": build_pack.sha256_of(archive), "size": archive.stat().st_size,
    }]}
    idx = tmp_path / f"{name}.json"
    idx.write_text(json.dumps(index))
    return idx.as_uri()


def test_ensure_rejects_bad_manifest_and_unsafe_paths(tmp_path):
    wrong_py = json.dumps({"schema": 1, "variant": "vulkan", "platform_tag": TAG, "python": "2.7"})
    m = _mgr(tmp_path, _publish_zip(tmp_path, "py.zip", {PACK_MANIFEST: wrong_py}))
    with pytest.raises(RuntimeUnavailable, match=r"Python 2\.7"):
        m.ensure("vulkan")

    wrong_variant = json.dumps({"schema": 1, "variant": "cuda", "platform_tag": TAG, "python": PY})
    m = _mgr(tmp_path, _publish_zip(tmp_path, "var.zip", {PACK_MANIFEST: wrong_variant}))
    with pytest.raises(RuntimeUnavailable, match="variant 'cuda'"):
        m.ensure("vulkan")

    m = _mgr(tmp_path, _publish_zip(tmp_path, "esc.zip", {"../evil.txt": "x", PACK_MANIFEST: "{}"}))
    with pytest.raises(RuntimeUnavailable, match="unsafe path"):
        m.ensure("vulkan")

    m = _mgr(tmp_path, _publish_zip(tmp_path, "none.zip", {"site-packages/x.py": ""}))
    with pytest.raises(RuntimeUnavailable, match="no MANIFEST"):
        m.ensure("vulkan")
    assert not (tmp_path / "runtimes" / "vulkan").exists()
    assert not [p for p in (tmp_path / "runtimes").iterdir() if p.name.startswith("vulkan.")]


# ---------- activate: the pack must win over a built-in copy ----------


@pytest.fixture
def clean_llama_cpp(monkeypatch):
    """Remove any imported llama_cpp so each test resolves it afresh."""
    for name in list(sys.modules):
        if name == "llama_cpp" or name.startswith("llama_cpp."):
            monkeypatch.delitem(sys.modules, name)
    monkeypatch.setattr(sys, "meta_path", list(sys.meta_path))
    monkeypatch.setattr(sys, "path", list(sys.path))
    yield
    for name in list(sys.modules):
        if name == "llama_cpp" or name.startswith("llama_cpp."):
            del sys.modules[name]


def test_activate_routes_pack_modules_ahead_of_builtin(tmp_path, published, clean_llama_cpp):
    # A "built-in" llama_cpp first on sys.path stands in for the frozen bundle's copy.
    builtin = tmp_path / "builtin"
    (builtin / "llama_cpp").mkdir(parents=True)
    (builtin / "llama_cpp" / "__init__.py").write_text('WHERE = "builtin"\n')
    sys.path.insert(0, str(builtin))
    assert importlib.import_module("llama_cpp").WHERE == "builtin"
    del sys.modules["llama_cpp"]

    _, index_url = published
    m = _mgr(tmp_path, index_url)
    m.ensure("vulkan")
    sel = m.activate()
    assert sel.variant == "vulkan"
    assert isinstance(sys.meta_path[0], PackFinder)
    mod = importlib.import_module("llama_cpp")
    assert mod.WHERE == "pack"
    assert importlib.import_module("llama_cpp.sub").VALUE == 42
    # anything not listed in the manifest is untouched
    assert sys.meta_path[0].find_spec("numpy") is None
    m.deactivate()
    assert not isinstance(sys.meta_path[0], PackFinder)


def test_activate_marks_pack_for_other_python_bad_and_falls_back(tmp_path, clean_llama_cpp):
    root = tmp_path / "runtimes" / "vulkan"
    (root / "site-packages").mkdir(parents=True)
    (root / PACK_MANIFEST).write_text(json.dumps(
        {"schema": 1, "variant": "vulkan", "platform_tag": TAG, "python": "3.9", "modules": ["llama_cpp"]}
    ))
    m = _mgr(tmp_path, "")
    assert m.select().variant == "vulkan"  # installed as far as the manifest goes
    sel = m.activate()
    assert sel.variant == "cpu"
    assert "Python 3.9" in m.bad["vulkan"]


def test_remove_pack(tmp_path, published):
    _, index_url = published
    m = _mgr(tmp_path, index_url)
    assert m.remove("vulkan") is False
    m.ensure("vulkan")
    assert m.remove("vulkan") is True
    assert not m.pack("vulkan").installed
    assert m.remove("cpu") is False


# ---------- user config persistence ----------


def test_write_user_config_merges(tmp_path, monkeypatch):
    path = tmp_path / "cfg" / "config.yaml"
    monkeypatch.setattr(config_mod, "USER_CONFIG", path)
    path.parent.mkdir()
    path.write_text("stt:\n  backend: whisper_cpp\ncompute:\n  gpu_layers: 20\n", encoding="utf-8")
    config_mod.write_user_config({"compute": {"accelerator": "vulkan"}})
    loaded = config_mod.load_config([path], use_env=False)
    assert loaded.compute.accelerator == "vulkan"
    assert loaded.compute.gpu_layers == 20  # untouched sibling key
    assert loaded.stt.backend == "whisper_cpp"  # untouched section
    assert not path.with_suffix(".yaml.tmp").exists()
    # creates the file (and parents) when absent
    monkeypatch.setattr(config_mod, "USER_CONFIG", tmp_path / "new" / "config.yaml")
    config_mod.write_user_config({"compute": {"accelerator": "auto"}})
    assert (tmp_path / "new" / "config.yaml").exists()


# ---------- HTTP ----------


def test_compute_select_persists_and_reports_restart(client, tmp_path):
    r = client.post("/v1/compute/select", json={"accelerator": "Vulkan"})
    assert r.status_code == 200
    body = r.json()
    assert body["accelerator"] == "vulkan" and body["restart_required"] is True
    assert (tmp_path / "config.yaml").read_text().strip().endswith("accelerator: vulkan")
    assert client.post("/v1/compute/select", json={"accelerator": "npu"}).status_code == 400
    assert client.post("/v1/compute/select", json={"accelerator": "auto"}).json()["accelerator"] == "auto"


def test_compute_install_409_when_nothing_published(client):
    # conftest sets runtimes_index_url="" → nothing can be installed
    r = client.post("/v1/compute/install", json={"variant": "vulkan"})
    assert r.status_code == 409
    assert client.post("/v1/compute/install", json={"variant": "bogus"}).status_code == 400
    # the built-in is "installed" without a job
    builtin = client.get("/v1/compute/status").json()["builtin"]
    r = client.post("/v1/compute/install", json={"variant": builtin})
    assert r.status_code == 200 and r.json() == {"job_id": None, "installed": True, "variant": builtin}


def test_compute_status_reports_index_state(client):
    st = client.get("/v1/compute/status").json()
    assert st["index"]["checked"] is True and st["index"]["reachable"] is False
    assert all("available" in p for p in st["packs"])


def test_compute_remove_endpoint(client):
    builtin = client.get("/v1/compute/status").json()["builtin"]
    assert client.delete(f"/v1/compute/packs/{builtin}").status_code == 400
    other = next(v for v in ("vulkan", "cuda", "cpu") if v != builtin)
    r = client.delete(f"/v1/compute/packs/{other}")
    assert r.status_code == 200 and r.json()["removed"] is False


@pytest.fixture
def install_client(monkeypatch, tmp_path, published):
    """A TestClient whose engine can really install the published fake pack."""
    from fastapi.testclient import TestClient

    import wrenote.core.recording as recording_mod
    import wrenote.core.store as store_mod
    import wrenote.server as server
    from wrenote import platform as plat
    from wrenote.core.config import Config

    _, index_url = published
    monkeypatch.setattr(store_mod, "DEFAULT_DB_PATH", tmp_path / "data.db")
    monkeypatch.setattr(recording_mod, "DEFAULT_DIR", tmp_path / "recordings")
    monkeypatch.setattr(config_mod, "USER_CONFIG", tmp_path / "config.yaml")
    plat.set_platform(HostPlatform())
    cfg = Config.model_validate({
        "stt": {"backend": "mock"}, "vad": {"backend": "disabled"}, "translator": {"backend": "mock"},
        "speaker": {"backend": "disabled"}, "chat": {"backend": "mock"},
        "compute": {"runtimes_dir": str(tmp_path / "runtimes"), "runtimes_index_url": index_url},
    })
    with TestClient(server.create_app(cfg)) as c:
        yield c
    plat.set_platform(None)


def test_compute_install_job_end_to_end(install_client, tmp_path):
    c = install_client
    r = c.post("/v1/compute/install", json={"variant": "vulkan"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["installed"] is False and body["release"]["variant"] == "vulkan"
    job_id = body["job_id"]
    # Stream to the terminal frame (the server closes after it).
    with c.stream("GET", f"/v1/jobs/{job_id}/stream") as resp:
        frames = [json.loads(line[len("data: "):]) for line in resp.iter_lines() if line.startswith("data: ")]
    assert frames[-1]["status"] == "done", frames[-1]
    assert frames[-1]["result"]["variant"] == "vulkan"
    assert (tmp_path / "runtimes" / "vulkan" / PACK_MANIFEST).exists()
    st = c.get("/v1/compute/status").json()
    assert {p["variant"]: p["installed"] for p in st["packs"]}["vulkan"] is True
    # already installed → no job
    assert c.post("/v1/compute/install", json={"variant": "vulkan"}).json()["job_id"] is None
    shutil.rmtree(tmp_path / "runtimes" / "vulkan")


# ---------- build_pack module detection ----------


def test_top_level_modules_ignores_wheel_install_leftovers(tmp_path):
    """llama-cpp-python's wheel drops bin/ include/ lib/ next to the package;
    those are not importable and must never be routed by the finder."""
    site = tmp_path / "site-packages"
    (site / "llama_cpp").mkdir(parents=True)
    (site / "llama_cpp" / "__init__.py").write_text("")
    (site / "llama_cpp" / "lib").mkdir()
    (site / "llama_cpp" / "lib" / "llama.dll").write_bytes(b"")
    (site / "_pywhispercpp.cp311-win_amd64.pyd").write_bytes(b"")
    (site / "pywhispercpp").mkdir()
    (site / "pywhispercpp" / "__init__.py").write_text("")
    for d in ("bin", "include", "lib"):
        (site / d).mkdir()
    (site / "bin" / "llama-cli.exe").write_bytes(b"")
    (site / "include" / "ggml.h").write_text("")
    (site / "lib" / "ggml.lib").write_bytes(b"")
    (site / "lib" / "cmake").mkdir()
    (site / "lib" / "cmake" / "ggml-config.cmake").write_text("")
    info = site / "llama_cpp_python-0.3.28.dist-info"
    info.mkdir()
    (info / "RECORD").write_text(
        "llama_cpp/__init__.py,,\nllama_cpp/lib/llama.dll,,\nbin/llama-cli.exe,,\n"
        "include/ggml.h,,\nlib/ggml.lib,,\nlib/cmake/ggml-config.cmake,,\n"
    )
    info2 = site / "pywhispercpp-1.4.1.dist-info"
    info2.mkdir()
    (info2 / "top_level.txt").write_text("_pywhispercpp\npywhispercpp\n")

    assert build_pack.top_level_modules(site) == ["_pywhispercpp", "llama_cpp", "pywhispercpp"]
    removed = build_pack.prune_dev_files(site)
    assert sorted(removed) == ["include/", "lib/"]
    assert not (site / "include").exists() and not (site / "lib").exists()
    assert (site / "bin" / "llama-cli.exe").exists()  # executables are left alone
    assert (site / "llama_cpp" / "lib" / "llama.dll").exists()  # package-internal DLLs untouched


def test_prune_keeps_lib_with_runtime_dlls(tmp_path):
    site = tmp_path / "sp"
    (site / "lib").mkdir(parents=True)
    (site / "lib" / "ggml.lib").write_bytes(b"")
    (site / "lib" / "ggml.dll").write_bytes(b"")
    assert build_pack.prune_dev_files(site) == []
    assert (site / "lib" / "ggml.dll").exists()
