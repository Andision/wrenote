"""Model catalogue: parsing, merging, resolution, and the download's integrity check.

The catalogue replaced a dict keyed by filename that the config had to match by
spelling that filename into a path. The tests that matter are therefore about
*resolution* — that a config still means what it used to — and about the two
promises the schema makes: a user file extends the shipped one, and a file whose
sha256 doesn't match never lands on disk.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import yaml

from wrenote.core.catalogue import (
    ModelCatalogue,
    bundled_catalogue_path,
    resolve,
    resolve_all,
)
from wrenote.core.config import Config, load_config
from wrenote.core.models import ModelEntry, download_model, required_models, sha256_of
from wrenote.platform.base import GpuInfo, HardwareInfo

SHIPPED = bundled_catalogue_path()


def _catalogue(tmp_path: Path, doc: dict, name: str = "models.yaml") -> Path:
    p = tmp_path / name
    p.write_text(yaml.safe_dump(doc), encoding="utf-8")
    return p


def _entry(model_id: str = "m1", **over) -> dict:
    row = {
        "id": model_id,
        "kind": "stt",
        "backend": "whisper_cpp",
        "tier": "small",
        "name": "Test model",
        "files": [{"param": "model_path", "filename": f"{model_id}.bin",
                   "repo": "acme/models", "path": f"{model_id}.bin",
                   "size": 10, "sha256": "ab" * 32}],
    }
    row.update(over)
    return row


# ---------- the shipped catalogue ----------


def test_shipped_catalogue_is_loadable_and_complete():
    """Every kind the default config uses must resolve, or a fresh install has
    no models and the failure only shows up at inference time."""
    cat = ModelCatalogue.load(user=Path("/nonexistent"))
    assert len(cat) >= 4
    for kind in ("stt", "translator", "chat", "speaker"):
        spec = cat.default_for(kind)
        assert spec is not None, kind
        assert spec.kind == kind
        assert spec.files and all(f.size > 0 for f in spec.files)


def test_shipped_catalogue_declares_a_checksum_for_every_file():
    """A missing sha256 silently downgrades the download to a size check."""
    cat = ModelCatalogue.load(user=Path("/nonexistent"))
    missing = [f"{m.id}/{f.filename}" for m in cat for f in m.files if len(f.sha256) != 64]
    assert not missing, f"no sha256 for: {missing}"


def test_default_config_resolves_to_the_shipped_defaults():
    cfg = load_config()
    resolved = resolve_all(cfg, ModelCatalogue.load(user=Path("/nonexistent")))
    assert resolved["stt"].spec is not None
    assert resolved["stt"].reason == "id"
    # The resolved path is where the downloader will put the file.
    assert resolved["stt"].params["model_path"].endswith(".bin")


# ---------- parsing and merging ----------


def test_user_catalogue_extends_and_overrides_by_id(tmp_path):
    shipped = _catalogue(tmp_path, {
        "schema": 1,
        "defaults": {"stt": "m1"},
        "models": [_entry("m1", name="Shipped")],
    })
    user = _catalogue(tmp_path, {
        "schema": 1,
        "models": [_entry("m1", name="Overridden"), _entry("m2", name="Added")],
    }, name="user.yaml")
    cat = ModelCatalogue.load(bundled=shipped, user=user)
    assert cat.get("m1").name == "Overridden"  # same id replaces
    assert cat.get("m2").name == "Added"  # new id extends
    assert [m.id for m in cat.for_kind("stt")] == ["m1", "m2"]  # order is stable


def test_a_broken_user_catalogue_does_not_take_the_app_down(tmp_path, caplog):
    shipped = _catalogue(tmp_path, {"schema": 1, "models": [_entry("m1")]})
    bad = tmp_path / "user.yaml"
    bad.write_text("this: is: not: valid: yaml:\n  - [", encoding="utf-8")
    cat = ModelCatalogue.load(bundled=shipped, user=bad)
    assert cat.get("m1") is not None


def test_a_malformed_entry_is_skipped_not_fatal(tmp_path):
    shipped = _catalogue(tmp_path, {
        "schema": 1,
        "models": [_entry("good"), {"id": "bad", "kind": "nonsense", "backend": "x"}],
    })
    cat = ModelCatalogue.load(bundled=shipped, user=Path("/nonexistent"))
    assert cat.get("good") is not None and cat.get("bad") is None


def test_missing_shipped_catalogue_yields_an_empty_one(tmp_path):
    cat = ModelCatalogue.load(bundled=tmp_path / "gone.yaml", user=tmp_path / "gone2.yaml")
    assert len(cat) == 0 and cat.default_for("stt") is None


# ---------- resolution ----------


def _cfg(tmp_path, **sections) -> Config:
    base = {"models": {"dir": str(tmp_path / "models")}}
    base.update(sections)
    return Config(**base)


def test_explicit_model_path_wins_and_is_not_downloadable(tmp_path):
    """The compatibility guarantee: a pre-catalogue ~/.wrenote/config.yaml keeps
    working, and we don't invent a download source for a file we don't know."""
    cat = ModelCatalogue.load(bundled=_catalogue(tmp_path, {
        "schema": 1, "defaults": {"stt": "m1"}, "models": [_entry("m1")]}),
        user=Path("/nonexistent"))
    cfg = _cfg(tmp_path, stt={"backend": "whisper_cpp",
                              "params": {"model_path": "/my/own.bin"}})
    r = resolve(cfg, "stt", cat)
    assert r.reason == "path" and r.spec is None and not r.downloadable
    assert r.params["model_path"] == "/my/own.bin"
    assert required_models(cfg, cat) == []


def test_model_id_supplies_the_path_and_config_params_win(tmp_path):
    cat = ModelCatalogue.load(bundled=_catalogue(tmp_path, {
        "schema": 1,
        "models": [_entry("m1", params={"n_ctx": 2048, "temperature": 0.0})],
    }), user=Path("/nonexistent"))
    cfg = _cfg(tmp_path, stt={"backend": "whisper_cpp", "model": "m1",
                              "params": {"n_ctx": 8192}})
    r = resolve(cfg, "stt", cat)
    assert r.reason == "id"
    assert r.params["model_path"] == str(tmp_path / "models" / "m1.bin")
    assert r.params["n_ctx"] == 8192  # the user's tuning beats the catalogue's
    assert r.params["temperature"] == 0.0  # …but the rest of the entry survives


def test_unknown_id_falls_back_to_the_default(tmp_path):
    cat = ModelCatalogue.load(bundled=_catalogue(tmp_path, {
        "schema": 1, "defaults": {"stt": "m1"}, "models": [_entry("m1")]}),
        user=Path("/nonexistent"))
    cfg = _cfg(tmp_path, stt={"backend": "whisper_cpp", "model": "typo"})
    r = resolve(cfg, "stt", cat)
    assert r.reason == "default" and r.spec.id == "m1"


def test_a_model_for_another_backend_is_refused(tmp_path):
    """Pointing whisper at a GGUF would fail deep inside a native library."""
    cat = ModelCatalogue.load(bundled=_catalogue(tmp_path, {
        "schema": 1,
        "models": [_entry("llm", kind="stt", backend="llama_cpp")],
    }), user=Path("/nonexistent"))
    cfg = _cfg(tmp_path, stt={"backend": "whisper_cpp", "model": "llm"})
    assert resolve(cfg, "stt", cat).spec is None


def test_mock_backends_need_nothing(tmp_path):
    cat = ModelCatalogue.load(user=Path("/nonexistent"))
    cfg = _cfg(tmp_path, stt={"backend": "mock"}, translator={"backend": "mock"},
               chat={"backend": "mock"}, speaker={"backend": "disabled"})
    assert required_models(cfg, cat) == []
    assert resolve(cfg, "stt", cat).reason == "backend-needs-no-model"


def test_required_models_lists_every_file_with_its_source(tmp_path):
    cat = ModelCatalogue.load(bundled=_catalogue(tmp_path, {
        "schema": 1, "defaults": {"stt": "m1"}, "models": [_entry("m1")]}),
        user=Path("/nonexistent"))
    cfg = _cfg(tmp_path, stt={"backend": "whisper_cpp", "model": "m1"},
               translator={"backend": "mock"}, chat={"backend": "mock"},
               speaker={"backend": "disabled"})
    [entry] = required_models(cfg, cat)
    assert entry.key == "stt" and entry.model_id == "m1"
    assert entry.url.endswith("/acme/models/resolve/main/m1.bin")
    assert entry.path == tmp_path / "models" / "m1.bin"
    assert entry.status_dict()["model_name"] == "Test model"


# ---------- download integrity ----------


class _FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self._data = payload
        self.status = 200
        self.headers = {"Content-Length": str(len(payload))}

    def read(self, n: int) -> bytes:
        out, self._data = self._data[:n], self._data[n:]
        return out

    def close(self) -> None:
        pass


@pytest.fixture
def served(monkeypatch):
    """Serve fixed bytes to the downloader instead of reaching HuggingFace."""
    def _serve(payload: bytes):
        monkeypatch.setattr(
            "urllib.request.urlopen", lambda *a, **k: _FakeResponse(payload)
        )
    return _serve


def _run(entry: ModelEntry) -> None:
    asyncio.run(download_model(entry, lambda frac, status: None))


def test_download_verifies_the_checksum(tmp_path, served):
    payload = b"the real weights"
    served(payload)
    entry = ModelEntry(key="stt", path=tmp_path / "m.bin", url="https://x/m.bin",
                       approx_size=len(payload), sha256=sha256_of_bytes(payload))
    _run(entry)
    assert entry.path.read_bytes() == payload


def test_a_wrong_checksum_leaves_nothing_behind(tmp_path, served):
    """A file that hashes wrong must not survive: `present` is a size check, so
    a bad file of the right length would be accepted forever after."""
    served(b"tampered content")
    entry = ModelEntry(key="stt", path=tmp_path / "m.bin", url="https://x/m.bin",
                       approx_size=16, sha256="00" * 32)
    with pytest.raises(RuntimeError, match="checksum mismatch"):
        _run(entry)
    assert not entry.path.exists()
    assert not entry.partial.exists()


def test_no_checksum_still_downloads(tmp_path, served):
    """Upstreams without a content hash (a non-LFS file) must still work."""
    payload = b"unverifiable"
    served(payload)
    entry = ModelEntry(key="stt", path=tmp_path / "m.bin", url="https://x/m.bin",
                       approx_size=len(payload), sha256="")
    _run(entry)
    assert entry.path.read_bytes() == payload


def sha256_of_bytes(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


def test_sha256_of_matches_hashlib(tmp_path):
    p = tmp_path / "f"
    p.write_bytes(b"x" * (1 << 20) + b"tail")
    assert sha256_of(p) == sha256_of_bytes(p.read_bytes())


# ---------- matching models to the machine ----------


def _hw(ram_gb: int, gpus=()) -> HardwareInfo:
    return HardwareInfo(
        os="win32", arch="x86_64", cpu_count=8, ram_mb=ram_gb * 1024,
        gpus=tuple(gpus), npu=None, accelerators=("cpu",),
    )


@pytest.fixture
def tiers(tmp_path):
    """A kind with all three tiers, so ranking has something to rank."""
    doc = {"schema": 1, "models": [
        _entry("s", tier="small", requires={"ram_mb": 2048}),
        _entry("m", tier="medium", requires={"ram_mb": 8192}),
        _entry("l", tier="large", requires={"ram_mb": 16384}),
    ]}
    return ModelCatalogue.load(bundled=_catalogue(tmp_path, doc), user=Path("/nonexistent"))


@pytest.mark.parametrize(
    "ram_gb, expected, reason",
    [(4, "s", "limited_ram"), (8, "m", "moderate_ram"), (16, "l", "ample_ram"),
     (64, "l", "ample_ram")],
)
def test_recommendation_follows_total_ram(tiers, tmp_path, ram_gb, expected, reason):
    ko = tiers.options("stt", _hw(ram_gb), models_dir=tmp_path)
    assert ko.reason_code == reason
    assert [o.id for o in ko.options if o.recommended] == [expected]


def test_a_big_discrete_gpu_lifts_one_tier(tiers, tmp_path):
    """Weights move off system RAM, so the machine punches above its RAM."""
    gpu = GpuInfo(vendor="nvidia", name="RTX 4070", vram_mb=12282)
    ko = tiers.options("stt", _hw(8, [gpu]), models_dir=tmp_path)
    assert ko.reason_code == "gpu_headroom"
    assert [o.id for o in ko.options if o.recommended] == ["l"]


def test_shared_memory_gpus_do_not_lift(tiers, tmp_path):
    """An iGPU's VRAM *is* system RAM — counting it twice would over-promise."""
    igpu = GpuInfo(vendor="intel", name="Iris Xe", vram_mb=8192, unified_memory=True)
    ko = tiers.options("stt", _hw(8, [igpu]), models_dir=tmp_path)
    assert [o.id for o in ko.options if o.recommended] == ["m"]


