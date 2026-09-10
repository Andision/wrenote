"""Optional features: a first run should not have to download 4.3 GB.

The default set is speech recognition (574 MB, shared by the live and the
after-recording slot), translation (1.1 GB), chat — which also writes the
minutes — (2.5 GB) and speaker identification (84 MB). Three of those are
things a given user may simply not want, and before this they were downloaded
anyway because ``required_models`` walked every slot.

A feature that is off resolves to no model, so it costs no download, and the
paths that would have used it say so instead of failing on a file that was
never fetched.
"""
from __future__ import annotations

import pytest
import yaml

from wrenote.core.catalogue import ModelCatalogue, feature_enabled, resolve
from wrenote.core.config import Config, user_config_path
from wrenote.core.models import required_models


def _cfg(**over) -> Config:
    base = {
        "stt": {"backend": "whisper_cpp", "model": "whisper-small-q5"},
        "stt_offline": {"backend": "whisper_cpp", "model": "whisper-small-q5"},
        "translator": {"backend": "llama_cpp", "model": "hy-mt2-1.8b-q4"},
        "chat": {"backend": "llama_cpp", "model": "qwen3-4b-instruct-q4"},
        "speaker": {"backend": "ecapa", "model": "ecapa-voxceleb"},
    }
    for slot, patch in over.items():
        base.setdefault(slot, {}).update(patch)
    return Config.model_validate(base)


# ---------- resolution ----------


def test_a_switched_off_slot_needs_no_download():
    cat = ModelCatalogue.load()
    on = {e.key for e in required_models(_cfg(), cat)}
    assert {"translator", "chat", "speaker"} <= on

    off = _cfg(chat={"enabled": False}, speaker={"enabled": False})
    keys = {e.key for e in required_models(off, cat)}
    assert "chat" not in keys and "speaker" not in keys
    assert "stt" in keys and "translator" in keys


def test_switching_off_keeps_the_model_choice():
    """Turning a feature back on should remember what it was set to, so the
    two are separate keys rather than `model: null` standing in for `off`."""
    cfg = _cfg(chat={"enabled": False})
    assert cfg.chat.model == "qwen3-4b-instruct-q4"
    r = resolve(cfg, "chat", ModelCatalogue.load())
    assert r.disabled and r.spec is None


def test_speech_recognition_cannot_be_switched_off():
    """`enabled: false` on a mandatory slot is ignored: the app is a
    transcriber, and a config that turned it off would just fail later."""
    cfg = _cfg(stt={"enabled": False})
    assert feature_enabled(cfg, "stt")
    assert resolve(cfg, "stt", ModelCatalogue.load()).spec is not None


# ---------- the HTTP surface ----------


def test_status_reports_the_features_and_ignores_what_is_off(client):
    body = client.get("/v1/models/status").json()
    assert body["features"] == {"translator": True, "chat": True, "speaker": True}

    r = client.post("/v1/models/features", json={"chat": False})
    assert r.status_code == 200
    assert r.json()["features"]["chat"] is False

    body = client.get("/v1/models/status").json()
    assert body["features"]["chat"] is False
    assert all(m["key"] != "chat" for m in body["models"])
    # Still offered, so Settings and the wizard can turn it back on.
    assert any(k["kind"] == "chat" for k in body["options"])


def test_features_persist_to_the_user_config(client):
    client.post("/v1/models/features", json={"translator": False, "speaker": False})
    written = yaml.safe_load(user_config_path().read_text(encoding="utf-8"))
    assert written["translator"]["enabled"] is False
    assert written["speaker"]["enabled"] is False
    assert "chat" not in written  # an omitted slot is left alone


def _record(client, sid="s1"):
    """A finished session to hang the per-session endpoints off."""
    with client.websocket_connect("/v1/ws") as ws:
        ws.send_json({"type": "start", "config": {"session_id": sid, "title": "T", "tgt": "zh"}})
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "stop"})


def test_chat_off_answers_a_code_the_client_can_act_on(client):
    """Not a 500 and not a sentence: `feature_off` is what turns into an offer
    to download the model, so it has to survive to the client as a code."""
    _record(client)
    conv = client.post("/v1/sessions/s1/conversations", json={}).json()["conversation"]
    client.post("/v1/models/features", json={"chat": False})
    r = client.post(
        f"/v1/sessions/s1/conversations/{conv['id']}/chat", json={"text": "hi"}
    )
    assert r.status_code == 503 and r.json()["detail"] == "feature_off"


def test_translation_off_refuses_the_jobs_that_would_need_it(client):
    _record(client)
    client.post("/v1/models/features", json={"translator": False})
    r = client.post("/v1/sessions/s1/translate", json={})
    assert r.status_code == 503 and r.json()["detail"] == "feature_off"
    r = client.post("/v1/sessions/s1/refine", json={"translate": True})
    assert r.status_code == 503 and r.json()["detail"] == "feature_off"


def test_turning_chat_back_on_restores_the_backend(client):
    manager = client.app.state.models
    client.post("/v1/models/features", json={"chat": False})
    assert manager.chat_backend is None
    client.post("/v1/models/features", json={"chat": True})
    assert manager.chat_backend is not None


@pytest.mark.parametrize("slot", ["stt", "stt_offline"])
def test_the_endpoint_will_not_switch_off_a_mandatory_slot(client, slot):
    r = client.post("/v1/models/features", json={slot: False})
    assert r.status_code == 422 or r.json()["features"].get(slot) is None
    assert getattr(client.app.state.config, slot).enabled is True


# ---------- deleting a model (the developer tools' one destructive act) ------


def test_deleting_a_model_removes_its_files_and_can_be_undone(client, tmp_path):
    """What makes the first-run flow testable without hunting for files by
    hand. Recoverable by construction: the catalogue still knows the URL."""
    models_dir = tmp_path / "models"
    models_dir.mkdir(exist_ok=True)
    cat = client.app.state.catalogue
    spec = cat.get("whisper-base-q5")
    for f in spec.files:
        f.local_path(models_dir).write_bytes(b"x" * 16)
    # A half-finished download beside it: deleting the model takes that too,
    # or a resume would pick up where a file that no longer exists left off.
    partial = spec.files[0].local_path(models_dir)
    partial.with_suffix(partial.suffix + ".partial").write_bytes(b"y")

    r = client.delete("/v1/models/whisper-base-q5")
    assert r.status_code == 200
    body = r.json()
    assert sorted(body["removed"]) == sorted(
        [f.local_path(models_dir).name for f in spec.files]
        + [partial.name + ".partial"]
    )
    assert body["failed"] == []
    assert not any(f.local_path(models_dir).exists() for f in spec.files)


def test_deleting_an_unknown_model_is_a_404(client):
    assert client.delete("/v1/models/nope").status_code == 404


def test_deleting_the_loaded_chat_model_drops_the_backend_first(client):
    """Windows will not unlink a file the process still has open, so the
    manager lets go before the delete rather than after."""
    client.post("/v1/models/select", json={"kind": "chat", "model": "qwen3-4b-instruct-q4"})
    assert client.app.state.models.chat_backend is not None
    r = client.delete("/v1/models/qwen3-4b-instruct-q4")
    assert r.status_code == 200 and r.json()["slots"] == ["chat"]
    assert client.app.state.models.chat_backend is None
