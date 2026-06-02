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
import json
import logging
import os
import re
import sys
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import shutil
import tempfile

from fastapi import (
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from datetime import datetime, timezone

from .chat.base import ChatMessage
from .core.config import Config, load_config
from .core.diarize import diarize_session
from .core.jobs import JobRegistry, Phase, encode_sse
from .core.models import download_model, required_models
from .core.syscap import SystemAudioMixer
from .core.events import (
    ErrorEvent,
    ReadyEvent,
    TranscriptEvent,
    TranslationEvent,
    VADEvent,
)
from .core.pipeline import Pipeline, SessionParams
from .core.recording import WavWriter, resolve_recording_path
from .core.registry import make_chat, make_speaker, make_stt, make_translator, make_vad
from .core.store import Store
from .core.upload import (
    UPLOAD_PHASES_NO_TRANSLATE,
    UPLOAD_PHASES_TRANSLATE,
    process_upload,
)
from .speaker.base import SpeakerBackend

# Session IDs must be filesystem-safe; we accept UUIDs and slug-like strings
# only to avoid path-traversal via the recording endpoints.
_SAFE_SESSION_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")

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


async def _ensure_chat_loaded(app: FastAPI) -> None:
    """Idempotent lazy-load of the chat model. Serialized so concurrent
    first-requests don't try to load twice."""
    if app.state.chat_loaded:
        return
    async with app.state.chat_load_lock:
        if app.state.chat_loaded:
            return
        await app.state.chat_backend.load()
        app.state.chat_loaded = True


async def _ensure_diarize_loaded(app: FastAPI) -> SpeakerBackend:
    """Lazy-load the speaker embedding model for offline diarization."""
    if app.state.diarize_speaker is None:
        raise HTTPException(
            status_code=503, detail="speaker backend disabled in config"
        )
    if app.state.diarize_loaded:
        return app.state.diarize_speaker
    async with app.state.diarize_load_lock:
        if not app.state.diarize_loaded:
            await app.state.diarize_speaker.load()
            app.state.diarize_loaded = True
    return app.state.diarize_speaker


_CHAT_SYSTEM_TEMPLATE = (
    "You are an assistant helping the user understand and reason about a "
    "live conversation transcript. The transcript below is everything the "
    "session has captured so far. Be concise, cite times when useful, and "
    "answer in the same language the user writes to you in.{trunc_note}\n\n"
    "=== TRANSCRIPT ===\n{transcript}\n=== END TRANSCRIPT ==="
)

# Soft cap on the transcript portion (chars). The chat model is 32K-token
# native; reserving a chunk for system framing, chat history, and the
# generated response leaves ~80K chars of transcript headroom (CJK runs
# ~1.5 tokens/char, Latin ~0.25). Past this we keep the *tail* — the user
# is far likelier to ask about recent content than the opening minutes.
_MAX_TRANSCRIPT_CHARS = 80_000


def _build_transcript_snapshot(segments: list[dict[str, Any]]) -> tuple[str, bool]:
    """Return (snapshot, truncated). Most recent end of the transcript wins
    when we have to trim."""
    lines: list[str] = []
    for s in segments:
        text = (s.get("orig_text") or "").strip()
        if not text:
            continue
        t = s.get("started_at") or 0.0
        spk = s.get("speaker") or ""
        prefix = f"[{t:.1f}s{' ' + spk if spk else ''}]"
        lines.append(f"{prefix} {text}")
    if not lines:
        return "(no speech captured yet)", False

    full = "\n".join(lines)
    if len(full) <= _MAX_TRANSCRIPT_CHARS:
        return full, False

    # Drop oldest lines until we fit. Walk back from the end.
    kept: list[str] = []
    running = 0
    for line in reversed(lines):
        # +1 for the newline that will join them.
        if running + len(line) + 1 > _MAX_TRANSCRIPT_CHARS:
            break
        kept.append(line)
        running += len(line) + 1
    kept.reverse()
    return "\n".join(kept), True


async def _translate_one_for_segment(
    *,
    translator: Any,
    text: str,
    audio_lang: str | None,
    tgt_lang: str,
) -> tuple[str, str]:
    """Translate one segment's text. Returns (translated_text, status).

    Single source of truth for the per-segment translate step shared by
    the /translate endpoint and the diarize-retranslate phase. Status is
    "final" on a successful non-empty translation, "skipped" when the
    text is already in the target language or the translator returns
    nothing / errors.
    """
    from .core.pipeline import _text_lang_override

    src = _text_lang_override(text, audio_lang=audio_lang or "en", tgt_lang=tgt_lang)
    if src == tgt_lang:
        return ("", "skipped")
    try:
        translated = await translator.translate(text, src=src, tgt=tgt_lang)
    except Exception as e:
        log.exception(
            "translate failed: src=%s tgt=%s text=%r err=%r",
            src, tgt_lang, text[:80], e,
        )
        return ("", "skipped")
    translated = (translated or "").strip()
    if not translated:
        log.warning(
            "translator returned empty: src=%s tgt=%s text=%r",
            src, tgt_lang, text[:80],
        )
        return ("", "skipped")
    return (translated, "final")


def _has_real_translations(segments: list[dict[str, Any]]) -> bool:
    return any(
        (s.get("trans_status") == "final")
        and bool((s.get("trans_text") or "").strip())
        for s in segments
    )


def _translation_candidates(
    segments: list[dict[str, Any]],
    *,
    only_missing: bool,
) -> list[dict[str, Any]]:
    return [
        s for s in segments
        if (s.get("orig_text") or "").strip()
        and (
            not only_missing
            or not (s.get("trans_text") or "")
            or s.get("trans_status") == "skipped"
        )
    ]


async def _translate_segments_for_session(
    *,
    store: Store,
    session_id: str,
    session: dict[str, Any],
    segments: list[dict[str, Any]],
    translator: Any,
    tgt_lang: str,
    registry: JobRegistry,
    job_id: str,
) -> int:
    if not segments:
        registry.advance(job_id, phase_inner=1.0)
        return 0
    total = max(1, len(segments))
    done = 0
    for s in segments:
        text = (s.get("orig_text") or "").strip()
        if not text:
            continue
        audio_lang = s.get("orig_lang") or session.get("src_lang") or "en"
        if audio_lang == "auto":
            audio_lang = "en"
        translated, status = await _translate_one_for_segment(
            translator=translator,
            text=text,
            audio_lang=audio_lang,
            tgt_lang=tgt_lang,
        )
        await store.upsert_segment_trans(
            session_id=session_id,
            segment_id=s["segment_id"],
            ord_=s["ord"],
            trans_text=translated,
            trans_status=status,
            trans_lang=tgt_lang,
        )

        done += 1
        registry.advance(
            job_id,
            phase_inner=done / total,
            log_line=(
                f"Translated {done}/{total}"
                if done % 5 == 0 or done == total else None
            ),
        )
    return done


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


# ---------- Loopback auth ----------
# The desktop launcher sets WRENOTE_AUTH_TOKEN to a random per-launch secret.
# All local pages share the loopback interface and pass the WS origin check, so
# the token — handed to our own webview as a same-origin cookie when it loads
# the SPA — is what actually keeps other local pages out of the API/WebSocket.
# Unset (e.g. plain `uvicorn ...` in dev) => auth disabled, nothing changes.
AUTH_TOKEN = os.environ.get("WRENOTE_AUTH_TOKEN", "")
AUTH_COOKIE = "wrenote_token"

# Reachable without a token so the shell can bootstrap and pick up the cookie:
# the SPA entry, its assets, and the health probe.
_PUBLIC_PREFIXES = ("/assets", "/static")
_PUBLIC_PATHS = {"/", "/health", "/favicon.svg", "/icons.svg"}


def _token_from_request(request: Request) -> str | None:
    cookie = request.cookies.get(AUTH_COOKIE)
    if cookie:
        return cookie
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:]
    return request.query_params.get("token")


