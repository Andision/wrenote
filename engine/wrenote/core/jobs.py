"""Lightweight in-memory job registry.

Long-running operations (file upload + transcribe, offline diarization)
hold the user's POST request open today. That's bad UX — they can't
navigate away, refresh, or close the tab without losing progress. So we
flip those endpoints to: POST returns immediately with a job id, work
runs as a background asyncio task, and the client subscribes to progress
via SSE (``GET /jobs/{id}/stream``).

State is per-process in-memory. Single-user local app, no need for Redis.
We bound the registry size so a long-running server doesn't leak.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Phase:
    name: str
    weight: float  # share of the overall progress bar; weights sum to 1.0


JobStatus = Literal["running", "done", "error"]


@dataclass
class Job:
    id: str
    kind: str
    phases: list[Phase]
    status: JobStatus = "running"
    phase_idx: int = 0
    phase_inner: float = 0.0  # 0..1 within current phase
    log: list[str] = field(default_factory=list)
    started_at: float = field(default_factory=time.monotonic)
    finished_at: float | None = None
    error: str | None = None
    result: dict[str, Any] | None = None
    # Internal: wakes anyone awaiting the next update.
    _tick: asyncio.Event = field(default_factory=asyncio.Event)

    @property
    def fraction(self) -> float:
        """Weighted overall progress in [0, 1]."""
        if self.status == "done":
            return 1.0
        done = sum(p.weight for p in self.phases[: self.phase_idx])
        cur = (
            self.phases[self.phase_idx].weight * max(0.0, min(1.0, self.phase_inner))
            if self.phase_idx < len(self.phases)
            else 0.0
        )
        return min(1.0, done + cur)

    @property
    def elapsed_s(self) -> float:
        end = self.finished_at if self.finished_at is not None else time.monotonic()
        return end - self.started_at

    @property
    def eta_s(self) -> float | None:
        """Linear extrapolation from elapsed × (1/frac - 1). None when
        the fraction is too low to be meaningful or when finished."""
        if self.status != "running":
            return 0.0
        f = self.fraction
        if f < 0.02:  # too early to guess
            return None
        elapsed = self.elapsed_s
        total = elapsed / f
        return max(0.0, total - elapsed)

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "status": self.status,
            "phase": (
                self.phases[self.phase_idx].name
                if self.phase_idx < len(self.phases) else ""
            ),
            "phase_idx": self.phase_idx,
            "phase_count": len(self.phases),
            "fraction": round(self.fraction, 4),
            "elapsed_s": round(self.elapsed_s, 2),
            "eta_s": (
                round(self.eta_s, 1) if self.eta_s is not None else None
            ),
            "log": list(self.log[-50:]),  # tail; full log not useful over SSE
            "error": self.error,
            "result": self.result,
        }


class JobRegistry:
    def __init__(self, max_jobs: int = 64) -> None:
        self._jobs: dict[str, Job] = {}
        self._order: list[str] = []  # insertion order for LRU eviction
        self._max = max_jobs
        self._lock = asyncio.Lock()

    def create(self, *, kind: str, phases: list[Phase]) -> Job:
        if not phases:
            raise ValueError("phases must be non-empty")
        total = sum(p.weight for p in phases)
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"phase weights must sum to 1.0 (got {total:.3f})")
        job = Job(id=uuid.uuid4().hex, kind=kind, phases=list(phases))
        self._jobs[job.id] = job
        self._order.append(job.id)
        # Evict oldest if over cap.
        while len(self._order) > self._max:
            evict = self._order.pop(0)
            self._jobs.pop(evict, None)
        return job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def _wake(self, job: Job) -> None:
        # Replace the event so all current waiters fire, then arm a fresh one.
        old = job._tick
        job._tick = asyncio.Event()
        old.set()

    def advance(
        self,
        job_id: str,
        *,
        phase_idx: int | None = None,
        phase_inner: float | None = None,
        log_line: str | None = None,
    ) -> None:
        job = self._jobs.get(job_id)
        if job is None or job.status != "running":
            return
        if phase_idx is not None:
            job.phase_idx = phase_idx
            job.phase_inner = 0.0
        if phase_inner is not None:
            job.phase_inner = max(0.0, min(1.0, phase_inner))
        if log_line:
            job.log.append(log_line)
        self._wake(job)

    def complete(self, job_id: str, *, result: dict[str, Any] | None = None) -> None:
        job = self._jobs.get(job_id)
        if job is None:
            return
        job.status = "done"
        job.phase_idx = len(job.phases)
        job.phase_inner = 0.0
        job.finished_at = time.monotonic()
        job.result = result or {}
        self._wake(job)

    def fail(self, job_id: str, error: str) -> None:
        job = self._jobs.get(job_id)
        if job is None:
            return
        job.status = "error"
        job.finished_at = time.monotonic()
        job.error = error
        self._wake(job)

    async def subscribe(self, job_id: str) -> AsyncIterator[dict[str, Any]]:
        """Yield a JSON-able snapshot whenever the job changes; ends after
        the terminal (done/error) snapshot is delivered."""
        job = self._jobs.get(job_id)
        if job is None:
            return
        # Always emit the current state first so subscribers attached
        # mid-flight get an immediate paint.
        yield job.snapshot()
        while job.status == "running":
            tick = job._tick
            try:
                await asyncio.wait_for(tick.wait(), timeout=15.0)
            except TimeoutError:
                # Heartbeat — keeps clients/proxies happy on long pauses.
                pass
            yield job.snapshot()
        # Done/error already in snapshot above? Emit once more for safety.
        yield job.snapshot()


# Helper: build a phased progress reporter scoped to a single phase index.
class PhaseReporter:
    """Sugar that turns ``reporter.tick(0.42)`` calls into job advances
    pinned to a specific phase index."""

    def __init__(self, registry: JobRegistry, job_id: str, phase_idx: int) -> None:
        self.registry = registry
        self.job_id = job_id
        self.phase_idx = phase_idx

    def enter(self) -> None:
        self.registry.advance(self.job_id, phase_idx=self.phase_idx, phase_inner=0.0)

    def tick(self, inner: float, *, log: str | None = None) -> None:
        self.registry.advance(self.job_id, phase_inner=inner, log_line=log)

    def log(self, line: str) -> None:
        self.registry.advance(self.job_id, log_line=line)


def encode_sse(payload: dict[str, Any]) -> bytes:
    """Standard SSE framing: ``data: <json>\\n\\n``."""
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()
