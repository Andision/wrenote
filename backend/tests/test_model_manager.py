"""Unit tests for ModelManager (REFACTOR_PLAN.md Phase 3).

The 503-when-disabled path lives inside a background job at the HTTP layer, so a
direct unit test is the cleanest way to pin it. Also covers idempotent lazy load.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from wrenote.core.registry import make_chat
from wrenote.model_manager import ModelManager


async def test_ensure_diarize_raises_503_when_disabled():
    mm = ModelManager(chat_backend=make_chat("mock"), diarize_speaker=None)
    with pytest.raises(HTTPException) as excinfo:
        await mm.ensure_diarize_loaded()
    assert excinfo.value.status_code == 503


async def test_ensure_chat_loaded_is_idempotent_and_returns_backend():
    backend = make_chat("mock")
    mm = ModelManager(chat_backend=backend, diarize_speaker=None)
    assert await mm.ensure_chat_loaded() is backend
    # Second call must not reload; still returns the same backend.
    assert await mm.ensure_chat_loaded() is backend
    assert mm.chat_backend is backend


async def test_aclose_is_safe_when_nothing_loaded():
    mm = ModelManager(chat_backend=make_chat("mock"), diarize_speaker=None)
    await mm.aclose()  # no load happened → must not raise
