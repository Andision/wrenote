"""Upload (batch transcribe from files) — async job."""
from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from ..core import glossary
from ..core.catalogue import ModelCatalogue, resolve
from ..core.config import Config
from ..core.jobs import JobRegistry
from ..core.registry import make_translator
from ..core.store import Store
from ..core.upload import (
    UPLOAD_PHASES_NO_TRANSLATE,
    UPLOAD_PHASES_TRANSLATE,
    process_upload,
)
from ..deps import get_catalogue, get_config, get_jobs, get_store

log = logging.getLogger(__name__)
router = APIRouter()


@router.post("/sessions/upload")
async def upload_session(
    files: list[UploadFile] = File(...),
    title: str = Form("Untitled session"),
    src_lang: str = Form("auto"),
    tgt_lang: str = Form("zh"),
    translate: bool = Form(True),
    cfg: Config = Depends(get_config),
    store: Store = Depends(get_store),
    registry: JobRegistry = Depends(get_jobs),
    catalogue: ModelCatalogue = Depends(get_catalogue),
) -> dict[str, str]:
    """Kicks off a background job; returns immediately.

    Returns ``{job_id, session_id}``. Client subscribes to
    ``/jobs/{job_id}/stream`` for progress + final result.
    """
    if not files:
        raise HTTPException(status_code=400, detail="no files")

    if cfg.stt.backend != "whisper_cpp":
        raise HTTPException(
            status_code=400,
            detail="upload only supports the whisper_cpp STT backend currently",
        )
    whisper_model_path = str(resolve(cfg, "stt", catalogue).params.get("model_path") or "")
    if not whisper_model_path:
        raise HTTPException(status_code=500, detail="stt model not configured")

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
    phases = UPLOAD_PHASES_TRANSLATE if translate else UPLOAD_PHASES_NO_TRANSLATE
    job = registry.create(kind="upload", phases=list(phases), session_id=sid)

    async def runner() -> None:
        translator = (
            make_translator(
                cfg.translator.backend, resolve(cfg, "translator", catalogue).params
            )
            if translate else None
        )
        try:
            entries = await store.list_glossary()
            if translator is not None:
                glossary.apply_to_backends(entries, translator=translator)
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
                recordings_dir=Path(cfg.data.recordings_dir),
                initial_prompt=glossary.stt_initial_prompt(entries),
            )
            registry.complete(
                job.id,
                result={"session_id": sid},
            )
        except Exception as e:
            log.exception("upload job %s failed", job.id)
            detail = f"{type(e).__name__}: {e}"
            try:
                # The row may not exist yet (decode failed): only mark it when it does.
                if await store.get_session(sid) is not None:
                    await store.set_session_status(sid, "failed", detail=detail)
            except Exception:
                log.exception("could not record upload failure for %s", sid)
            registry.fail(job.id, detail)
        finally:
            if translator is not None:
                try:
                    await translator.unload()
                except Exception:
                    pass
            shutil.rmtree(tmpdir, ignore_errors=True)

    task = asyncio.create_task(runner(), name=f"upload-{job.id[:8]}")
    # Hold a reference: the event loop keeps only a weak one, so an
    # unreferenced task can be collected mid-flight (RUF006).
    _background.add(task)
    task.add_done_callback(_background.discard)
    return {"job_id": job.id, "session_id": sid}


_background: set[asyncio.Task[None]] = set()
