"""Job system (async background work): status + SSE progress stream."""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from ..core.jobs import JobRegistry, encode_sse

router = APIRouter()


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, request: Request) -> dict[str, Any]:
    registry: JobRegistry = request.app.state.jobs
    job = registry.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return job.snapshot()


@router.get("/jobs/{job_id}/stream")
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
