"""Capture-target enumeration for the screen/window recorder.

`GET /capture/targets` feeds both PreFlight pickers: the windows + displays
the user can record, and — from the same list — the one application whose
audio to capture. Pure enumeration (no app state), so no deps.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from ..core import screenrec
from ..core.syscap import system_audio_can_scope

router = APIRouter()


@router.get("/capture/targets")
async def capture_targets() -> dict[str, Any]:
    """List capturable displays + windows. Empty when unsupported / permission
    not yet granted (the UI then just offers full-screen / mic-only).

    ``audio_scope`` says whether a window's ``bundle`` can be used to capture
    only that application's sound. macOS can (ScreenCaptureKit filters audio
    by application); Windows can only where the `procloop` helper shipped.
    Where it cannot, the client must not offer the choice — capturing the
    whole desktop when someone asked for one app records more than they
    agreed to.
    """
    targets = await screenrec.list_targets()
    return {**targets, "audio_scope": system_audio_can_scope()}
