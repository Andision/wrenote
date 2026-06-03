"""FastAPI application factory + assembly.

``create_app()`` wires the lifespan, CORS, loopback auth, the ``api/*`` routers,
the WebSocket router, and the static/SPA mounts — in that order, so every API
route and ``/static`` take precedence over the SPA catch-all at ``/``.

The module also exposes ``app = create_app()`` so the ``"wrenote.server:app"``
ASGI string used by the desktop launcher and ``__main__`` keeps working. The
factory takes ``config`` and ``auth_token`` so tests can build an isolated,
optionally-authenticated app without monkeypatching module globals.
"""
from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from . import ws
from .api import (
    chat,
    diarize,
    groups,
    jobs,
    models,
    recordings,
    sessions,
    translate,
    upload,
)
from .auth import AUTH_TOKEN, install_loopback_auth
from .core.config import Config, load_config
from .core.jobs import JobRegistry
from .core.registry import make_chat, make_speaker
from .core.store import Store
from .model_manager import ModelManager

log = logging.getLogger(__name__)

if getattr(sys, "frozen", False):
    # PyInstaller: bundled data lives under sys._MEIPASS.
    STATIC_DIR = Path(getattr(sys, "_MEIPASS", ".")) / "static"
else:
    STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

APP_DIR = STATIC_DIR / "app"  # built SPA (vite output); served at "/" last.

# Resource routers, registered in this order before the SPA catch-all.
_ROUTERS = (
    sessions, groups, recordings, jobs, models,
    upload, translate, diarize, chat, ws,
)


def _make_lifespan(config: Config | None):
    """Build a lifespan bound to ``config`` (or ``load_config()`` at startup)."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        )
        cfg = config if config is not None else load_config()
        app.state.config = cfg

        if cfg.server.host not in {"127.0.0.1", "localhost", "::1"}:
            log.warning(
                "Server is bound to %s — this exposes the WebSocket to your LAN. "
                "Anyone on this network can capture your microphone. Bind to "
                "127.0.0.1 unless you have explicitly opted into LAN access.",
                cfg.server.host,
            )
        log.info("Loaded config: server=%s:%d  stt=%s vad=%s translator=%s",
                 cfg.server.host, cfg.server.port,
                 cfg.stt.backend, cfg.vad.backend, cfg.translator.backend)

        store = Store()
        await store.open()
        app.state.store = store
        # Chat + offline-diarize models are instantiated up-front (cheap) but
        # their weights load lazily on first use — most sessions never invoke
        # chat (~3GB) or diarization, so paying the load cost at startup is
        # wasteful. ModelManager owns that lazy lifecycle.
        diarize_speaker = (
            make_speaker(cfg.speaker.backend, cfg.speaker.params)
            if cfg.speaker.backend not in (None, "", "disabled")
            else None
        )
        app.state.models = ModelManager(
            chat_backend=make_chat(cfg.chat.backend, cfg.chat.params),
            diarize_speaker=diarize_speaker,
        )
        # In-memory job registry for async upload + diarize.
        app.state.jobs = JobRegistry()
        try:
            yield
        finally:
            await app.state.models.aclose()
            await store.close()

    return lifespan


def _register_meta_routes(app: FastAPI) -> None:
    """Health/info, plus a dev-only JSON banner at ``/`` when the SPA isn't built."""
    if not APP_DIR.exists():
        # Once the SPA is built, its mount owns "/" instead.
        @app.get("/")
        async def root() -> dict[str, Any]:
            return {
                "service": "wrenote",
                "version": "0.1.0",
                "ws": "/ws",
                "test_page": "/static/test.html",
            }

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/info")
    async def info(request: Request) -> dict[str, Any]:
        cfg: Config = request.app.state.config
        return {
            "config": cfg.model_dump(),
            "static_dir_exists": STATIC_DIR.exists(),
        }


def create_app(config: Config | None = None, *, auth_token: str | None = None) -> FastAPI:
    """Build a wrenote ASGI app.

    ``config``: injected config (tests); ``None`` loads it at startup.
    ``auth_token``: loopback token; ``None`` falls back to the env-derived
    ``AUTH_TOKEN`` (empty in plain dev → auth disabled).
    """
    app = FastAPI(title="Wrenote", lifespan=_make_lifespan(config))

    # Allow the Vite dev server (different port) to call HTTP endpoints. The
    # WebSocket has its own origin check; this is for fetch/XHR.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173", "http://127.0.0.1:5173",
            "http://localhost:4173", "http://127.0.0.1:4173",
        ],
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )
    # Loopback-auth HTTP middleware (no-op when the token is empty). The WS
    # upgrade is NOT covered by HTTP middleware, so ws.py gates itself separately.
    install_loopback_auth(app, AUTH_TOKEN if auth_token is None else auth_token)

    for module in _ROUTERS:
        app.include_router(module.router)

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    _register_meta_routes(app)

    # SPA catch-all, mounted LAST so every API route + /static win; only
    # unmatched paths fall through. `html=True` serves index.html at "/".
    if APP_DIR.exists():
        app.mount("/", StaticFiles(directory=str(APP_DIR), html=True), name="spa")

    return app


app = create_app()