def test_models_that_do_not_fit_stay_visible_with_the_reason(tiers, tmp_path):
    ko = tiers.options("stt", _hw(4), models_dir=tmp_path)
    large = next(o for o in ko.options if o.id == "l")
    assert not large.fits and large.blocked_code == "needs_ram"
    assert large.blocked_params["need"] == "16 GB"
    assert not large.recommended  # not suggested while something else fits


def test_recommends_the_smallest_when_nothing_fits(tiers, tmp_path):
    """A 1 GB machine fits nothing, but the user still has to pick something to
    use the app at all — the smallest, with its blocker visible, beats no
    recommendation."""
    ko = tiers.options("stt", _hw(1), models_dir=tmp_path)
    recommended = [o for o in ko.options if o.recommended]
    assert len(recommended) == 1 and recommended[0].tier == "small"


def test_options_report_installed_and_download_size(tiers, tmp_path):
    models = tmp_path / "models"
    models.mkdir()
    (models / "s.bin").write_bytes(b"x")
    ko = tiers.options("stt", _hw(16), models_dir=models, selected="m")
    by_id = {o.id: o for o in ko.options}
    assert by_id["s"].installed and by_id["s"].download_mb is None
    assert not by_id["m"].installed and by_id["m"].download_mb == 1
    assert by_id["m"].selected and not by_id["s"].selected


