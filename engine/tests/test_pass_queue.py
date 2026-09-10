"""One whole-file pass at a time, and a queued one that says so.

The pass is the heaviest thing the engine does: its own whisper.cpp context,
its own copy of the weights, eight compute threads. It used to go straight to
the default executor (``cpu_count + 4`` workers), so three recordings queued
after a morning of meetings ran *concurrently* — 24 compute threads on 8
cores, three copies of the model in memory — and all three crawled while
every one of them reported ``processing``. Indistinguishable from stuck.

What these hold: the second pass waits, it is visibly *waiting* rather than
running, and the limit is a config key rather than a constant.
"""
from __future__ import annotations

import asyncio
import wave
from pathlib import Path

import pytest

import wrenote.core.batch as batch
import wrenote.core.refine as refine_mod
from wrenote.core.catalogue import ModelCatalogue
from wrenote.core.config import Config
from wrenote.core.jobs import JobRegistry
from wrenote.core.store import Store

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def fresh_gate():
    """The gate is a module global built on first use; rebuild it per test."""
    batch._pass_gate = None
    batch._pass_limit = 1
    yield
    batch._pass_gate = None
    batch._pass_limit = 1


@pytest.fixture
async def store(tmp_path):
    s = Store(tmp_path / "data.db")
    await s.open()
    yield s
    await s.close()


def _wav(path: Path, seconds: float = 1.0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"\x00\x01" * int(16000 * seconds))
    return path


def _cfg(tmp_path) -> Config:
    return Config.model_validate({
        "stt": {"backend": "mock"},
        "stt_offline": {"backend": "whisper_cpp",
                        "params": {"model_path": str(tmp_path / "m.bin")}},
        "translator": {"backend": "mock", "enabled": False},
        "data": {"dir": str(tmp_path)},
    })


async def _session(store: Store, sid: str) -> dict:
    await store.upsert_session(
        session_id=sid, title=sid, created_at="2026-01-01T00:00:00",
        src_lang="en", tgt_lang="zh", status="ready",
    )
    await store.upsert_segment_orig(
        session_id=sid, segment_id="l-0", ord_=0, started_at=0.0, ended_at=1.0,
        orig_text="live text", orig_status="final",
    )
    return await store.get_session(sid)


class TestGate:
    async def test_the_second_pass_waits_for_the_first(self):
        order: list[str] = []
        started = asyncio.Event()

        async def pass_(name: str, hold: asyncio.Event) -> None:
            async with batch.whole_file_slot():
                order.append(f"{name}:in")
                started.set()
                await hold.wait()
                order.append(f"{name}:out")

        hold_a = asyncio.Event()
        a = asyncio.create_task(pass_("a", hold_a))
        await started.wait()
        b = asyncio.create_task(pass_("b", asyncio.Event()))
        await asyncio.sleep(0)  # give b every chance to jump the queue
        assert order == ["a:in"], "the second pass got in while the first held the slot"

        hold_a.set()
        await a
        await asyncio.sleep(0)
        assert order[:3] == ["a:in", "a:out", "b:in"]
        b.cancel()

    async def test_on_wait_fires_only_when_there_is_nothing_free(self):
        waited: list[str] = []
        async with batch.whole_file_slot(on_wait=lambda: waited.append("first")):
            assert waited == []  # nothing to wait for
            task = asyncio.create_task(
                _take(batch.whole_file_slot(on_wait=lambda: waited.append("second")))
            )
            await asyncio.sleep(0)
            assert waited == ["second"]
        await task

    async def test_the_limit_comes_from_the_config(self):
        batch.set_pass_limit(2)
        held = asyncio.Event()
        entered: list[int] = []

        async def pass_() -> None:
            async with batch.whole_file_slot():
                entered.append(1)
                await held.wait()

        tasks = [asyncio.create_task(pass_()) for _ in range(3)]
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert len(entered) == 2, "a limit of 2 should let exactly two in"
        held.set()
        await asyncio.gather(*tasks)

    def test_a_limit_below_one_is_still_one(self):
        batch.set_pass_limit(0)
        assert batch._pass_limit == 1


async def _take(cm) -> None:
    async with cm:
        pass


class TestRefineReportsTheQueue:
    async def test_a_queued_pass_stays_pending_until_it_holds_the_slot(
        self, store, tmp_path, monkeypatch
    ):
        """Two recordings, one slot: the second says `pending`, not
        `processing`, for as long as it is waiting."""
        release = asyncio.Event()

        async def slow(pcm, **kwargs):
            await release.wait()
            return [("refined text", 0.0, 1.0)]

        monkeypatch.setattr(refine_mod, "transcribe_pcm", slow)
        cfg = _cfg(tmp_path)
        registry = JobRegistry()
        catalogue = ModelCatalogue.load(user=None)
        recordings = Path(cfg.data.recordings_dir)

        jobs = []
        for sid in ("s1", "s2"):
            session = await _session(store, sid)
            _wav(recordings / f"{sid}.wav")
            jobs.append(await refine_mod.launch(
                session=session, cfg=cfg, catalogue=catalogue, store=store,
                registry=registry, recordings_dir=recordings,
            ))

        # Let both runners get as far as they can.
        for _ in range(20):
            await asyncio.sleep(0)
        assert (await store.get_session("s1"))["status"] == "processing"
        assert (await store.get_session("s2"))["status"] == "pending"
        # …and the waiting one says why in its log, so the progress card can too.
        assert any("Queued" in line for line in registry.get(jobs[1]).log)

        release.set()
        for _ in range(400):
            if all(registry.get(j).status != "running" for j in jobs):
                break
            await asyncio.sleep(0.01)
        assert [registry.get(j).status for j in jobs] == ["done", "done"]
        assert (await store.get_session("s2"))["status"] == "ready"
