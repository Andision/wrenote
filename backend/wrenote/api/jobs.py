"""Job system (async background work): status + SSE progress stream."""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from ..core.jobs import JobRegistry, encode_sse
from ..deps import get_jobs

router = APIRouter()


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, jobs: JobRegistry = Depends(get_jobs)) -> dict[str, Any]:
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return job.snapshot()


@router.get("/jobs/{job_id}/stream")
async def stream_job(
    job_id: str, jobs: JobRegistry = Depends(get_jobs)
) -> StreamingResponse:
    """SSE stream of job snapshots. Closes when the job is done/error."""
    if jobs.get(job_id) is None:
        raise HTTPException(status_code=404, detail="job not found")

    async def gen() -> AsyncIterator[bytes]:
        async for snap in jobs.subscribe(job_id):
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