def test_options_are_ordered_smallest_first(tiers, tmp_path):
    ko = tiers.options("stt", _hw(16), models_dir=tmp_path)
    assert [o.tier for o in ko.options] == ["small", "medium", "large"]


# ---------- the HTTP surface ----------
# The `client` fixture runs an all-mock config, so these exercise the endpoint's
# own logic (validation, persistence, how soon a change applies) without loading
# a native backend.


def test_status_offers_options_per_kind(client):
    body = client.get("/v1/models/status").json()
    kinds = {row["kind"] for row in body["options"]}
    assert {"stt", "translator", "chat", "speaker"} <= kinds
    stt = next(r for r in body["options"] if r["kind"] == "stt")
    assert stt["reason_code"]  # always says why this tier
    assert sum(o["recommended"] for o in stt["options"]) == 1
    assert all({"id", "tier", "size_mb", "installed", "fits"} <= o.keys()
               for o in stt["options"])


def test_select_rejects_unknown_kinds_and_models(client):
    assert client.post("/v1/models/select",
                       json={"kind": "nope", "model": "x"}).status_code == 400
    assert client.post("/v1/models/select",
                       json={"kind": "stt", "model": "nope"}).status_code == 404
    # A chat model is not an STT model.
    assert client.post("/v1/models/select",
                       json={"kind": "stt", "model": "qwen3-4b-instruct-q4"}).status_code == 404