if AUTH_TOKEN:

    @app.middleware("http")
    async def loopback_auth(request: Request, call_next: Any) -> Any:
        path = request.url.path
        if (
            path not in _PUBLIC_PATHS
            and not path.startswith(_PUBLIC_PREFIXES)
            and _token_from_request(request) != AUTH_TOKEN
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


APP_DIR = STATIC_DIR / "app"  # built SPA (vite output); served at "/" at EOF.

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


# ---------- Session CRUD endpoints (SQLite-backed) ----------


@app.get("/sessions")
async def list_sessions(request: Request) -> dict[str, Any]:
    store: Store = request.app.state.store
    return {"sessions": await store.list_sessions()}


@app.get("/sessions/{session_id}")
async def get_session(session_id: str, request: Request) -> dict[str, Any]:
    sid = _safe_session_id(session_id)
    store: Store = request.app.state.store
    sess = await store.get_session(sid)
    if sess is None:
        raise HTTPException(status_code=404, detail="session not found")
    return sess


@app.patch("/sessions/{session_id}")
async def patch_session(
    session_id: str,
    request: Request,
) -> dict[str, str]:
    """Currently only supports renaming. Body: ``{"title": "..."}``."""
    sid = _safe_session_id(session_id)
    body = await request.json()
    title = body.get("title")
    if not isinstance(title, str) or not title.strip():
        raise HTTPException(status_code=400, detail="title required")
    store: Store = request.app.state.store
    await store.update_session_title(sid, title.strip())
    return {"status": "ok"}


@app.delete("/sessions/{session_id}")
async def delete_session(session_id: str, request: Request) -> dict[str, str]:
    """Delete the session row (cascades to segments) AND the WAV file."""
    sid = _safe_session_id(session_id)
    store: Store = request.app.state.store
    existed = await store.delete_session(sid)
    # Always try to remove the WAV — file may exist without a DB row if
    # a previous run died mid-session.
    wav = resolve_recording_path(sid)
    if wav.exists():
        try:
            wav.unlink()
        except Exception:
            log.exception("failed to remove recording %s", wav)
    return {"status": "ok" if existed else "not_found"}


# ---------- Session groups (sidebar folders) ----------


@app.get("/groups")
async def list_groups(request: Request) -> dict[str, Any]:
    store: Store = request.app.state.store
    return {"groups": await store.list_groups()}


@app.post("/groups")
async def create_group(request: Request) -> dict[str, Any]:
    store: Store = request.app.state.store
    try:
        body = await request.json()
    except Exception:
        body = {}
    name = (body.get("name") or "New group").strip() if isinstance(body, dict) else "New group"
    existing = await store.list_groups()
    gid = uuid.uuid4().hex
    now = datetime.now(timezone.utc).isoformat()
    await store.create_group(
        group_id=gid, name=name or "New group", created_at=now, position=len(existing)
    )
    return {"group": {"id": gid, "name": name or "New group", "created_at": now, "position": len(existing)}}


@app.patch("/groups/{group_id}")
async def rename_group(group_id: str, request: Request) -> dict[str, str]:
    gid = _safe_session_id(group_id)
    body = await request.json()
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name required")
    store: Store = request.app.state.store
    await store.rename_group(gid, name)
    return {"status": "ok"}


@app.delete("/groups/{group_id}")
async def delete_group(group_id: str, request: Request) -> dict[str, str]:
    gid = _safe_session_id(group_id)
    store: Store = request.app.state.store
    existed = await store.delete_group(gid)
    return {"status": "ok" if existed else "not_found"}


@app.patch("/sessions/{session_id}/group")
async def set_session_group(session_id: str, request: Request) -> dict[str, str]:
    """Body: ``{"groupId": "<id>"|null}``. Move a session into a group (or out
    of all groups when null)."""
    sid = _safe_session_id(session_id)
    body = await request.json()
    group_id = body.get("groupId")
    if group_id is not None and not isinstance(group_id, str):
        raise HTTPException(status_code=400, detail="groupId must be a string or null")
    gid = _safe_session_id(group_id) if group_id else None
    store: Store = request.app.state.store
    await store.set_session_group(sid, gid)
    return {"status": "ok"}


# ---------- Job system (async background work) ----------


@app.get("/jobs/{job_id}")
async def get_job(job_id: str, request: Request) -> dict[str, Any]:
    registry: JobRegistry = request.app.state.jobs
    job = registry.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return job.snapshot()


@app.get("/jobs/{job_id}/stream")
async def stream_job(job_id: str, request: Request) -> StreamingResponse:
    """SSE stream of job snapshots. Closes when the job is done/error."""
    registry: JobRegistry = request.app.state.jobs
    if registry.get(job_id) is None:
        raise HTTPException(status_code=404, detail="job not found")

    async def gen() -> AsyncIterator[bytes]:
        async for snap in registry.subscribe(job_id):
            yield encode_sse(snap)
            if snap.get("status") != "running":
                # One terminal frame is enough; bail.
                return

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable nginx buffering if anyone proxies
        },
    )


