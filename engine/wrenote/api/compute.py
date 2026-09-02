"""Compute runtime: hardware, packs, install, and accelerator selection.

* ``GET  /compute/status``  — hardware, candidate chain, active runtime, packs
  (with availability from the published index).
* ``POST /compute/install`` — download + verify + unpack a runtime pack as a
  background job (progress over ``/jobs/{id}/stream``).
* ``POST /compute/select``  — persist ``compute.accelerator`` to the user
  config. Native bindings can't be re-imported in a running process, so the
  change takes effect on the next launch; the response says so.
* ``DELETE /compute/packs/{variant}`` — remove an installed pack.
"""
from __future__ import annotations

import asyncio
import functools
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..core.config import write_user_config
from ..core.jobs import JobRegistry, Phase
from ..core.runtimes import VARIANTS, RuntimeManager, RuntimeUnavailable
from ..deps import get_jobs, get_runtimes

log = logging.getLogger(__name__)
router = APIRouter()


class InstallRequest(BaseModel):
    variant: str


class SelectRequest(BaseModel):
    accelerator: str  # "auto" or one of VARIANTS


def _check_variant(variant: str) -> str:
    v = variant.strip().lower()
    if v not in VARIANTS:
        raise HTTPException(status_code=400, detail=f"unknown variant {variant!r}; one of {list(VARIANTS)}")
    return v


@router.get("/compute/status")
async def compute_status(runtimes: RuntimeManager = Depends(get_runtimes)) -> dict[str, Any]:
    """Detected hardware, the runtime-pack chain, which one is active, and
    which packs can be installed (index lookup runs off the event loop)."""
    return await asyncio.to_thread(runtimes.status, include_index=True)


@router.post("/compute/install")
async def compute_install(
    body: InstallRequest,
    runtimes: RuntimeManager = Depends(get_runtimes),
    jobs: JobRegistry = Depends(get_jobs),
) -> dict[str, Any]:
    """Install a runtime pack in the background. Returns ``job_id`` (``None``
    when it is already installed). 409 when no pack is published for this
    machine, so the UI can say so without starting a job."""
    variant = _check_variant(body.variant)
    if runtimes.pack(variant).installed:
        return {"job_id": None, "installed": True, "variant": variant}
    try:
        release = await asyncio.to_thread(runtimes.release_for, variant)
    except RuntimeUnavailable as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    if release is None:
        raise HTTPException(
            status_code=409,
            detail=f"no {variant!r} runtime pack is published for {runtimes.platform_tag}",
        )

    job = jobs.create(
        kind="runtime_install",
        phases=[Phase(name=f"download {variant}", weight=0.9), Phase(name="unpack", weight=0.1)],
    )
    loop = asyncio.get_running_loop()

    def _progress(frac: float, status: str) -> None:
        # Called from the worker thread; JobRegistry is event-loop-only.
        if frac < 0.9:
            phase, inner = 0, frac / 0.9
        else:
            phase, inner = 1, (frac - 0.9) / 0.1
        loop.call_soon_threadsafe(
            functools.partial(
                jobs.advance, job.id, phase_idx=phase, phase_inner=inner, log_line=status
            )
        )

    async def runner() -> None:
        try:
            pack = await asyncio.to_thread(runtimes.ensure, variant, _progress)
            jobs.complete(job.id, result={"variant": variant, "path": str(pack.path)})
        except Exception as ex:  # surfaced to the client via the job registry
            log.exception("runtime install failed")
            jobs.fail(job.id, str(ex))

    task = asyncio.create_task(runner())
    _background.add(task)
    task.add_done_callback(_background.discard)
    return {"job_id": job.id, "installed": False, "variant": variant, "release": release.to_dict()}


_background: set[asyncio.Task[None]] = set()


@router.post("/compute/select")
async def compute_select(
    body: SelectRequest, runtimes: RuntimeManager = Depends(get_runtimes)
) -> dict[str, Any]:
    """Pin (or ``auto``) the accelerator for the next launch."""
    acc = body.accelerator.strip().lower()
    if acc != "auto":
        acc = _check_variant(acc)
        # Choosing a variant explicitly is a request to try it again.
        runtimes.clear_bad(acc)
    path = await asyncio.to_thread(write_user_config, {"compute": {"accelerator": acc}})
    active = runtimes.active.variant if runtimes.active else None
    return {
        "accelerator": acc,
        "active": active,
        "restart_required": True,
        "config_path": str(path),
    }


@router.delete("/compute/packs/{variant}")
async def compute_remove(
    variant: str, runtimes: RuntimeManager = Depends(get_runtimes)
) -> dict[str, Any]:
    v = _check_variant(variant)
    if runtimes.pack(v).builtin:
        raise HTTPException(status_code=400, detail="the built-in runtime cannot be removed")
    removed = await asyncio.to_thread(runtimes.remove, v)
    return {"variant": v, "removed": removed, "restart_required": removed}
