"""Compute runtime status: hardware, candidate accelerators, active runtime.

Read-only for now. Switching the accelerator is a config change
(``compute.accelerator``) that takes effect on the next launch, because the
native bindings can't be re-imported in a running process.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from ..core.runtimes import RuntimeManager
from ..deps import get_runtimes

router = APIRouter()


@router.get("/compute/status")
async def compute_status(runtimes: RuntimeManager = Depends(get_runtimes)) -> dict[str, Any]:
    """Detected hardware, the runtime-pack chain, and which one is active."""
    return runtimes.status()
