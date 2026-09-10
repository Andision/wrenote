"""Session CRUD endpoints (SQLite-backed)."""
from __future__ import annotations

import asyncio
import logging
import re
import shutil
import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from ..core import export as export_mod
from ..core import minutes as minutes_mod
from ..core.config import Config
from ..core.jobs import JobRegistry
from ..core.recording import resolve_recording_path
from ..core.store import Store
from ..deps import get_config, get_exports_dir, get_jobs, get_recordings_dir, get_store
from ._common import SAFE_SESSION_ID, safe_session_id

log = logging.getLogger(__name__)
router = APIRouter()


def _with_job(row: dict[str, Any], jobs: JobRegistry) -> dict[str, Any]:
    """A session in ``processing`` names the job doing it, so a client that
    finds one (after a reload, or on another tab) can follow its progress."""
    job = jobs.active_for(row["id"]) if row.get("status") == "processing" else None
    row["job_id"] = job.id if job is not None else None
    return row


def _decode_cursor(cursor: str) -> tuple[str, str]:
    """``<created_at>|<id>`` of the last row the client has."""
    created_at, sep, sid = cursor.rpartition("|")
    if not sep or not created_at or not SAFE_SESSION_ID.match(sid):
        raise HTTPException(status_code=400, detail="invalid cursor")
    return created_at, sid


@router.get("/sessions")
async def list_sessions(
    limit: int = Query(0, ge=0, le=500),
    cursor: str | None = None,
    store: Store = Depends(get_store),
    jobs: JobRegistry = Depends(get_jobs),
) -> dict[str, Any]:
    """Newest first. ``limit`` 0 = all (the default, for older clients);
    otherwise a page, with ``next_cursor`` to pass back for the one after —
    null when this was the last."""
    before = _decode_cursor(cursor) if cursor else None
    if limit == 0:
        rows = await store.list_sessions(before=before)
        next_cursor = None
    else:
        rows = await store.list_sessions(limit=limit + 1, before=before)
        more = len(rows) > limit
        rows = rows[:limit]
        next_cursor = f"{rows[-1]['created_at']}|{rows[-1]['id']}" if more and rows else None
    return {"sessions": [_with_job(r, jobs) for r in rows], "next_cursor": next_cursor}


@router.get("/sessions/{session_id}")
async def get_session(
    session_id: str,
    store: Store = Depends(get_store),
    jobs: JobRegistry = Depends(get_jobs),
) -> dict[str, Any]:
    sid = safe_session_id(session_id)
    sess = await store.get_session(sid)
    if sess is None:
        raise HTTPException(status_code=404, detail="session not found")
    return _with_job(sess, jobs)


@router.get("/sessions/{session_id}/export")
async def export_session(
    session_id: str,
    fmt: str = "md",
    content: str = "both",
    minutes: str = "",
    store: Store = Depends(get_store),
) -> PlainTextResponse:
    """Export the transcript as text. ``fmt`` = md|txt|srt|vtt;
    ``content`` = original|translation|both. ``minutes`` = a language code
    puts that language's minutes before the transcript (md and txt only;
    404 when the session has none in that language). Returned as text so
    the frontend can copy it or save it client-side with a chosen filename."""
    sid = safe_session_id(session_id)
    text, mime, _ext = await _render_export(sid, fmt, content, minutes, store)
    return PlainTextResponse(text, media_type=mime)


async def _render_export(
    sid: str, fmt: str, content: str, minutes: str, store: Store
) -> tuple[str, str, str]:
    """The text, its mime type and its extension. Shared by the GET (which
    hands it to the client) and the save (which writes it)."""
    if content not in ("original", "translation", "both"):
        raise HTTPException(status_code=400, detail="invalid content")
    sess = await store.get_session(sid)
    if sess is None:
        raise HTTPException(status_code=404, detail="session not found")
    try:
        text, mime, ext = export_mod.export_transcript(sess, fmt, content)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if minutes:
        if fmt not in ("md", "txt"):
            raise HTTPException(status_code=400, detail="minutes only in md or txt")
        row = next((r for r in await store.list_minutes(sid) if r["lang"] == minutes), None)
        if row is None:
            raise HTTPException(status_code=404, detail="minutes not found")
        doc = minutes_mod.row_to_public(row, "")["content"]
        text = export_mod.with_minutes(text, minutes_mod.to_markdown(doc, minutes), fmt)
    return text, mime, ext


class SaveExportRequest(BaseModel):
    """Which rendering to write, and — for the markdown/text formats — which
    language's minutes to put in front of it."""

    fmt: str = "md"
    content: str = "both"
    minutes: str = ""


