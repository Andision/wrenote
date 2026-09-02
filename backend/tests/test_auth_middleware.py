"""Loopback-auth middleware behavior (REFACTOR_PLAN.md Phase 3).

Previously untestable: auth was gated on ``WRENOTE_AUTH_TOKEN`` read at import,
so it couldn't be toggled per-test. ``create_app(auth_token=...)`` makes it a
per-app concern, so we can finally assert the full middleware contract — the
concrete payoff of the factory.
"""
from __future__ import annotations

TOKEN = "test-secret"


def test_protected_route_401_without_token(auth_client):
    assert auth_client.get("/v1/sessions").status_code == 401


def test_health_is_public(auth_client):
    assert auth_client.get("/health").status_code == 200


def test_static_prefix_is_public(auth_client):
    # /static is a public prefix; a missing file is 404, NOT 401 (auth passed).
    assert auth_client.get("/static/nope.js").status_code != 401


def test_bearer_token_grants_access(auth_client):
    r = auth_client.get("/v1/sessions", headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 200


def test_query_token_grants_access(auth_client):
    assert auth_client.get(f"/v1/sessions?token={TOKEN}").status_code == 200


def test_wrong_token_is_401(auth_client):
    r = auth_client.get("/v1/sessions", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401


def test_root_hands_back_the_cookie(auth_client):
    # "/" is public and sets the auth cookie so the SPA's subsequent
    # fetch/SSE/WS are authenticated same-origin.
    r = auth_client.get("/")
    assert r.cookies.get("wrenote_token") == TOKEN


def test_cookie_grants_access(auth_client):
    auth_client.cookies.set("wrenote_token", TOKEN)
    assert auth_client.get("/v1/sessions").status_code == 200