# ---------- First-run model download ----------


@app.get("/api/models/status")
async def models_status(request: Request) -> dict[str, Any]:
    """Which required models are present in ~/.wrenote/models/, and their sizes."""
    cfg: Config = request.app.state.config
    entries = required_models(cfg)
    return {
        "models": [e.status_dict() for e in entries],
        "all_present": all(e.present for e in entries),
    }


@app.post("/api/models/download")
async def models_download(request: Request) -> dict[str, Any]:
    """Start downloading any missing models as a background job. Progress streams
    over ``/jobs/{job_id}/stream`` (one weighted phase per model)."""
    cfg: Config = request.app.state.config
    missing = [e for e in required_models(cfg) if not e.present]
    if not missing:
        return {"job_id": None, "all_present": True}

    registry: JobRegistry = request.app.state.jobs
    total = sum(e.approx_size for e in missing) or 1
    phases = [Phase(name=e.filename, weight=e.approx_size / total) for e in missing]
    job = registry.create(kind="model_download", phases=phases)

    async def runner() -> None:
        try:
            for idx, entry in enumerate(missing):
                registry.advance(
                    job.id, phase_idx=idx, phase_inner=0.0,
                    log_line=f"Downloading {entry.filename}",
                )

                def _progress(frac: float, status: str) -> None:
                    registry.advance(job.id, phase_inner=frac, log_line=status)

                await download_model(entry, _progress)
            registry.complete(job.id, result={"downloaded": [e.filename for e in missing]})
        except Exception as ex:  # noqa: BLE001 — surfaced to the client via the job
            log.exception("model download failed")
            registry.fail(job.id, str(ex))

    asyncio.create_task(runner())
    return {"job_id": job.id, "all_present": False}


# ---------- Upload (batch transcribe from files) — async job ----------


@app.post("/sessions/upload")
async def upload_session(
    request: Request,
    files: list[UploadFile] = File(...),
    title: str = Form("Untitled session"),
    src_lang: str = Form("auto"),
    tgt_lang: str = Form("zh"),
    translate: bool = Form(True),
) -> dict[str, str]:
    """Kicks off a background job; returns immediately.

    Returns ``{job_id, session_id}``. Client subscribes to
    ``/jobs/{job_id}/stream`` for progress + final result.
    """
    if not files:
        raise HTTPException(status_code=400, detail="no files")

    cfg: Config = request.app.state.config
    if cfg.stt.backend != "whisper_cpp":
        raise HTTPException(
            status_code=400,
            detail="upload only supports the whisper_cpp STT backend currently",
        )
    whisper_model_path = str(cfg.stt.params.get("model_path") or "")
    if not whisper_model_path:
        raise HTTPException(status_code=500, detail="stt.model_path not configured")

    tmpdir = Path(tempfile.mkdtemp(prefix="wrenote-upload-"))
    saved_paths: list[Path] = []
    for upload in files:
        safe_name = Path(upload.filename or "audio.bin").name
        dest = tmpdir / safe_name
        with dest.open("wb") as f:
            while chunk := await upload.read(1 << 20):
                f.write(chunk)
        saved_paths.append(dest)

    sid = uuid.uuid4().hex
    store: Store = request.app.state.store
    registry: JobRegistry = request.app.state.jobs

    phases = UPLOAD_PHASES_TRANSLATE if translate else UPLOAD_PHASES_NO_TRANSLATE
    job = registry.create(kind="upload", phases=list(phases))

    async def runner() -> None:
        translator = (
            make_translator(cfg.translator.backend, cfg.translator.params)
            if translate else None
        )
        try:
            if translator is not None:
                registry.advance(job.id, log_line="Loading translator")
                await translator.load()
            await process_upload(
                job_id=job.id,
                registry=registry,
                session_id=sid,
                title=title,
                src_lang=src_lang,
                tgt_lang=tgt_lang,
                translate=translate,
                file_paths=saved_paths,
                whisper_model_path=whisper_model_path,
                translator=translator,
                store=store,
            )
            registry.complete(
                job.id,
                result={"session_id": sid},
            )
        except Exception as e:
            log.exception("upload job %s failed", job.id)
            registry.fail(job.id, f"{type(e).__name__}: {e}")
        finally:
            if translator is not None:
                try:
                    await translator.unload()
                except Exception:
                    pass
            shutil.rmtree(tmpdir, ignore_errors=True)

    asyncio.create_task(runner(), name=f"upload-{job.id[:8]}")
    return {"job_id": job.id, "session_id": sid}


# ---------- Speaker diarization endpoints (per-session) ----------


_DIARIZE_PHASES = [
    Phase("load_model", 0.05),
    Phase("embed_and_cluster", 0.90),
    Phase("persist", 0.05),
]

_DIARIZE_RETRANSLATE_PHASES = [
    Phase("load_model", 0.05),
    Phase("embed_and_cluster", 0.60),
    Phase("persist", 0.05),
    Phase("load_translator", 0.05),
    Phase("translate", 0.25),
]

_TRANSLATE_PHASES = [
    Phase("load_translator", 0.05),
    Phase("translate", 0.95),
]