@router.post("/sessions/{session_id}/export/save")
async def save_export(
    session_id: str,
    body: SaveExportRequest,
    store: Store = Depends(get_store),
    exports_dir: Path = Depends(get_exports_dir),
) -> dict[str, Any]:
    """Write the export to ``data.exports_dir`` and say where it went.

    The client used to save it with a blob download, which in a WebView
    lands somewhere the app can neither choose nor name — so the user got a
    file with no idea whether, or where. The engine is local by construction,
    so it can just write the file and answer with the absolute path; the
    directory is a config key, which is the "choose where" half.
    """
    sid = safe_session_id(session_id)
    text, _mime, ext = await _render_export(sid, body.fmt, body.content, body.minutes, store)
    sess = await store.get_session(sid)
    assert sess is not None  # _render_export 404s otherwise
    base = safe_filename(str(sess.get("title") or "") or sid)
    path = await asyncio.to_thread(write_unique, exports_dir, base, ext, text)
    log.info("saved export for %s to %s", sid, path)
    return {
        "path": str(path),
        "filename": path.name,
        "dir": str(exports_dir),
        "bytes": len(text.encode("utf-8")),
    }


#: Characters no mainstream filesystem takes, plus the ones that make a name
#: awkward to type back. Control characters go too — a session title is user
#: text and can contain anything.
_UNSAFE_NAME = re.compile(r'[/\\?%*:|"<>\x00-\x1f]')


def safe_filename(title: str) -> str:
    name = _UNSAFE_NAME.sub("_", title).strip(" .")
    return (name[:80] or "transcript")


def write_unique(directory: Path, base: str, ext: str, text: str) -> Path:
    """``base.ext``, or ``base (2).ext`` and so on. Exporting twice should
    leave you with both files, not silently replace the first."""
    directory.mkdir(parents=True, exist_ok=True)
    candidate = directory / f"{base}.{ext}"
    n = 2
    while candidate.exists():
        candidate = directory / f"{base} ({n}).{ext}"
        n += 1
    candidate.write_text(text, encoding="utf-8")
    return candidate


class RevealRequest(BaseModel):
    path: str


@router.post("/reveal")
async def reveal(
    body: RevealRequest,
    cfg: Config = Depends(get_config),
) -> dict[str, str]:
    """Show a file or folder in the OS file manager.

    The client cannot do this: a page may not navigate to `file://`, so the
    "Show folder" button on a save toast was a silent no-op in a browser tab
    and blocked by the shell's opener scope under Tauri. The engine is a
    local process and can just ask the desktop.

    Only paths under a directory this engine writes to — the data root, and
    the exports folder, which since it defaults to the user's Downloads is
    usually somewhere else entirely. Anything else is refused: this takes a
    path from the client and hands it to the window server, so the set of
    things it can open has to be ours, not whatever was asked for.
    """
    target = Path(body.path).expanduser()
    roots = [
        Path(p).expanduser().resolve()
        for p in (cfg.data.dir, cfg.data.exports_dir, cfg.data.recordings_dir)
    ]
    try:
        resolved = target.resolve()
    except OSError:
        raise HTTPException(status_code=400, detail="bad_path") from None
    if not any(resolved.is_relative_to(r) for r in roots):
        raise HTTPException(status_code=400, detail="path_not_ours")
    if not resolved.exists():
        raise HTTPException(status_code=404, detail="not_found")

    folder = resolved if resolved.is_dir() else resolved.parent
    argv = _reveal_argv(resolved, folder)
    if argv is None:
        raise HTTPException(status_code=501, detail="unsupported_platform")
    try:
        # No shell: the path is user data and may contain anything.
        await asyncio.create_subprocess_exec(
            *argv, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
        )
    except Exception as e:
        log.exception("reveal failed for %s", resolved)
        raise HTTPException(status_code=500, detail="reveal_failed") from e
    return {"path": str(resolved)}


def _reveal_argv(target: Path, folder: Path) -> list[str] | None:
    """The command that shows ``target`` in a file manager, per platform.
    macOS and Windows can select the file itself; Linux opens the folder."""
    if sys.platform == "darwin":
        return ["open", "-R", str(target)] if target.is_file() else ["open", str(folder)]
    if sys.platform == "win32":
        return ["explorer", f"/select,{target}"] if target.is_file() else ["explorer", str(folder)]
    opener = shutil.which("xdg-open")
    return [opener, str(folder)] if opener else None


@router.patch("/sessions/{session_id}")
async def patch_session(
    session_id: str,
    request: Request,
    store: Store = Depends(get_store),
) -> dict[str, str]:
    """Currently only supports renaming. Body: ``{"title": "..."}``."""
    sid = safe_session_id(session_id)
    body = await request.json()
    title = body.get("title")
    if not isinstance(title, str) or not title.strip():
        raise HTTPException(status_code=400, detail="title required")
    await store.update_session_title(sid, title.strip())
    return {"status": "ok"}


@router.delete("/sessions/{session_id}")
async def delete_session(
    session_id: str,
    store: Store = Depends(get_store),
    recordings_dir: Path = Depends(get_recordings_dir),
) -> dict[str, str]:
    """Delete the session row (cascades to segments) AND the WAV file."""
    sid = safe_session_id(session_id)
    existed = await store.delete_session(sid)
    # Always try to remove the WAV — file may exist without a DB row if
    # a previous run died mid-session.
    wav = resolve_recording_path(sid, recordings_dir=recordings_dir)
    if wav.exists():
        try:
            wav.unlink()
        except Exception:
            log.exception("failed to remove recording %s", wav)
    return {"status": "ok" if existed else "not_found"}
