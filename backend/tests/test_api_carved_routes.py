"""Behavior coverage for the routes carved into api/{upload,translate,diarize,chat}.

The route-surface snapshot proves these are *registered*; these tests prove the
router wiring actually *works* end to end, exercising the model-free code paths
(validation + not-found) so a future change to the moved handlers is caught.
They deliberately avoid paths that would load real STT/translator/chat models.
"""
from __future__ import annotations


def test_translate_missing_session_404(client):
    assert client.post("/sessions/nope/translate").status_code == 404


def test_diarize_missing_session_404(client):
    assert client.post("/sessions/nope/diarize").status_code == 404


def test_conversations_list_empty(client):
    r = client.get("/sessions/any-sid/conversations")
    assert r.status_code == 200
    assert r.json() == {"conversations": []}


def test_create_conversation_missing_session_404(client):
    r = client.post("/sessions/nope/conversations", json={"title": "x"})
    assert r.status_code == 404


def test_chat_post_requires_text_400(client):
    # text is validated before any session/model work, so this is model-free.
    r = client.post(
        "/sessions/any/conversations/anyconv/chat", json={"text": "   "}
    )
    assert r.status_code == 400


def test_rename_speaker_requires_from_and_to_400(client):
    r = client.patch("/sessions/any/speakers", json={"from": "", "to": ""})
    assert r.status_code == 400


def test_assign_segment_speaker_validation_400(client):
    r = client.post(
        "/sessions/any/segments/speaker", json={"segmentIds": [], "speaker": ""}
    )
    assert r.status_code == 400


def test_title_suggest_missing_session_404(client):
    assert client.post("/sessions/nope/title/suggest").status_code == 404


def test_chat_routes_resolve_to_api_not_spa(client):
    """A carved POST route must hit the router (JSON 4xx), never the SPA."""
    r = client.post("/sessions/nope/diarize")
    assert r.headers["content-type"].startswith("application/json")
