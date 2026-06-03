"""FastAPI WebSocket server.

Per design.v1.1 §5. Single endpoint ``/ws`` carries:

* Client → server: binary PCM frames + JSON control messages (``start``/``stop``/``switch_lang``).
* Server → client: JSON events (``ready``, ``speech_start``, ``partial``, ``final``,
  ``translation``, ``error``, ``metric``).

A new :class:`Pipeline` is created per WebSocket connection. P1-a single-user
focus; shared-backend pooling is a later optimisation.
"""
from __future__ import annotations

import asyncio
import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import (
    FastAPI,
    Request,
)
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
from .auth import install_loopback_auth
from .core.config import Config, load_config
from .core.jobs import JobRegistry
from .core.registry import make_chat, make_speaker
from .core.store import Store

log = logging.getLogger(__name__)

if getattr(sys, "frozen", False):
    # PyInstaller: bundled data lives under sys._MEIPASS.
    STATIC_DIR = Path(getattr(sys, "_MEIPASS", ".")) / "static"
else:
    STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load config once at startup; warn loudly on insecure host binding."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    cfg = load_config()
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
    # Chat backend is instantiated up-front (cheap) but the model is loaded
    # lazily on first chat request — Qwen3.5-4B is ~3GB and most sessions
    # never invoke chat, so paying the load cost at startup is wasteful.
    app.state.chat_backend = make_chat(cfg.chat.backend, cfg.chat.params)
    app.state.chat_loaded = False
    app.state.chat_load_lock = asyncio.Lock()
    # Offline-diarize backend: also lazy. Same pattern.
    app.state.diarize_speaker = (
        make_speaker(cfg.speaker.backend, cfg.speaker.params)
        if cfg.speaker.backend not in (None, "", "disabled")
        else None
    )
    app.state.diarize_loaded = False
    app.state.diarize_load_lock = asyncio.Lock()
    # In-memory job registry for async upload + diarize.
    app.state.jobs = JobRegistry()
    try:
        yield
    finally:
        if app.state.chat_loaded:
            try:
                await app.state.chat_backend.unload()
            except Exception:
                log.exception("chat backend unload failed")
        if app.state.diarize_loaded and app.state.diarize_speaker is not None:
            try:
                await app.state.diarize_speaker.unload()
            except Exception:
                log.exception("diarize speaker unload failed")
        await store.close()


app = FastAPI(title="Wrenote", lifespan=lifespan)

# Allow the Vite dev server (different port) to call HTTP endpoints. The
# WebSocket has its own origin check; this is for fetch/XHR (recording
# download, future session DB endpoints).
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173", "http://127.0.0.1:5173",
        "http://localhost:4173", "http://127.0.0.1:4173",
    ],
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

# Loopback-auth HTTP middleware (no-op unless WRENOTE_AUTH_TOKEN is set). The WS
# upgrade is NOT covered by HTTP middleware, so ws.py gates itself separately.
install_loopback_auth(app)


APP_DIR = STATIC_DIR / "app"  # built SPA (vite output); served at "/" at EOF.

# Resource routers (carved out of this module). Registered before the SPA
# catch-all mount at EOF so API paths always win over the single-page app.
app.include_router(sessions.router)
app.include_router(groups.router)
app.include_router(recordings.router)
app.include_router(jobs.router)
app.include_router(models.router)
app.include_router(upload.router)
app.include_router(translate.router)
app.include_router(diarize.router)
app.include_router(chat.router)
app.include_router(ws.router)

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ---------- Basic HTTP endpoints ----------


# When the SPA hasn't been built yet (dev without `npm run build`), expose a
# small JSON banner at "/". Once built, the SPA mount at the bottom of this
# module owns "/" instead.
if not APP_DIR.exists():

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


# ---------- SPA (built frontend) ----------
# Mounted LAST so every API route and the /static mount above take precedence;
# only unmatched paths fall through to the single-page app. `html=True` serves
# index.html at "/" and for client-side routes.
if APP_DIR.exists():
    app.mount("/", StaticFiles(directory=str(APP_DIR), html=True), name="spa")
