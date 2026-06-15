"""First-run model download (status + background download job)."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends

from ..core.config import Config
from ..core.jobs import JobRegistry, Phase
from ..core.models import download_model, required_models
from ..deps import get_config, get_jobs

log = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/models/status")
async def models_status(cfg: Config = Depends(get_config)) -> dict[str, Any]:
    """Which required models are present in ~/.wrenote/models/, and their sizes."""
    entries = required_models(cfg)
    return {
        "models": [e.status_dict() for e in entries],
        "all_present": all(e.present for e in entries),
    }


@router.post("/api/models/download")
async def models_download(
    cfg: Config = Depends(get_config),
    jobs: JobRegistry = Depends(get_jobs),
) -> dict[str, Any]:
    """Start downloading any missing models as a background job. Progress streams
    over ``/jobs/{job_id}/stream`` (one weighted phase per model)."""
    missing = [e for e in required_models(cfg) if not e.present]
    if not missing:
        return {"job_id": None, "all_present": True}

    total = sum(e.approx_size for e in missing) or 1
    phases = [Phase(name=e.filename, weight=e.approx_size / total) for e in missing]
    job = jobs.create(kind="model_download", phases=phases)

    async def runner() -> None:
        try:
            for idx, entry in enumerate(missing):
                jobs.advance(
                    job.id, phase_idx=idx, phase_inner=0.0,
                    log_line=f"Downloading {entry.filename}",
                )

                def _progress(frac: float, status: str) -> None:
                    jobs.advance(job.id, phase_inner=frac, log_line=status)

                await download_model(entry, _progress)
            jobs.complete(job.id, result={"downloaded": [e.filename for e in missing]})
        except Exception as ex:  # surfaced to the client via the job registry
            log.exception("model download failed")
            jobs.fail(job.id, str(ex))

    asyncio.create_task(runner())
    return {"job_id": job.id, "all_present": False}
