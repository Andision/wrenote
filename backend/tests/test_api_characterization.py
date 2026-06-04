"""HTTP-surface characterization tests (REFACTOR_PLAN.md Phase 0).

These pin down the *current* behavior of the HTTP API and the static/SPA mount
ordering. They are the oracle the refactor must keep green: if a route moves,
disappears, gets swallowed by the SPA catch-all, or changes its status/shape,
one of these fails.

They intentionally assert observable behavior (status codes, JSON keys, mount
precedence), not internal structure, so they survive the server.py → app/deps/
api/* split unchanged.
"""
from __future__ import annotations

from fastapi.routing import APIRoute
from starlette.routing import Mount, WebSocketRoute

import wrenote.server as server

# Frozen snapshot of the route surface as of the pre-refactor baseline.
# (method, path) for every API route, plus WS and mounts. Captured from the
# live app; the refactor must reproduce this set exactly.
EXPECTED_ROUTES = {
    ("DELETE", "/groups/{group_id}"),
    ("DELETE", "/recordings/{session_id}.wav"),
    ("DELETE", "/sessions/{session_id}"),
    ("DELETE", "/sessions/{session_id}/conversations/{conversation_id}"),
    ("DELETE", "/sessions/{session_id}/conversations/{conversation_id}/chat"),
    ("GET", "/api/models/status"),
    ("GET", "/capture/targets"),
    ("GET", "/groups"),
    ("GET", "/health"),
    ("GET", "/info"),
    ("GET", "/jobs/{job_id}"),
    ("GET", "/jobs/{job_id}/stream"),
    ("GET", "/recordings/{session_id}.wav"),
    ("GET", "/sessions"),
    ("GET", "/sessions/{session_id}"),
    ("GET", "/sessions/{session_id}/export"),
    ("GET", "/sessions/{session_id}/conversations"),
    ("GET", "/sessions/{session_id}/conversations/{conversation_id}/chat"),
    ("MOUNT", "/"),
    ("MOUNT", "/static"),
    ("PATCH", "/groups/{group_id}"),
    ("PATCH", "/sessions/{session_id}"),
    ("PATCH", "/sessions/{session_id}/conversations/{conversation_id}"),
    ("PATCH", "/sessions/{session_id}/group"),
    ("PATCH", "/sessions/{session_id}/speakers"),
    ("POST", "/api/models/download"),
    ("POST", "/groups"),
    ("POST", "/sessions/upload"),
    ("POST", "/sessions/{session_id}/conversations"),
    ("POST", "/sessions/{session_id}/conversations/{conversation_id}/chat"),
    ("POST", "/sessions/{session_id}/diarize"),
    ("PATCH", "/sessions/{session_id}/segments/{segment_id}"),
    ("POST", "/sessions/{session_id}/segments/speaker"),
    ("POST", "/sessions/{session_id}/segments/{segment_id}/split"),
    ("POST", "/sessions/{session_id}/segments/{segment_id}/merge"),
    ("POST", "/sessions/{session_id}/title/suggest"),
    ("POST", "/sessions/{session_id}/translate"),
    ("WS", "/ws"),
}


def _current_routes() -> set[tuple[str, str]]:
    rows: set[tuple[str, str]] = set()
    for r in server.app.routes:
        if isinstance(r, APIRoute):
            for m in r.methods - {"HEAD", "OPTIONS"}:
                rows.add((m, r.path))
        elif isinstance(r, WebSocketRoute):
            rows.add(("WS", r.path))
        elif isinstance(r, Mount):
            rows.add(("MOUNT", r.path or "/"))
    return rows


def test_route_surface_snapshot():
    """The full (method, path) surface must match the frozen baseline exactly.

    This is the cheapest guard against a route silently moving or vanishing in
    the refactor. Adding a route is also a deliberate act — update the snapshot.
    """
    assert _current_routes() == EXPECTED_ROUTES


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_info(client):
    r = client.get("/info")
    assert r.status_code == 200
    body = r.json()
    assert "config" in body and isinstance(body["config"], dict)
    assert "static_dir_exists" in body
    # The mock config we injected must be what the running app loaded.
    assert body["config"]["stt"]["backend"] == "mock"


def test_sessions_empty_on_fresh_db(client):
    r = client.get("/sessions")
    assert r.status_code == 200
    assert r.json() == {"sessions": []}


def test_groups_empty_on_fresh_db(client):
    r = client.get("/groups")
    assert r.status_code == 200
    assert r.json() == {"groups": []}


def test_missing_session_404(client):
    r = client.get("/sessions/does-not-exist")
    assert r.status_code == 404


def test_missing_job_404(client):
    r = client.get("/jobs/nope")
    assert r.status_code == 404


def test_missing_recording_404(client):
    r = client.get("/recordings/nope.wav")
    assert r.status_code == 404


def test_models_status(client):
    r = client.get("/api/models/status")
    assert r.status_code == 200
    assert isinstance(r.json(), dict)


def test_group_create_list_roundtrip(client):
    created = client.post("/groups", json={"name": "Meetings"})
    assert created.status_code == 200
    grp = created.json()["group"]
    assert grp["name"] == "Meetings"
    gid = grp["id"]

    listed = client.get("/groups").json()["groups"]
    assert any(g["id"] == gid and g["name"] == "Meetings" for g in listed)

    # rename → reflected in the list
    assert client.patch(f"/groups/{gid}", json={"name": "Standups"}).status_code == 200
    listed2 = client.get("/groups").json()["groups"]
    assert any(g["id"] == gid and g["name"] == "Standups" for g in listed2)

    # delete → gone
    assert client.delete(f"/groups/{gid}").json() == {"status": "ok"}
    assert all(g["id"] != gid for g in client.get("/groups").json()["groups"])


def test_patch_session_requires_title(client):
    # 400 (bad body), not 404/500, is the current contract for an empty title.
    r = client.patch("/sessions/whatever", json={"title": "  "})
    assert r.status_code == 400


# ---------- mount-ordering: the highest-risk thing OpenAPI can't see ----------


def test_api_route_not_swallowed_by_spa(client):
    """/sessions must hit the API (JSON), not the SPA catch-all at '/'."""
    r = client.get("/sessions")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")


def test_spa_served_at_root(client):
    """With the SPA built (APP_DIR present), '/' serves index.html, not JSON."""
    assert server.APP_DIR.exists(), "test assumes a built SPA; run `npm run build`"
    r = client.get("/")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")


def test_unknown_path_is_404_not_spa_rewrite(client):
    """Characterization, not aspiration: ``StaticFiles(html=True)`` serves
    index.html for '/' but does NOT rewrite arbitrary deep links to the SPA —
    an unknown, non-file path returns 404. (If the frontend ever adds a router
    with reload-able deep links, this 404 is the thing that would need fixing;
    pinning it here means that future change is a conscious one.)"""
    r = client.get("/some-client-side-route")
    assert r.status_code == 404