@app.post("/sessions/{session_id}/translate")
async def translate_session(session_id: str, request: Request) -> dict[str, str]:
    """Retroactively translate any segments missing a translation.

    Useful for STT-only sessions: user records without translation, then
    later wants the translated version. Runs as a job; subscribe to
    ``/jobs/{job_id}/stream``. Body (optional): ``{"tgt_lang": "..."}``
    overrides the session's target lang.
    """
    sid = _safe_session_id(session_id)
    body: dict[str, Any] = {}
    raw = await request.body()
    if raw:
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            pass

    store: Store = request.app.state.store
    session = await store.get_session(sid)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")

    tgt_lang = str(body.get("tgt_lang") or session["tgt_lang"] or "zh")
    # retranslate=True re-does every segment (replacing existing translations);
    # the default only fills in segments that are missing a translation.
    retranslate = bool(body.get("retranslate"))
    cfg: Config = request.app.state.config
    registry: JobRegistry = request.app.state.jobs
    job = registry.create(kind="translate", phases=list(_TRANSLATE_PHASES))

    async def runner() -> None:
        translator = make_translator(cfg.translator.backend, cfg.translator.params)
        try:
            registry.advance(job.id, phase_idx=0, log_line="Loading translator")
            await translator.load()
            registry.advance(job.id, phase_inner=1.0)
            registry.advance(job.id, phase_idx=1, log_line="Translating")

            segs = session["segments"]
            # Default: only segments missing a real translation. When the
            # caller passes retranslate=True, every segment is a candidate and
            # existing translations get replaced.
            candidates = _translation_candidates(segs, only_missing=not retranslate)
            done = await _translate_segments_for_session(
                store=store,
                session_id=sid,
                session=session,
                segments=candidates,
                translator=translator,
                tgt_lang=tgt_lang,
                registry=registry,
                job_id=job.id,
            )

            await store.update_session_duration(sid, session.get("duration_s", 0.0))
            registry.complete(
                job.id,
                result={"session_id": sid, "tgt_lang": tgt_lang, "translated": done},
            )
        except Exception as e:
            log.exception("translate job %s failed", job.id)
            registry.fail(job.id, f"{type(e).__name__}: {e}")
        finally:
            try:
                await translator.unload()
            except Exception:
                pass

    asyncio.create_task(runner(), name=f"translate-{job.id[:8]}")
    return {"job_id": job.id}


@app.post("/sessions/{session_id}/diarize")
async def diarize_endpoint(session_id: str, request: Request) -> dict[str, str]:
    """Kick off offline diarization as a job. Returns ``{job_id}``
    immediately; subscribe to ``/jobs/{job_id}/stream`` for progress."""
    sid = _safe_session_id(session_id)
    store: Store = request.app.state.store
    session = await store.get_session(sid)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    wav = resolve_recording_path(sid)
    if not wav.exists():
        raise HTTPException(
            status_code=400,
            detail="no recording on file for this session",
        )
    segments = session.get("segments", [])
    should_retranslate = _has_real_translations(segments)

    registry: JobRegistry = request.app.state.jobs
    job = registry.create(
        kind="diarize",
        phases=list(
            _DIARIZE_RETRANSLATE_PHASES
            if should_retranslate
            else _DIARIZE_PHASES
        ),
    )

    async def runner() -> None:
        translator: Any | None = None
        try:
            if not segments:
                registry.complete(
                    job.id, result={"session_id": sid, "n_speakers": 0, "labels": {}}
                )
                return

            # ---- Phase 0: load speaker model ----
            registry.advance(job.id, phase_idx=0, phase_inner=0.0,
                             log_line="Loading speaker model")
            speaker = await _ensure_diarize_loaded(request.app)
            registry.advance(job.id, phase_inner=1.0)

            # ---- Phase 1: embed + cluster ----
            registry.advance(job.id, phase_idx=1, phase_inner=0.0,
                             log_line="Embedding segments")
            def on_progress(frac: float, log_line: str | None = None) -> None:
                registry.advance(job.id, phase_inner=frac, log_line=log_line)
            result = await diarize_session(
                wav_path=wav,
                segments=segments,
                speaker=speaker,
                on_progress=on_progress,
            )
            labels = result.labels
            resegmented = result.segments

            # ---- Phase 2: persist speaker-aware segments ----
            registry.advance(job.id, phase_idx=2, phase_inner=0.0,
                             log_line="Writing speaker-aware segments")
            if resegmented:
                await store.replace_segments(sid, resegmented)
            elif labels:
                await store.set_segment_speakers(sid, labels)
            registry.advance(job.id, phase_inner=1.0)

            translated = 0
            if should_retranslate and resegmented:
                tgt_lang = str(session.get("tgt_lang") or "zh")
                cfg: Config = request.app.state.config

                # ---- Phase 3: load translator only when the old transcript
                # had real translations. STT-only sessions stay diarize-only.
                registry.advance(
                    job.id,
                    phase_idx=3,
                    phase_inner=0.0,
                    log_line="Loading translator for resegmented transcript",
                )
                translator = make_translator(
                    cfg.translator.backend,
                    cfg.translator.params,
                )
                await translator.load()
                registry.advance(job.id, phase_inner=1.0)

                # ---- Phase 4: translate every new row because old
                # translations no longer align after boundaries/text changed.
                registry.advance(
                    job.id,
                    phase_idx=4,
                    phase_inner=0.0,
                    log_line="Retranslating speaker-aware segments",
                )
                candidates = _translation_candidates(
                    resegmented,
                    only_missing=False,
                )
                translated = await _translate_segments_for_session(
                    store=store,
                    session_id=sid,
                    session=session,
                    segments=candidates,
                    translator=translator,
                    tgt_lang=tgt_lang,
                    registry=registry,
                    job_id=job.id,
                )

            distinct = {v for v in labels.values() if v.startswith("Speaker ")}
            registry.complete(
                job.id,
                result={
                    "session_id": sid,
                    "n_speakers": len(distinct),
                    "n_labeled": len(labels),
                    "n_segments": len(resegmented) if resegmented else len(segments),
                    "resegmented": bool(resegmented),
                    "retranslated": bool(should_retranslate and resegmented),
                    "translated": translated,
                    "labels": labels,
                },
            )
        except Exception as e:
            log.exception("diarize job %s failed", job.id)
            registry.fail(job.id, f"{type(e).__name__}: {e}")
        finally:
            if translator is not None:
                try:
                    await translator.unload()
                except Exception:
                    pass

    asyncio.create_task(runner(), name=f"diarize-{job.id[:8]}")
    return {"job_id": job.id}


