"""Loopback authentication + WebSocket origin validation.

The desktop launcher sets ``WRENOTE_AUTH_TOKEN`` to a random per-launch secret.
All local pages share the loopback interface and pass the WS origin check, so
the token — handed to our own webview as a same-origin cookie when it loads the
SPA — is what actually keeps other local pages out of the API/WebSocket. Unset
(e.g. plain ``uvicorn ...`` in dev) => auth disabled, nothing changes.

``AUTH_TOKEN`` is read once at import (matching the previous behavior). Folding
this into ``create_app(config, token)`` so it becomes per-app injectable is the
job of a later phase; until then both ``server`` and ``ws`` import from here.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

log = logging.getLogger(__name__)

AUTH_TOKEN = os.environ.get("WRENOTE_AUTH_TOKEN", "")
AUTH_COOKIE = "wrenote_token"

# Reachable without a token so the shell can bootstrap and pick up the cookie:
# the SPA entry, its assets, and the health probe.
_PUBLIC_PREFIXES = ("/assets", "/static")
_PUBLIC_PATHS = {"/", "/health", "/favicon.svg", "/icons.svg"}


def token_from_request(request: Request) -> str | None:
    cookie = request.cookies.get(AUTH_COOKIE)
    if cookie:
        return cookie
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:]
    return request.query_params.get("token")


def origin_allowed(origin: str | None) -> bool:
    """Allow only local origins for the WebSocket.

    Accepted:
    * No Origin header (some non-browser clients omit it)
    * ``null`` (Origin header from ``file://`` pages)
    * ``http://localhost`` or ``http://127.0.0.1`` (any port)
    * ``http://[::1]`` (IPv6 loopback)
    """
    if origin is None or origin == "null":
        return True
    local_prefixes = (
        "http://localhost",
        "http://127.0.0.1",
        "http://[::1]",
        "https://localhost",
        "https://127.0.0.1",
        "https://[::1]",
    )
    for prefix in local_prefixes:
        if origin == prefix or origin.startswith(prefix + ":") or origin.startswith(prefix + "/"):
            return True
    return False


def install_loopback_auth(app: FastAPI) -> None:
    """Register the loopback-auth HTTP middleware when a token is configured.

    No-op when ``AUTH_TOKEN`` is unset (plain dev), so behavior is unchanged.
    """
    if not AUTH_TOKEN:
        return

    @app.middleware("http")
    async def loopback_auth(request: Request, call_next: Any) -> Any:
        path = request.url.path
        if (
            path not in _PUBLIC_PATHS
            and not path.startswith(_PUBLIC_PREFIXES)
            and token_from_request(request) != AUTH_TOKEN
        ):
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
        response = await call_next(request)
        # Hand the SPA its token on entry; subsequent fetch/SSE/WS carry the
        # cookie automatically (same-origin), so no frontend changes are needed.
        if path == "/":
            response.set_cookie(
                AUTH_COOKIE, AUTH_TOKEN, samesite="strict", path="/", max_age=86400
            )
        return response
