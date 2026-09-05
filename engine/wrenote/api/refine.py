"""Re-transcribe a finished session from its recording — async job.

The same pass runs on its own after a recording stops (see ``ws.py``); this
is the manual trigger for a session recorded before that existed, one whose
pass failed, or one the user wants done again with a different model.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from ..core import refine
from ..core.catalogue import ModelCatalogue
from ..core.config import Config
from ..core.jobs import JobRegistry
from ..core.store import Store
from ..deps import get_catalogue, get_config, get_jobs, get_recordings_dir, get_store
from ._common import safe_session_id

log = logging.getLogger(__name__)
router = APIRouter()

# Which precondition failed → the HTTP status a client expects for it.
_STATUS_FOR: dict[str, int] = {
    "busy": 409,
    "recording": 409,
    "no_recording": 400,
    "unsupported_backend": 400,
    "no_model": 500,
}


@router.post("/sessions/{session_id}/refine")
async def refine_endpoint(
    session_id: str,
    request: Request,
    store: Store = Depends(get_store),
    cfg: Config = Depends(get_config),
    catalogue: ModelCatalogue = Depends(get_catalogue),
    registry: JobRegistry = Depends(get_jobs),
    recordings_dir: Path = Depends(get_recordings_dir),
) -> dict[str, str]:
    """Kick off the whole-recording pass. Returns ``{job_id}``; subscribe to
    ``/jobs/{job_id}/stream``. Body (optional): ``{"translate": bool}`` —
    default is whatever the session had.

    The session shows ``status: processing`` from now until the job ends; its
    current rows stay readable and are replaced in one go at the end.
    """
    sid = safe_session_id(session_id)
    body: dict[str, Any] = {}
    raw = await request.body()
    if raw:
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            pass
    translate = body.get("translate")
    if translate is not None and not isinstance(translate, bool):
        raise HTTPException(status_code=400, detail="translate must be a boolean")

    session = await store.get_session(sid)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    try:
        job_id = await refine.launch(
            session=session,
            cfg=cfg,
            catalogue=catalogue,
            store=store,
            registry=registry,
            recordings_dir=recordings_dir,
            translate=translate,
        )
    except refine.RefineError as e:
        raise HTTPException(status_code=_STATUS_FOR.get(e.code, 400), detail=e.code) from e
    return {"job_id": job_id}
