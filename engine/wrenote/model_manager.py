"""Lazy-load lifecycle for the app-lifetime models (chat + offline diarize).

Replaces the four loose ``app.state.{chat,diarize}_{loaded,load_lock}`` flags
with one object stored at ``app.state.models``. Both backends are instantiated
up-front (cheap) but their weights load on first use, serialized so concurrent
first-requests don't double-load. Lives at the app layer (not ``core``) because
``ensure_diarize_loaded`` raises an HTTP 503 when diarization is disabled.

**Loading lazily is only half of it.** The chat model is 2.5 GB and most
sessions ask it one question or none, so it is also *released* after a spell
with nothing using it (``chat.idle_unload_s``). That is worth doing now and
was not before: the model runs in a subprocess, so releasing it is killing a
process, and the memory comes back because the OS says so rather than because
a binding chose to.

Two things make it safe. A lease (:meth:`chat_lease`) marks work in flight, so
the reaper never collects a model something is part-way through using — which
matters because "how long can a minutes job take" is not a number anyone
should have to keep the timeout above. And every consumer re-asks
:meth:`ensure_chat_loaded` inside its lease, so a collection between deciding
to answer and answering costs a reload rather than a crash.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import HTTPException

from .chat.base import ChatBackend
from .speaker.base import SpeakerBackend

log = logging.getLogger(__name__)


class ModelManager:
    #: How often the reaper wakes. A fraction of the timeout, so the model is
    #: released somewhere near when it was promised without a task that ticks
    #: for nothing all day.
    CHECK_FRACTION = 0.25

    def __init__(
        self,
        *,
        chat_backend: ChatBackend | None,
        diarize_speaker: SpeakerBackend | None,
        chat_idle_unload_s: float = 0.0,
    ) -> None:
        self._chat_backend = chat_backend
        self._chat_loaded = False
        self._chat_lock = asyncio.Lock()
        self._diarize_speaker = diarize_speaker
        self._diarize_loaded = False
        self._diarize_lock = asyncio.Lock()
        self._chat_idle_unload_s = max(0.0, float(chat_idle_unload_s))
        #: Work in flight. The reaper collects only at zero.
        self._chat_leases = 0
        #: Monotonic time the last lease was released, or the model loaded.
        self._chat_idle_since = 0.0
        self._reaper: asyncio.Task[None] | None = None

    def start(self) -> None:
        """Begin releasing an idle chat model. Needs a running event loop.

        Separate from ``__init__`` because the manager is built before the
        loop exists in some tests, and a task created there would be attached
        to the wrong one.
        """
        if self._chat_idle_unload_s > 0 and self._reaper is None:
            self._reaper = asyncio.create_task(self._reap_idle(), name="chat.idle-unload")
            log.info(
                "chat model will be released after %.0fs idle", self._chat_idle_unload_s
            )

    @asynccontextmanager
    async def chat_lease(self) -> AsyncIterator[None]:
        """Hold the chat model against collection for the duration of a block.

        Wrap the *whole* piece of work, not just the load: a streamed answer
        and a minutes job both keep using the backend long after
        ``ensure_chat_loaded`` returned.
        """
        self._chat_leases += 1
        try:
            yield
        finally:
            self._chat_leases -= 1
            self._chat_idle_since = time.monotonic()

    async def _reap_idle(self) -> None:
        interval = max(1.0, self._chat_idle_unload_s * self.CHECK_FRACTION)
        while True:
            try:
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                return
            try:
                await self._reap_once()
            except Exception:  # a reaper that dies stops reclaiming, silently
                log.exception("idle chat unload failed; will try again")

    async def _reap_once(self) -> None:
        """Release the model if it is loaded, unused, and has been for long."""
        if self._chat_idle_unload_s <= 0:
            # Switched off. `start` declines to run a reaper in that case, so
            # this only matters to a direct caller — but a method that
            # collects when collection is disabled is a trap either way, and
            # the elapsed-time check below cannot catch it: everything is
            # "longer than zero".
            return
        now = time.monotonic()
        async with self._chat_lock:
            if (
                not self._chat_loaded
                or self._chat_leases > 0
                or now - self._chat_idle_since < self._chat_idle_unload_s
            ):
                return
            backend, self._chat_loaded = self._chat_backend, False
        if backend is None:
            return
        log.info("releasing the chat model after %.0fs idle", now - self._chat_idle_since)
        try:
            await backend.unload()
        except Exception:
            log.exception("unloading the idle chat model failed")

    @property
    def chat_backend(self) -> ChatBackend | None:
        return self._chat_backend

    async def ensure_chat_loaded(self) -> ChatBackend:
        """Idempotent lazy-load of the chat model; 503 when the feature is off.

        ``None`` means the user switched chat off (``chat.enabled``), so its
        2.5 GB was never downloaded. The client turns ``feature_off`` into an
        offer to enable it, which is why the detail is a code and not a
        sentence.
        """
        if self._chat_backend is None:
            raise HTTPException(status_code=503, detail="feature_off")
        if not self._chat_loaded:
            async with self._chat_lock:
                if not self._chat_loaded:
                    await self._chat_backend.load()
                    self._chat_loaded = True
        # Even a hit counts as use: a question every ten minutes should never
        # pay for a reload on a fifteen-minute timer.
        self._chat_idle_since = time.monotonic()
        return self._chat_backend

    async def ensure_diarize_loaded(self) -> SpeakerBackend:
        """Lazy-load the speaker-embedding model; 503 when disabled in config."""
        if self._diarize_speaker is None:
            raise HTTPException(
                status_code=503, detail="speaker backend disabled in config"
            )
        if not self._diarize_loaded:
            async with self._diarize_lock:
                if not self._diarize_loaded:
                    await self._diarize_speaker.load()
                    self._diarize_loaded = True
        return self._diarize_speaker

    async def replace_chat(self, backend: ChatBackend | None) -> None:
        """Swap the chat backend (a different model was chosen).

        Under the same lock ``ensure_chat_loaded`` uses, so a request that is
        mid-load finishes against the old backend rather than racing the swap.
        The old weights are unloaded — otherwise choosing a *smaller* model
        would raise memory use until the next restart.
        """
        async with self._chat_lock:
            old, was_loaded = self._chat_backend, self._chat_loaded
            self._chat_backend, self._chat_loaded = backend, False
        if was_loaded and old is not None:
            try:
                await old.unload()
            except Exception:
                log.exception("unloading the previous chat backend failed")

    async def replace_diarize_speaker(self, backend: SpeakerBackend | None) -> None:
        """Swap the offline-diarization speaker backend. See :meth:`replace_chat`."""
        async with self._diarize_lock:
            old, was_loaded = self._diarize_speaker, self._diarize_loaded
            self._diarize_speaker, self._diarize_loaded = backend, False
        if was_loaded and old is not None:
            try:
                await old.unload()
            except Exception:
                log.exception("unloading the previous speaker backend failed")

    async def aclose(self) -> None:
        """Unload whatever was loaded. Best-effort; logs and continues."""
        if self._reaper is not None:
            self._reaper.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._reaper
            self._reaper = None
        if self._chat_loaded and self._chat_backend is not None:
            try:
                await self._chat_backend.unload()
            except Exception:
                log.exception("chat backend unload failed")
        if self._diarize_loaded and self._diarize_speaker is not None:
            try:
                await self._diarize_speaker.unload()
            except Exception:
                log.exception("diarize speaker unload failed")
