"""Behavior coverage for the routes carved into api/{upload,translate,diarize,chat}.

The route-surface snapshot proves these are *registered*; these tests prove the
router wiring actually *works* end to end, exercising the model-free code paths
(validation + not-found) so a future change to the moved handlers is caught.
They deliberately avoid paths that would load real STT/translator/chat models.
"""
from __future__ import annotations

import pytest
from starlette.websockets import WebSocketDisconnect


def test_capture_targets_returns_enumeration(client, monkeypatch):
    """GET /capture/targets returns whatever screenrec.list_targets() produces.

    Monkeypatched so the test never spawns the real ScreenCaptureKit helper (which
    needs Screen-Recording permission); this only checks the router→core wiring.
    """
    import wrenote.core.screenrec as screenrec

    async def fake_list_targets():
        return {
            "displays": [{"type": "display", "id": 1, "title": "Display 1", "width": 100, "height": 100}],
            "windows": [{"type": "window", "id": 9, "title": "Notes", "app": "Notes", "width": 80, "height": 80}],
        }

    monkeypatch.setattr(screenrec, "list_targets", fake_list_targets)
    r = client.get("/v1/capture/targets")
    assert r.status_code == 200
    body = r.json()
    assert [d["title"] for d in body["displays"]] == ["Display 1"]
    assert body["windows"][0]["app"] == "Notes"


def test_capture_targets_shape_without_permission(client):
    """Real call on this box: macOS without Screen-Recording permission (or non-mac)
    degrades to empty lists — still a 200 with the right shape, never a 500."""
    r = client.get("/v1/capture/targets")
    assert r.status_code == 200
    body = r.json()
    assert set(body.keys()) >= {"displays", "windows"}
    assert isinstance(body["displays"], list) and isinstance(body["windows"], list)


def test_translate_missing_session_404(client):
    assert client.post("/v1/sessions/nope/translate").status_code == 404


def test_diarize_missing_session_404(client):
    assert client.post("/v1/sessions/nope/diarize").status_code == 404


def test_conversations_list_empty(client):
    r = client.get("/v1/sessions/any-sid/conversations")
    assert r.status_code == 200
    assert r.json() == {"conversations": []}


def test_create_conversation_missing_session_404(client):
    r = client.post("/v1/sessions/nope/conversations", json={"title": "x"})
    assert r.status_code == 404


def test_chat_post_requires_text_400(client):
    # text is validated before any session/model work, so this is model-free.
    r = client.post(
        "/v1/sessions/any/conversations/anyconv/chat", json={"text": "   "}
    )
    assert r.status_code == 400


def test_rename_speaker_requires_from_and_to_400(client):
    r = client.patch("/v1/sessions/any/speakers", json={"from": "", "to": ""})
    assert r.status_code == 400


def test_assign_segment_speaker_validation_400(client):
    r = client.post(
        "/v1/sessions/any/segments/speaker", json={"segmentIds": [], "speaker": ""}
    )
    assert r.status_code == 400


def test_title_suggest_missing_session_404(client):
    assert client.post("/v1/sessions/nope/title/suggest").status_code == 404


def test_chat_routes_resolve_to_api_not_spa(client):
    """A carved POST route must hit the router (JSON 4xx), never the SPA."""
    r = client.post("/v1/sessions/nope/diarize")
    assert r.headers["content-type"].startswith("application/json")


def test_refine_missing_session_404(client):
    assert client.post("/v1/sessions/nope/refine").status_code == 404


def test_refine_without_recording_400(client):
    sid = "no-wav"
    with client.websocket_connect("/v1/ws") as ws:
        ws.send_json({"type": "start", "config": {"session_id": sid}})
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "stop"})
        # Wait for the server to finish its cleanup (it closes the socket last).
        with pytest.raises(WebSocketDisconnect):
            while True:
                ws.receive_json()
    # The WS wrote no audio, so there is nothing to refine — and the session
    # itself is no longer "recording" once the socket is gone.
    sess = client.get(f"/v1/sessions/{sid}").json()
    assert sess["status"] == "ready" and sess["job_id"] is None
    r = client.post(f"/v1/sessions/{sid}/refine")
    assert r.status_code == 400
    assert r.json()["detail"] == "no_recording"


def test_refine_rejects_non_boolean_translate(client):
    r = client.post("/v1/sessions/x/refine", json={"translate": "yes"})
    assert r.status_code == 400
