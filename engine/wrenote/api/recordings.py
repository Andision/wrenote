"""Recording file endpoints (per-session WAV)."""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from ..core.recording import resolve_recording_path
from ._common import safe_session_id

log = logging.getLogger(__name__)
router = APIRouter()


@router.get("/recordings/{session_id}.wav")
async def get_recording(session_id: str) -> FileResponse:
    sid = safe_session_id(session_id)
    path = resolve_recording_path(sid)
    if not path.exists():
        raise HTTPException(status_code=404, detail="recording not found")
    return FileResponse(
        path=str(path),
        media_type="audio/wav",
        filename=f"{sid}.wav",
    )


@router.delete("/recordings/{session_id}.wav")
async def delete_recording(session_id: str) -> dict[str, str]:
    sid = safe_session_id(session_id)
    path = resolve_recording_path(sid)
    if path.exists():
        try:
            path.unlink()
        except Exception as e:
            log.exception("failed to delete recording %s", path)
            raise HTTPException(status_code=500, detail="delete failed") from e
    return {"status": "ok"}
