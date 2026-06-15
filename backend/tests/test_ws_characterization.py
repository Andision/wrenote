"""WebSocket characterization tests (REFACTOR_PLAN.md Phase 0).

Cover the /ws path the refactor will move into ws.py: the origin gate, the
start handshake, persistence-on-start, and the bad-first-message error contract.
Uses mock backends (from the `client` fixture's config) so the pipeline starts
instantly with no native models and no real audio.
"""
from __future__ import annotations

import pytest
from starlette.websockets import WebSocketDisconnect


def test_ws_start_persists_session_and_acks_ready(client):
    """start → server upserts the session row and replies with a ReadyEvent.

    Asserting the row via GET /sessions afterwards exercises the WS→Store path
    that the refactor must preserve.
    """
    sid = "test-session-abc"
    with client.websocket_connect("/ws") as ws:
        ws.send_json(
            {"type": "start", "config": {"session_id": sid, "title": "Daily sync"}}
        )
        ready = ws.receive_json()
        assert ready["type"] == "ready"
        # graceful stop so cleanup runs the normal path
        ws.send_json({"type": "stop"})

    sess = client.get(f"/sessions/{sid}")
    assert sess.status_code == 200
    assert sess.json()["title"] == "Daily sync"


def test_ws_first_message_must_be_start(client):
    """A non-'start' first message yields a BAD_CONFIG error event."""
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "feed"})
        evt = ws.receive_json()
        assert evt["type"] == "error"
        assert evt["code"] == "BAD_CONFIG"


def test_ws_invalid_json_first_message(client):
    with client.websocket_connect("/ws") as ws:
        ws.send_text("not json{")
        evt = ws.receive_json()
        assert evt["type"] == "error"
        assert evt["code"] == "BAD_CONFIG"


def test_ws_rejects_foreign_origin(client):
    """A non-local Origin is closed (1008) before accept — the LAN guard."""
    with pytest.raises(WebSocketDisconnect), client.websocket_connect(
        "/ws", headers={"origin": "http://evil.example.com"}
    ):
        pass