@app.patch("/sessions/{session_id}/speakers")
async def rename_speaker(session_id: str, request: Request) -> dict[str, int]:
    """Body: ``{"from": "Speaker 1", "to": "Alice"}``. Renames every segment
    whose ``speaker`` matches ``from`` to ``to``. Returns count updated."""
    sid = _safe_session_id(session_id)
    body = await request.json()
    old = (body.get("from") or "").strip()
    new = (body.get("to") or "").strip()
    if not old or not new:
        raise HTTPException(status_code=400, detail="from and to are required")
    if old == new:
        return {"updated": 0}
    store: Store = request.app.state.store
    n = await store.rename_speaker(sid, old, new)
    return {"updated": n}


@app.post("/sessions/{session_id}/segments/speaker")
async def assign_segment_speaker(session_id: str, request: Request) -> dict[str, int]:
    """Body: ``{"segmentIds": [...], "speaker": "Alice"}``. Assigns a speaker
    to specific segments — used to label segments diarization left
    unidentified, without cascading across the whole session."""
    sid = _safe_session_id(session_id)
    body = await request.json()
    seg_ids = body.get("segmentIds") or []
    speaker = (body.get("speaker") or "").strip()
    if not isinstance(seg_ids, list) or not seg_ids or not speaker:
        raise HTTPException(
            status_code=400, detail="segmentIds and speaker are required"
        )
    store: Store = request.app.state.store
    labels = {str(s): speaker for s in seg_ids}
    n = await store.set_segment_speakers(sid, labels)
    return {"updated": n}


# ---------- Chat conversations + messages (per-session threads) ----------


def _safe_conversation_id(conversation_id: str) -> str:
    if not _SAFE_SESSION_ID.match(conversation_id):
        raise HTTPException(status_code=400, detail="invalid conversation id")
    return conversation_id


async def _require_conversation(store: Store, sid: str, cid: str) -> dict[str, Any]:
    """Fetch a conversation, 404-ing unless it belongs to this session."""
    conv = await store.get_conversation(cid)
    if conv is None or conv["session_id"] != sid:
        raise HTTPException(status_code=404, detail="conversation not found")
    return conv


@app.get("/sessions/{session_id}/conversations")
async def list_conversations(session_id: str, request: Request) -> dict[str, Any]:
    sid = _safe_session_id(session_id)
    store: Store = request.app.state.store
    return {"conversations": await store.list_conversations(sid)}


@app.post("/sessions/{session_id}/conversations")
async def create_conversation(
    session_id: str, request: Request
) -> dict[str, Any]:
    sid = _safe_session_id(session_id)
    store: Store = request.app.state.store
    if await store.get_session(sid) is None:
        raise HTTPException(status_code=404, detail="session not found")
    try:
        body = await request.json()
    except Exception:
        body = {}
    title = (body.get("title") or "").strip() if isinstance(body, dict) else ""
    conv_id = uuid.uuid4().hex
    now = datetime.now(timezone.utc).isoformat()
    await store.create_conversation(
        conversation_id=conv_id, session_id=sid, title=title, created_at=now,
    )
    conv = await store.get_conversation(conv_id)
    return {"conversation": {**(conv or {}), "message_count": 0}}


@app.patch("/sessions/{session_id}/conversations/{conversation_id}")
async def rename_conversation(
    session_id: str, conversation_id: str, request: Request
) -> dict[str, str]:
    sid = _safe_session_id(session_id)
    cid = _safe_conversation_id(conversation_id)
    store: Store = request.app.state.store
    await _require_conversation(store, sid, cid)
    body = await request.json()
    title = (body.get("title") or "").strip()
    await store.rename_conversation(cid, title)
    return {"status": "ok"}


@app.delete("/sessions/{session_id}/conversations/{conversation_id}")
async def delete_conversation(
    session_id: str, conversation_id: str, request: Request
) -> dict[str, str]:
    sid = _safe_session_id(session_id)
    cid = _safe_conversation_id(conversation_id)
    store: Store = request.app.state.store
    await _require_conversation(store, sid, cid)
    await store.delete_conversation(cid)
    return {"status": "ok"}


@app.get("/sessions/{session_id}/conversations/{conversation_id}/chat")
async def list_conversation_chat(
    session_id: str, conversation_id: str, request: Request
) -> dict[str, Any]:
    sid = _safe_session_id(session_id)
    cid = _safe_conversation_id(conversation_id)
    store: Store = request.app.state.store
    await _require_conversation(store, sid, cid)
    return {"messages": await store.list_chat_messages(cid)}


@app.delete("/sessions/{session_id}/conversations/{conversation_id}/chat")
async def clear_conversation_chat(
    session_id: str, conversation_id: str, request: Request
) -> dict[str, str]:
    sid = _safe_session_id(session_id)
    cid = _safe_conversation_id(conversation_id)
    store: Store = request.app.state.store
    await _require_conversation(store, sid, cid)
    await store.clear_chat(cid)
    return {"status": "ok"}


