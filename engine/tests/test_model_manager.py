"""Unit tests for ModelManager (REFACTOR_PLAN.md Phase 3).

The 503-when-disabled path lives inside a background job at the HTTP layer, so a
direct unit test is the cleanest way to pin it. Also covers idempotent lazy load.
"""
from __future__ import annotations

import asyncio

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


# ---------- releasing a model nobody is using ----------
#
# 2.5 GB for a feature most sessions ask one question of, or none. Worth doing
# now and not before: the model is a subprocess, so releasing it is killing a
# process rather than asking a binding to give the memory back.


class CountingChat:
    """A chat backend that records its own load/unload, so a test can watch."""

    def __init__(self) -> None:
        self.loads = 0
        self.unloads = 0

    async def load(self) -> None:
        self.loads += 1

    async def unload(self) -> None:
        self.unloads += 1

    async def chat(self, messages, *, max_tokens=1024, temperature=0.7):  # pragma: no cover
        raise NotImplementedError

    @property
    def info(self):  # pragma: no cover
        from wrenote.core.events import BackendInfo

        return BackendInfo(name="counting")


def _mm(backend, **over) -> ModelManager:
    return ModelManager(chat_backend=backend, diarize_speaker=None, **over)


async def test_a_model_nobody_has_touched_is_released():
    """Drives the decision directly rather than waiting on a clock: what is
    worth pinning is *when it collects*, and a test that sleeps for it is a
    test that fails on a loaded CI box for no reason."""
    backend = CountingChat()
    mm = _mm(backend, chat_idle_unload_s=900)
    await mm.ensure_chat_loaded()
    assert backend.loads == 1

    await mm._reap_once()
    assert backend.unloads == 0, "collected something used a moment ago"

    mm._chat_idle_since -= 1000  # as if the afternoon had gone by
    await mm._reap_once()
    assert backend.unloads == 1

    # The next question loads it again rather than handing back a backend
    # whose weights are gone.
    await mm.ensure_chat_loaded()
    assert backend.loads == 2


async def test_using_it_keeps_it():
    """A question every ten minutes must never pay for a reload on a
    fifteen-minute timer — a cache hit is use, not idleness."""
    backend = CountingChat()
    mm = _mm(backend, chat_idle_unload_s=900)
    await mm.ensure_chat_loaded()
    for _ in range(5):
        mm._chat_idle_since -= 1000  # long enough to collect...
        await mm.ensure_chat_loaded()  # ...but it was asked for again
        await mm._reap_once()
    assert backend.unloads == 0


async def test_a_lease_outranks_the_timer():
    """The reason leases exist: "how long can a minutes job take" is not a
    number anyone should have to keep the timeout above."""
    backend = CountingChat()
    mm = _mm(backend, chat_idle_unload_s=900)
    async with mm.chat_lease():
        await mm.ensure_chat_loaded()
        mm._chat_idle_since -= 1000
        await mm._reap_once()
        assert backend.unloads == 0, "collected a model a job was using"
    # Releasing the lease starts the clock at the end of the work, not the
    # start — so it is not instantly collectable either.
    await mm._reap_once()
    assert backend.unloads == 0
    mm._chat_idle_since -= 1000
    await mm._reap_once()
    assert backend.unloads == 1


async def test_nothing_is_released_when_the_timer_is_off():
    backend = CountingChat()
    mm = _mm(backend)  # idle_unload_s defaults to 0
    mm.start()
    try:
        await mm.ensure_chat_loaded()
        mm._chat_idle_since -= 1000
        await mm._reap_once()
        assert backend.unloads == 0
        assert mm._reaper is None, "started a reaper with nothing to reap"
    finally:
        await mm.aclose()


async def test_the_reaper_actually_runs():
    """The one test that does wait on the clock, because it is the one thing
    the others assume: that something calls _reap_once on a timer at all."""
    backend = CountingChat()
    mm = _mm(backend, chat_idle_unload_s=0.2)
    mm.start()
    try:
        await mm.ensure_chat_loaded()
        for _ in range(100):
            if backend.unloads:
                break
            await asyncio.sleep(0.05)
        assert backend.unloads == 1
    finally:
        await mm.aclose()


async def test_closing_stops_the_reaper():
    """A task left ticking after shutdown holds the loop open."""
    mm = _mm(CountingChat(), chat_idle_unload_s=0.2)
    mm.start()
    await mm.aclose()
    assert mm._reaper is None


async def test_a_swap_while_idle_does_not_unload_twice():
    """replace_chat already unloads what it replaces; the reaper must not be
    handed the same backend to collect a second time."""
    old, new = CountingChat(), CountingChat()
    mm = _mm(old, chat_idle_unload_s=900)
    await mm.ensure_chat_loaded()
    await mm.replace_chat(new)
    assert old.unloads == 1
    mm._chat_idle_since -= 1000
    await mm._reap_once()
    assert old.unloads == 1  # not collected again
    assert new.loads == 0  # and the new one was never loaded to collect
