"""Capture-target enumeration for the screen/window recorder.

`GET /capture/targets` feeds the PreFlight picker: the windows + displays the
user can choose to record. Pure enumeration (no app state), so no deps.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from ..core import screenrec

router = APIRouter()


@router.get("/capture/targets")
async def capture_targets() -> dict[str, list[dict[str, Any]]]:
    """List capturable displays + windows. Empty when unsupported / permission
    not yet granted (the UI then just offers full-screen / mic-only)."""
    return await screenrec.list_targets()
