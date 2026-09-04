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
