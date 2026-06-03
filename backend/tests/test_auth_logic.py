"""Auth/origin logic unit tests (REFACTOR_PLAN.md Phase 0).

The loopback-auth *middleware* is registered at import time, gated on
``WRENOTE_AUTH_TOKEN`` being set before import — so it can't be toggled
per-test against the global app. That import-time gating is exactly why the
plan introduces ``create_app(config, token)``: it makes auth a first-class,
injectable concern. Until then we pin the two pure helpers that carry the auth
logic. They now live in ``wrenote.auth`` (``token_from_request`` /
``origin_allowed``); pinning them here keeps that move semantics-preserving.
"""
from __future__ import annotations

from types import SimpleNamespace

import wrenote.auth as auth


def _req(*, cookies=None, headers=None, query=None):
    """Minimal stand-in for starlette Request — _token_from_request only uses
    .cookies/.headers/.query_params with .get(), which dicts satisfy."""
    return SimpleNamespace(
        cookies=cookies or {},
        headers=headers or {},
        query_params=query or {},
    )


def test_token_precedence_cookie_first():
    req = _req(
        cookies={auth.AUTH_COOKIE: "from-cookie"},
        headers={"authorization": "Bearer from-header"},
        query={"token": "from-query"},
    )
    assert auth.token_from_request(req) == "from-cookie"


def test_token_bearer_when_no_cookie():
    req = _req(headers={"authorization": "Bearer abc123"}, query={"token": "q"})
    assert auth.token_from_request(req) == "abc123"


def test_token_query_when_no_cookie_or_header():
    req = _req(query={"token": "q-only"})
    assert auth.token_from_request(req) == "q-only"


def test_token_none_when_absent():
    assert auth.token_from_request(_req()) is None


def test_origin_allows_loopback_and_none():
    allowed = [
        None,
        "null",
        "http://localhost",
        "http://localhost:5173",
        "http://127.0.0.1:8000",
        "http://[::1]:9000",
        "https://127.0.0.1",
    ]
    for o in allowed:
        assert auth.origin_allowed(o) is True, o


def test_origin_rejects_remote():
    rejected = [
        "http://evil.example.com",
        "http://10.0.0.5:8000",
        "https://localhost.evil.com",  # prefix trick must not pass
        "http://127.0.0.1.evil.com",
    ]
    for o in rejected:
        assert auth.origin_allowed(o) is False, o
