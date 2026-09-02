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

# Frozen snapshot of the route surface (contract v1: everything under /v1
# except the shell's /health probe and the static/SPA mounts).
# (method, path) for every API route, plus WS and mounts. Captured from the
# live app; the refactor must reproduce this set exactly.
EXPECTED_ROUTES = {
    ("DELETE", "/v1/groups/{group_id}"),
    ("DELETE", "/v1/recordings/{session_id}.wav"),
    ("DELETE", "/v1/sessions/{session_id}"),
    ("DELETE", "/v1/sessions/{session_id}/conversations/{conversation_id}"),
    ("DELETE", "/v1/sessions/{session_id}/conversations/{conversation_id}/chat"),
    ("DELETE", "/v1/compute/packs/{variant}"),
    ("GET", "/v1/compute/status"),
    ("POST", "/v1/compute/install"),
    ("POST", "/v1/compute/select"),
    ("GET", "/v1/models/status"),
    ("GET", "/v1/capture/targets"),
    ("GET", "/v1/glossary"),
    ("PUT", "/v1/glossary"),
    ("GET", "/v1/groups"),
    ("GET", "/health"),
    ("GET", "/v1/info"),
    ("GET", "/v1/jobs/{job_id}"),
    ("GET", "/v1/jobs/{job_id}/stream"),
    ("GET", "/v1/recordings/{session_id}.wav"),
    ("GET", "/v1/sessions"),
    ("GET", "/v1/sessions/{session_id}"),
    ("GET", "/v1/sessions/{session_id}/export"),
    ("GET", "/v1/sessions/{session_id}/conversations"),
    ("GET", "/v1/sessions/{session_id}/conversations/{conversation_id}/chat"),
    ("MOUNT", "/"),
    ("MOUNT", "/static"),
    ("PATCH", "/v1/groups/{group_id}"),
    ("PATCH", "/v1/sessions/{session_id}"),
    ("PATCH", "/v1/sessions/{session_id}/conversations/{conversation_id}"),
    ("PATCH", "/v1/sessions/{session_id}/group"),
    ("PATCH", "/v1/sessions/{session_id}/speakers"),
    ("POST", "/v1/models/download"),
    ("POST", "/v1/groups"),
    ("POST", "/v1/sessions/upload"),
    ("POST", "/v1/sessions/{session_id}/conversations"),
    ("POST", "/v1/sessions/{session_id}/conversations/{conversation_id}/chat"),
    ("POST", "/v1/sessions/{session_id}/diarize"),
    ("PATCH", "/v1/sessions/{session_id}/segments/{segment_id}"),
    ("POST", "/v1/sessions/{session_id}/segments/speaker"),
    ("POST", "/v1/sessions/{session_id}/segments/{segment_id}/split"),
    ("POST", "/v1/sessions/{session_id}/segments/{segment_id}/merge"),
    ("POST", "/v1/sessions/{session_id}/title/suggest"),
    ("POST", "/v1/sessions/{session_id}/translate"),
    ("WS", "/v1/ws"),
}


def _walk_routes(routes, prefix: str = ""):
    """Yield (route, effective_path) for every leaf route.

    FastAPI < 0.140 flattens included routers into ``app.routes``; newer
    versions keep an ``_IncludedRouter`` node whose ``include_context.prefix``
    must be prepended to the original router's route paths.
    """
    for r in routes:
        original = getattr(r, "original_router", None)
        if original is not None:
            ctx = getattr(r, "include_context", None)
            sub_prefix = prefix + (getattr(ctx, "prefix", "") or "")
            yield from _walk_routes(original.routes, sub_prefix)
        else:
            yield r, prefix + (getattr(r, "path", "") or "")


def _current_routes() -> set[tuple[str, str]]:
    rows: set[tuple[str, str]] = set()
    for r, path in _walk_routes(server.app.routes):
        if isinstance(r, APIRoute):
            for m in r.methods - {"HEAD", "OPTIONS"}:
                rows.add((m, path))
        elif isinstance(r, WebSocketRoute):
            rows.add(("WS", path))
        elif isinstance(r, Mount):
            rows.add(("MOUNT", path or "/"))
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
    r = client.get("/v1/info")
    assert r.status_code == 200
    body = r.json()
    assert "config" in body and isinstance(body["config"], dict)
    assert "static_dir_exists" in body
    # The mock config we injected must be what the running app loaded.
    assert body["config"]["stt"]["backend"] == "mock"


def test_sessions_empty_on_fresh_db(client):
    r = client.get("/v1/sessions")
    assert r.status_code == 200
    assert r.json() == {"sessions": []}


def test_groups_empty_on_fresh_db(client):
    r = client.get("/v1/groups")
    assert r.status_code == 200
    assert r.json() == {"groups": []}


def test_missing_session_404(client):
    r = client.get("/v1/sessions/does-not-exist")
    assert r.status_code == 404


def test_missing_job_404(client):
    r = client.get("/v1/jobs/nope")
    assert r.status_code == 404


def test_missing_recording_404(client):
    r = client.get("/v1/recordings/nope.wav")
    assert r.status_code == 404


def test_models_status(client):
    r = client.get("/v1/models/status")
    assert r.status_code == 200
    assert isinstance(r.json(), dict)


def test_group_create_list_roundtrip(client):
    created = client.post("/v1/groups", json={"name": "Meetings"})
    assert created.status_code == 200
    grp = created.json()["group"]
    assert grp["name"] == "Meetings"
    gid = grp["id"]

    listed = client.get("/v1/groups").json()["groups"]
    assert any(g["id"] == gid and g["name"] == "Meetings" for g in listed)

    # rename → reflected in the list
    assert client.patch(f"/v1/groups/{gid}", json={"name": "Standups"}).status_code == 200
    listed2 = client.get("/v1/groups").json()["groups"]
    assert any(g["id"] == gid and g["name"] == "Standups" for g in listed2)

    # delete → gone
    assert client.delete(f"/v1/groups/{gid}").json() == {"status": "ok"}
    assert all(g["id"] != gid for g in client.get("/v1/groups").json()["groups"])


def test_patch_session_requires_title(client):
    # 400 (bad body), not 404/500, is the current contract for an empty title.
    r = client.patch("/v1/sessions/whatever", json={"title": "  "})
    assert r.status_code == 400


# ---------- mount-ordering: the highest-risk thing OpenAPI can't see ----------


def test_api_route_not_swallowed_by_spa(client):
    """/v1/sessions must hit the API (JSON), not the SPA catch-all at '/'."""
    r = client.get("/v1/sessions")
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
