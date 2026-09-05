"""App updates: is there a newer Wrenote, and does the user want to be asked.

* ``GET  /update``          — the cached answer (no request when the automatic
  check is off).
* ``POST /update/check``    — check now, whatever the setting says.
* ``POST /update/settings`` — persist ``update.check`` to the user config and
  apply it live.

The engine reports; installing is the shell's job (see core/update.py).
"""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..core.config import write_user_config
from ..core.update import UpdateChecker
from ..deps import get_updates

router = APIRouter()


class UpdateSettings(BaseModel):
    check: bool


@router.get("/update")
async def update_status(checker: UpdateChecker = Depends(get_updates)) -> dict[str, Any]:
    return await asyncio.to_thread(checker.status)


@router.post("/update/check")
async def update_check(checker: UpdateChecker = Depends(get_updates)) -> dict[str, Any]:
    return await asyncio.to_thread(checker.status, force=True)


@router.post("/update/settings")
async def update_settings(
    body: UpdateSettings, checker: UpdateChecker = Depends(get_updates)
) -> dict[str, Any]:
    await asyncio.to_thread(write_user_config, {"update": {"check": body.check}})
    checker.set_enabled(body.check)
    return {"check": body.check}