def test_select_refuses_when_the_backend_cannot_run_the_model(client):
    """The mock config's stt backend is `mock`; whisper models don't apply."""
    r = client.post("/v1/models/select",
                    json={"kind": "stt", "model": "whisper-small-q5"})
    assert r.status_code == 409 and "backend" in r.json()["detail"]


@pytest.fixture
def chat_client(monkeypatch, tmp_path):
    """An app whose chat backend can actually run a catalogue model, so the
    "applies now" path is exercised. Constructing LlamaCppChat is cheap — the
    binding and the weights load lazily in `load()` — so no model is touched."""
    from fastapi.testclient import TestClient

    import wrenote.core.config as config_mod
    import wrenote.server as server

    monkeypatch.setattr(config_mod, "USER_CONFIG", tmp_path / "config.yaml")
    cfg = Config.model_validate({
        "stt": {"backend": "mock"},
        "vad": {"backend": "disabled"},
        "translator": {"backend": "mock"},
        "speaker": {"backend": "disabled"},
        "chat": {"backend": "llama_cpp", "model": "qwen3-4b-instruct-q4"},
        "data": {"dir": str(tmp_path)},
        "compute": {"runtimes_index_url": ""},
    })
    with TestClient(server.create_app(cfg)) as c:
        yield c


def test_selecting_a_chat_model_applies_without_a_restart(chat_client, tmp_path):
    """Chat is held by ModelManager, so it is swapped in place. Telling the user
    to restart when nothing needs restarting trains them to ignore the notice."""
    before = chat_client.app.state.models.chat_backend
    r = chat_client.post("/v1/models/select",
                         json={"kind": "chat", "model": "qwen3-1.7b-instruct-q4"})
    assert r.status_code == 200
    body = r.json()
    assert body["applies"] == "now" and body["restart_required"] is False
    assert chat_client.app.state.models.chat_backend is not before
    # Persisted, so the choice survives a restart too.
    assert "qwen3-1.7b-instruct-q4" in (tmp_path / "config.yaml").read_text()
    # …and reflected in what the next status call reports as selected.
    assert chat_client.get("/v1/models/status").json()["selected"]["chat"] == (
        "qwen3-1.7b-instruct-q4"
    )


def test_selecting_an_stt_model_applies_to_the_next_session(client, tmp_path, monkeypatch):
    """STT backends are built per WebSocket connection, so the change lands on
    the next session — no restart, but not retroactive either."""
    import wrenote.core.config as config_mod

    monkeypatch.setattr(config_mod, "USER_CONFIG", tmp_path / "config.yaml")
    client.app.state.config.stt.backend = "whisper_cpp"
    r = client.post("/v1/models/select", json={"kind": "stt", "model": "whisper-base-q5"})
    assert r.status_code == 200
    assert r.json()["applies"] == "next_session"
    assert client.app.state.config.stt.model == "whisper-base-q5"


def test_select_refuses_while_an_explicit_path_is_pinned(client):
    """model_path is the escape hatch; silently overriding it would lose the
    user's own file."""
    client.app.state.config.stt.backend = "whisper_cpp"
    client.app.state.config.stt.params["model_path"] = "/my/own.bin"
    r = client.post("/v1/models/select", json={"kind": "stt", "model": "whisper-base-q5"})
    assert r.status_code == 409 and "model_path" in r.json()["detail"]
