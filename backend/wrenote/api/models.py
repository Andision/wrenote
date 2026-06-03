"""First-run model download (status + background download job)."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Request

from ..core.config import Config
from ..core.jobs import JobRegistry, Phase
from ..core.models import download_model, required_models

log = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/models/status")
async def models_status(request: Request) -> dict[str, Any]:
    """Which required models are present in ~/.wrenote/models/, and their sizes."""
    cfg: Config = request.app.state.config
    entries = required_models(cfg)
    return {
        "models": [e.status_dict() for e in entries],
        "all_present": all(e.present for e in entries),
    }


@router.post("/api/models/download")
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
        except Exception as ex:  # surfaced to the client via the job registry
            log.exception("model download failed")
            registry.fail(job.id, str(ex))

    asyncio.create_task(runner())
    return {"job_id": job.id, "all_present": False}