@app.post("/sessions/{session_id}/conversations/{conversation_id}/chat")
async def post_conversation_chat(
    session_id: str, conversation_id: str, request: Request
) -> StreamingResponse:
    """Stream the assistant reply for the user's next message in a thread.

    Body: ``{"text": "..."}``. Response: ``text/plain`` chunks. The server
    snapshots the session transcript at request time, prepends it as a
    system message, appends this conversation's prior history, then the new
    user message. Both user and assistant messages are persisted (user
    up-front, assistant after the stream completes), and the conversation's
    ``updated_at`` is bumped so it floats to the top of the thread list.
    """
    sid = _safe_session_id(session_id)
    cid = _safe_conversation_id(conversation_id)
    body = await request.json()
    text = (body.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text required")

    store: Store = request.app.state.store
    session = await store.get_session(sid)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    conv = await _require_conversation(store, sid, cid)

    transcript, truncated = _build_transcript_snapshot(session.get("segments", []))
    history_rows = await store.list_chat_messages(cid)

    # Lazy-load the model on first chat in this server's lifetime.
    await _ensure_chat_loaded(request.app)
    backend = request.app.state.chat_backend

    trunc_note = (
        "\n\nNote: only the most recent portion of the transcript is shown "
        "below — earlier content was trimmed to fit the context window. "
        "If the user asks about something not present, say so."
        if truncated else ""
    )
    system_msg = ChatMessage(
        role="system",
        content=_CHAT_SYSTEM_TEMPLATE.format(
            transcript=transcript, trunc_note=trunc_note,
        ),
    )
    history = [ChatMessage(role=r["role"], content=r["content"]) for r in history_rows]
    user_msg = ChatMessage(role="user", content=text)
    messages = [system_msg, *history, user_msg]

    # Persist the user turn up-front so a dropped connection still leaves
    # the question visible next time the panel loads.
    now = datetime.now(timezone.utc).isoformat()
    await store.append_chat_message(
        conversation_id=cid, role="user", content=text, created_at=now,
    )
    await store.touch_conversation(cid, now)
    # Give an untitled thread a label from its first user message.
    if not (conv.get("title") or "").strip():
        derived = text.strip().splitlines()[0][:48]
        await store.rename_conversation(cid, derived)

    async def stream() -> Any:
        accumulated: list[str] = []
        try:
            chunks = await backend.chat(messages)
            async for piece in chunks:
                accumulated.append(piece)
                yield piece
        except Exception as e:
            log.exception("chat stream errored")
            err = f"\n\n[ERROR] {type(e).__name__}: {e}"
            accumulated.append(err)
            yield err
        finally:
            full = "".join(accumulated)
            if full.strip():
                try:
                    ts = datetime.now(timezone.utc).isoformat()
                    await store.append_chat_message(
                        conversation_id=cid,
                        role="assistant",
                        content=full,
                        created_at=ts,
                    )
                    await store.touch_conversation(cid, ts)
                except Exception:
                    log.exception("failed to persist assistant message")

    return StreamingResponse(stream(), media_type="text/plain; charset=utf-8")


# ---------- Session title suggestion (LLM) ----------

_TITLE_SYSTEM = (
    "You write a short, specific title for a transcript — 3 to 6 words, in the "
    "transcript's own language. No quotes, no trailing punctuation, no prefix "
    "like 'Title:'. Reply with the title only."
)


@app.post("/sessions/{session_id}/title/suggest")
async def suggest_title(session_id: str, request: Request) -> dict[str, str]:
    """Summarize a concise title for the session from its transcript using the
    chat model, persist it, and return it. Best-effort: if there's nothing to
    summarize the existing title is returned unchanged."""
    sid = _safe_session_id(session_id)
    store: Store = request.app.state.store
    session = await store.get_session(sid)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")

    transcript, _ = _build_transcript_snapshot(session.get("segments", []))
    current = session.get("title", "")
    if not transcript.strip():
        return {"title": current}

    await _ensure_chat_loaded(request.app)
    backend = request.app.state.chat_backend
    messages = [
        ChatMessage(role="system", content=_TITLE_SYSTEM),
        ChatMessage(
            role="user",
            content=f"Transcript:\n\n{transcript}\n\nTitle:",
        ),
    ]
    try:
        parts: list[str] = []
        chunks = await backend.chat(messages)
        async for piece in chunks:
            parts.append(piece)
        raw = "".join(parts).strip().strip('"').strip("'")
        title = raw.splitlines()[0].strip()[:80] if raw else ""
    except Exception:
        log.exception("title suggestion failed")
        title = ""

    if title:
        await store.update_session_title(sid, title)
        return {"title": title}
    return {"title": current}


# ---------- Recording file endpoints (per-session WAV) ----------


def _safe_session_id(session_id: str) -> str:
    """Reject anything that could escape the recordings dir."""
    if not _SAFE_SESSION_ID.match(session_id):
        raise HTTPException(status_code=400, detail="invalid session id")
    return session_id


@app.get("/recordings/{session_id}.wav")
async def get_recording(session_id: str) -> FileResponse:
    sid = _safe_session_id(session_id)
    path = resolve_recording_path(sid)
    if not path.exists():
        raise HTTPException(status_code=404, detail="recording not found")
    return FileResponse(
        path=str(path),
        media_type="audio/wav",
        filename=f"{sid}.wav",
    )


@app.delete("/recordings/{session_id}.wav")
async def delete_recording(session_id: str) -> dict[str, str]:
    sid = _safe_session_id(session_id)
    path = resolve_recording_path(sid)
    if path.exists():
        try:
            path.unlink()
        except Exception:
            log.exception("failed to delete recording %s", path)
            raise HTTPException(status_code=500, detail="delete failed")
    return {"status": "ok"}


# ---------- Origin validation (per design.v1.1 §5.3) ----------


def _origin_allowed(origin: str | None) -> bool:
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


# ---------- WebSocket session ----------


async def _send_event(ws: WebSocket, event: Any) -> None:
    """Send a Pydantic event over the WebSocket as JSON."""
    await ws.send_text(event.model_dump_json())


async def _send_error(
    ws: WebSocket, code: str, msg: str, recoverable: bool = True
) -> None:
    try:
        await _send_event(ws, ErrorEvent(code=code, msg=msg, recoverable=recoverable))
    except Exception:
        log.exception("Failed to send error to client")


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    origin = ws.headers.get("origin")
    if not _origin_allowed(origin):
        log.warning("Rejecting WS connection from origin=%r", origin)
        await ws.close(code=status.WS_1008_POLICY_VIOLATION, reason="origin not allowed")
        return

    if AUTH_TOKEN:
        provided = ws.cookies.get(AUTH_COOKIE) or ws.query_params.get("token")
        if provided != AUTH_TOKEN:
            log.warning("Rejecting WS connection: missing/invalid token")
            await ws.close(code=status.WS_1008_POLICY_VIOLATION, reason="unauthorized")
            return

    await ws.accept()
    log.info("WS connected (client=%s, origin=%s)", ws.client, origin)

    cfg: Config = ws.app.state.config
    pipeline: Pipeline | None = None
    pump_task: asyncio.Task[None] | None = None
    mixer: SystemAudioMixer | None = None
    wav_writer: WavWriter | None = None
    # Set in the start-config parse; finally needs them in scope.
    session_id: str | None = None
    max_ended_at: float = 0.0

    try:
        # First message must be 'start'
        try:
            first_text = await ws.receive_text()
        except WebSocketDisconnect:
            return

        try:
            first = json.loads(first_text)
        except json.JSONDecodeError as e:
            await _send_error(ws, "BAD_CONFIG", f"Invalid JSON: {e}", recoverable=False)
            return

        if first.get("type") != "start":
            await _send_error(
                ws, "BAD_CONFIG",
                f"First message must be 'start', got {first.get('type')!r}",
                recoverable=False,
            )
            return

        session_cfg = first.get("config") or {}
        # Session id is client-generated (frontend uses it as primary key in
        # localStorage). Fall back to a server-side UUID if absent. Sanitized
        # so it can be used as a filename for the per-session WAV.
        raw_sid = str(session_cfg.get("session_id") or uuid.uuid4())
        session_id = raw_sid if _SAFE_SESSION_ID.match(raw_sid) else uuid.uuid4().hex
        src_lang = session_cfg.get("src", cfg.session.default_src_lang)
        tgt_lang = session_cfg.get("tgt", cfg.session.default_tgt_lang)
        capture_system = bool(session_cfg.get("capture_system"))
        min_silence_ms = int(session_cfg.get("min_silence_ms", 800))
        max_segment_ms = int(session_cfg.get("max_segment_ms", 25000))
        partial_interval_ms = int(session_cfg.get("partial_interval_ms", 800))
        partial_min_audio_ms = int(session_cfg.get("partial_min_audio_ms", 500))
        translate_partials = bool(session_cfg.get("translate_partials", True))
        translate_enabled = bool(session_cfg.get("translate_enabled", True))
        extended_silence_factor = float(session_cfg.get("extended_silence_factor", 2.25))
        speaker_enabled = bool(session_cfg.get("speaker_enabled", True))
        speaker_threshold = float(session_cfg.get("speaker_threshold", 0.65))
        speaker_min_audio_ms = int(session_cfg.get("speaker_min_audio_ms", 1000))

        # Build backends per connection (P1-a; share via app.state in a later pass)
        try:
            stt = make_stt(cfg.stt.backend, cfg.stt.params)
            vad = make_vad(cfg.vad.backend, cfg.vad.params)
            translator = make_translator(cfg.translator.backend, cfg.translator.params)
            speaker = None
            if speaker_enabled and cfg.speaker.backend not in (None, "", "disabled"):
                speaker = make_speaker(cfg.speaker.backend, cfg.speaker.params)
        except ValueError as e:
            await _send_error(ws, "BAD_CONFIG", str(e), recoverable=False)
            return

        pipeline = Pipeline(
            stt=stt,
            vad=vad,
            translator=translator,
            speaker=speaker,
            params=SessionParams(
                src_lang=src_lang,
                tgt_lang=tgt_lang,
                min_silence_ms=min_silence_ms,
                max_segment_ms=max_segment_ms,
                partial_interval_ms=partial_interval_ms,
                partial_min_audio_ms=partial_min_audio_ms,
                translate_partials=translate_partials,
                translate_enabled=translate_enabled,
                extended_silence_factor=extended_silence_factor,
                speaker_threshold=speaker_threshold,
                speaker_min_audio_ms=speaker_min_audio_ms,
                speaker_enabled=speaker_enabled,
            ),
        )

        try:
            await pipeline.start()
        except FileNotFoundError as e:
            await _send_error(ws, "MODEL_NOT_FOUND", str(e), recoverable=False)
            return
        except Exception as e:
            log.exception("Pipeline start failed")
            await _send_error(ws, "MODEL_LOAD_FAILED", f"{type(e).__name__}: {e}", recoverable=False)
            return

        # System-audio capture (meeting recording): mix the system output into
        # the mic stream. Falls back to mic-only if the helper/permission isn't
        # available, so recording still works.
        if capture_system:
            mixer = SystemAudioMixer()
            if not await mixer.start():
                mixer = None

        # Open the raw-audio WAV file for this session. Failures are
        # non-fatal: we log and continue without recording (translation
        # still works). The writer streams to disk; memory cost is O(1).
        try:
            wav_writer = WavWriter(session_id)
        except Exception:
            log.exception("Failed to open WAV writer for session %s — continuing without recording", session_id)
            wav_writer = None

        # Create / refresh the SQLite session row up-front so a crash
        # mid-recording still leaves *something* discoverable.
        store: Store = ws.app.state.store
        session_title = str(session_cfg.get("title") or "Untitled session")
        created_at = str(
            session_cfg.get("created_at")
            or datetime.now(timezone.utc).isoformat()
        )
        try:
            await store.upsert_session(
                session_id=session_id,
                title=session_title,
                created_at=created_at,
                src_lang=src_lang,
                tgt_lang=tgt_lang,
            )
        except Exception:
            log.exception("upsert_session failed for %s", session_id)

        # Send ready event
        await _send_event(
            ws,
            ReadyEvent(
                stt=stt.info,
                vad=vad.info,
                translator=translator.info,
                speaker=speaker.info if speaker is not None else None,
            ),
        )

        # Pump pipeline events → WebSocket + persist to SQLite as they fly by.
        # Segment ordinals come from a local counter; SQLite would do this
        # too but we want a stable insertion order regardless of clock skew.
        segment_ord: dict[str, int] = {}
        max_ended_at: float = 0.0

        async def _persist_event(event: Any) -> None:
            nonlocal max_ended_at
            try:
                if isinstance(event, TranscriptEvent):
                    sid = event.segment_id
                    if sid not in segment_ord:
                        segment_ord[sid] = len(segment_ord)
                    if event.t1 is not None and event.t1 > max_ended_at:
                        max_ended_at = event.t1
                    # Only the *orig* half of the row — preserves any
                    # translation already persisted for this segment.
                    await store.upsert_segment_orig(
                        session_id=session_id,
                        segment_id=sid,
                        ord_=segment_ord[sid],
                        started_at=event.t0 or 0.0,
                        ended_at=event.t1 or 0.0,
                        orig_text=event.text or "",
                        orig_status=event.type,
                        orig_lang=event.lang,
                        speaker=event.speaker,
                    )
                elif isinstance(event, TranslationEvent):
                    sid = event.segment_id
                    if sid not in segment_ord:
                        # Translation may arrive for a segment we haven't
                        # seen a TranscriptEvent for yet — preserve order.
                        segment_ord[sid] = len(segment_ord)
                    status = (
                        "skipped" if event.skipped
                        else "partial" if event.partial
                        else "final"
                    )
                    # Only the *trans* half — never touches orig_text /
                    # started_at / ended_at written by the transcript path.
                    await store.upsert_segment_trans(
                        session_id=session_id,
                        segment_id=sid,
                        ord_=segment_ord[sid],
                        trans_text=event.text or "",
                        trans_status=status,
                        trans_lang=event.tgt_lang,
                        speaker=event.speaker,
                    )
                elif isinstance(event, VADEvent) and event.type == "speech_end":
                    if event.ts > max_ended_at:
                        max_ended_at = event.ts
            except Exception:
                log.exception("persist failed for %s", type(event).__name__)

        async def event_pump() -> None:
            try:
                async for event in pipeline.client_event_stream():
                    # Persist before sending so a slow DB doesn't drop events,
                    # and so the client can immediately re-fetch from /sessions.
                    await _persist_event(event)
                    await _send_event(ws, event)
            except (WebSocketDisconnect, asyncio.CancelledError):
                pass
            except Exception:
                log.exception("event_pump crashed")

        pump_task = asyncio.create_task(event_pump(), name="ws-event-pump")

        # Main receive loop
        while True:
            try:
                msg = await ws.receive()
            except WebSocketDisconnect:
                break

            if msg.get("type") == "websocket.disconnect":
                break

            payload_bytes = msg.get("bytes")
            payload_text = msg.get("text")

            if payload_bytes:
                # P1 audio contract: 16kHz mono int16 PCM. Tee to both the
                # live pipeline and the per-session WAV file. Paused chunks
                # never arrive here (frontend gates them) so the WAV
                # naturally excludes silence-from-pause.
                if mixer is not None:
                    payload_bytes = await mixer.mix(payload_bytes)
                await pipeline.feed_audio(payload_bytes)
                if wav_writer is not None:
                    wav_writer.append(payload_bytes)
                continue

            if not payload_text:
                continue

            try:
                data = json.loads(payload_text)
            except json.JSONDecodeError:
                await _send_error(ws, "BAD_CONFIG", "Invalid JSON in control message")
                continue

            msg_type = data.get("type")
            if msg_type == "stop":
                log.info("WS received 'stop' — flushing pipeline before close")
                # Graceful drain: flush any in-flight segment, wait for
                # STT/translation to complete, give pump a moment to send.
                if pipeline is not None:
                    try:
                        await pipeline.flush()
                    except Exception:
                        log.exception("flush failed during stop")
                # Brief pause so the pump can ship the final TranslationEvent.
                await asyncio.sleep(0.5)
                break
            elif msg_type == "pause":
                # Frontend already stopped feeding PCM. Tell the pipeline to
                # flush any in-flight VAD segment now so the user sees their
                # pre-pause speech finalized instead of left hanging as a partial.
                log.info("WS received 'pause'")
                if pipeline is not None:
                    try:
                        await pipeline.close_open_segment()
                    except Exception:
                        log.exception("close_open_segment failed during pause")
            elif msg_type == "resume":
                log.info("WS received 'resume'")
            elif msg_type == "switch_lang":
                # P1: log and ignore (UI doesn't expose). Architecture supports it later.
                log.info("WS switch_lang requested but not implemented: %s", data)
            else:
                log.warning("WS ignoring unknown control message: %s", data)

    except Exception:
        log.exception("WebSocket handler crashed")
        try:
            await _send_error(ws, "STT_FAILED", "internal error", recoverable=False)
        except Exception:
            pass
    finally:
        if mixer is not None:
            try:
                await mixer.stop()
            except Exception:
                log.exception("syscap stop failed during cleanup")
        if pump_task is not None:
            pump_task.cancel()
            try:
                await pump_task
            except (asyncio.CancelledError, Exception):
                pass
        if pipeline is not None:
            try:
                await pipeline.stop()
            except Exception:
                log.exception("Pipeline stop failed during cleanup")
        if wav_writer is not None:
            try:
                wav_writer.close()
            except Exception:
                log.exception("WAV writer close failed during cleanup")
        # Stamp the final session duration so the past-session list shows
        # the right "X minutes ago" — derived from the last speech_end.
        try:
            store_ref: Store | None = getattr(ws.app.state, "store", None)
            if store_ref is not None and max_ended_at > 0:
                await store_ref.update_session_duration(session_id, max_ended_at)
        except Exception:
            log.exception("duration update failed during cleanup")
        try:
            await ws.close()
        except Exception:
            pass
        log.info("WS connection closed")


# ---------- SPA (built frontend) ----------
# Mounted LAST so every API route and the /static mount above take precedence;
# only unmatched paths fall through to the single-page app. `html=True` serves
# index.html at "/" and for client-side routes.
if APP_DIR.exists():
    app.mount("/", StaticFiles(directory=str(APP_DIR), html=True), name="spa")
