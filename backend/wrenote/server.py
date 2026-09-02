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

from . import __version__, ws
from .api import (
    capture,
    chat,
    compute,
    diarize,
    glossary,
    groups,
    jobs,
    models,
    recordings,
    segments,
    sessions,
    translate,
    upload,
)
from .auth import AUTH_TOKEN, install_loopback_auth
from .core.config import Config, load_config
from .core.jobs import JobRegistry
from .core.registry import make_chat, make_speaker
from .core.runtimes import RuntimeManager
from .core.store import Store
from .model_manager import ModelManager
from .platform import get_platform

log = logging.getLogger(__name__)

if getattr(sys, "frozen", False):
    # PyInstaller: bundled data lives under sys._MEIPASS.
    STATIC_DIR = Path(getattr(sys, "_MEIPASS", ".")) / "static"
else:
    STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

APP_DIR = STATIC_DIR / "app"  # built SPA (vite output); served at "/" last.

# Every HTTP resource and the WebSocket live under this prefix. Bump it (and
# keep the old one mounted) when a breaking change to the contract ships;
# clients pin the version they were built against. ``/health`` stays at the
# root: it is the shell's readiness probe and predates any versioning.
API_PREFIX = "/v1"

# Resource routers, registered in this order before the SPA catch-all.
_ROUTERS = (
    sessions, groups, recordings, jobs, models,
    upload, translate, diarize, segments, chat, capture, glossary, compute, ws,
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

        # Pick the compute runtime (CUDA / Vulkan / Metal / CPU) before any
        # native backend is imported — backends import their bindings lazily
        # in load() for exactly this reason. Falls back to the built-in runtime.
        runtimes = RuntimeManager(cfg.compute, get_platform())
        runtimes.activate()
        app.state.runtimes = runtimes

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
        @app.get("/", include_in_schema=False)
        async def root() -> dict[str, Any]:
            return {
                "service": "wrenote",
                "version": "0.1.0",
                "api": API_PREFIX,
                "ws": f"{API_PREFIX}/ws",
                "test_page": "/static/test.html",
            }

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get(f"{API_PREFIX}/info")
    async def info(request: Request) -> dict[str, Any]:
        cfg: Config = request.app.state.config
        plat = get_platform()
        runtimes: RuntimeManager = request.app.state.runtimes
        return {
            "config": cfg.model_dump(),
            "static_dir_exists": STATIC_DIR.exists(),
            "platform": {"name": plat.name, "capabilities": plat.capabilities.to_dict()},
            "compute": {
                "active": runtimes.active.variant if runtimes.active else None,
                "builtin": runtimes.builtin,
            },
        }


def create_app(config: Config | None = None, *, auth_token: str | None = None) -> FastAPI:
    """Build a wrenote ASGI app.

    ``config``: injected config (tests); ``None`` loads it at startup.
    ``auth_token``: loopback token; ``None`` falls back to the env-derived
    ``AUTH_TOKEN`` (empty in plain dev → auth disabled).
    """
    app = FastAPI(title="Wrenote Engine", version=__version__, lifespan=_make_lifespan(config))

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
        app.include_router(module.router, prefix=API_PREFIX)

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    _register_meta_routes(app)

    # SPA catch-all, mounted LAST so every API route + /static win; only
    # unmatched paths fall through. `html=True` serves index.html at "/".
    if APP_DIR.exists():
        app.mount("/", StaticFiles(directory=str(APP_DIR), html=True), name="spa")

    return app


app = create_app()
