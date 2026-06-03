"""Upload (batch transcribe from files) — async job."""
from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile

from ..core.config import Config
from ..core.jobs import JobRegistry
from ..core.registry import make_translator
from ..core.store import Store
from ..core.upload import (
    UPLOAD_PHASES_NO_TRANSLATE,
    UPLOAD_PHASES_TRANSLATE,
    process_upload,
)

log = logging.getLogger(__name__)
router = APIRouter()


@router.post("/sessions/upload")
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
