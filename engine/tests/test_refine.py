"""The post-recording pass (core/refine.py) and the session lifecycle it drives.

Whisper is replaced by a stub that returns rows for the recording it is
given; everything else — the store, the job registry, the translator (mock)
— is real. What these hold: the old rows stay until the new ones land, a
failure leaves them and says why, speaker labels survive by time overlap,
the job is findable from the session, and the start-up sweep settles what a
crash left behind.
"""
from __future__ import annotations

import asyncio
import wave
from pathlib import Path

import pytest

import wrenote.core.refine as refine_mod
from wrenote.core.catalogue import ModelCatalogue
from wrenote.core.config import Config
from wrenote.core.jobs import JobRegistry
from wrenote.core.refine import RefineError, carry_speakers, refine_session
from wrenote.core.store import Store
from wrenote.translator.mock import MockTranslatorBackend


def _wav(path: Path, seconds: float = 3.0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"\x00\x01" * int(16000 * seconds))
    return path


async def _session_with_live_rows(store: Store, sid: str = "s1") -> dict:
    await store.upsert_session(
        session_id=sid, title="T", created_at="2026-01-01T00:00:00",
        src_lang="en", tgt_lang="zh", status="ready",
    )
    rows = [
        ("live-1", 0, 0.0, 1.4, "hello every", "Speaker 1"),
        ("live-2", 1, 1.4, 2.9, "one welcome", "Speaker 2"),
    ]
    for seg_id, ord_, t0, t1, text, speaker in rows:
        await store.upsert_segment_orig(
            session_id=sid, segment_id=seg_id, ord_=ord_, started_at=t0, ended_at=t1,
            orig_text=text, orig_status="final", orig_lang="en", speaker=speaker,
        )
        await store.upsert_segment_trans(
            session_id=sid, segment_id=seg_id, ord_=ord_, trans_text=f"译 {text}",
            trans_status="final", trans_lang="zh",
        )
    session = await store.get_session(sid)
    assert session is not None
    return session


@pytest.fixture
async def store(tmp_path):
    s = Store(tmp_path / "data.db")
    await s.open()
    yield s
    await s.close()


def _stub_whisper(monkeypatch, rows):
    async def fake(pcm, **kwargs):
        fake.calls.append(kwargs)
        return rows
    fake.calls = []
    monkeypatch.setattr(refine_mod, "transcribe_pcm", fake)
    return fake


class TestCarrySpeakers:
    def test_label_of_the_most_overlapping_old_row(self):
        old = [
            {"started_at": 0.0, "ended_at": 2.0, "speaker": "Alice"},
            {"started_at": 2.0, "ended_at": 4.0, "speaker": "Bob"},
        ]
        new = [
            {"started_at": 0.5, "ended_at": 2.5},  # 1.5 s Alice, 0.5 s Bob
            {"started_at": 2.5, "ended_at": 3.5},
            {"started_at": 4.5, "ended_at": 5.0},  # nobody
        ]
        out = carry_speakers(old, new)
        assert [r.get("speaker") for r in out] == ["Alice", "Bob", None]
        assert "speaker" not in new[0]  # input untouched

    def test_unlabelled_old_rows_carry_nothing(self):
        old = [{"started_at": 0.0, "ended_at": 2.0, "speaker": None}]
        assert carry_speakers(old, [{"started_at": 0, "ended_at": 1}])[0].get("speaker") is None


class TestRefineSession:
    async def test_replaces_rows_translates_with_context_and_keeps_speakers(
        self, store, tmp_path, monkeypatch
    ):
        session = await _session_with_live_rows(store)
        whisper = _stub_whisper(monkeypatch, [("Hello everyone.", 0.0, 1.5), ("Welcome.", 1.5, 2.9)])
        registry = JobRegistry()
        job = registry.create(kind="refine", phases=refine_mod.REFINE_PHASES_TRANSLATE, session_id="s1")
        translator = MockTranslatorBackend(delay_s=0)

        result = await refine_session(
            job_id=job.id, registry=registry, session=session,
            wav_path=_wav(tmp_path / "s1.wav"), whisper_model_path="m.bin",
            translator=translator, translate=True, store=store,
            glossary_entries=[{"term": "Wrenote"}],
        )

        assert result["n_segments"] == 2 and result["translated"] == 2
        after = await store.get_session("s1")
        assert [r["segment_id"] for r in after["segments"]] == ["r-0000", "r-0001"]
        assert [r["orig_text"] for r in after["segments"]] == ["Hello everyone.", "Welcome."]
        assert [r["speaker"] for r in after["segments"]] == ["Speaker 1", "Speaker 2"]
        assert after["segments"][1]["trans_text"] == "[TRANSLATED] Welcome."
        # The second line was translated knowing the first.
        assert translator.last_context == ("Hello everyone.",)
        # The glossary reached Whisper.
        assert whisper.calls[0]["initial_prompt"].startswith("Glossary: Wrenote")
        assert after["status"] == "ready" and after["refined_at"]
        assert after["duration_s"] == pytest.approx(3.0)

    async def test_transcribe_only_session(self, store, tmp_path, monkeypatch):
        session = await _session_with_live_rows(store)
        _stub_whisper(monkeypatch, [("Hi.", 0.0, 1.0)])
        registry = JobRegistry()
        job = registry.create(kind="refine", phases=refine_mod.REFINE_PHASES_NO_TRANSLATE, session_id="s1")
        await refine_session(
            job_id=job.id, registry=registry, session=session,
            wav_path=_wav(tmp_path / "s1.wav"), whisper_model_path="m.bin",
            translator=None, translate=False, store=store,
        )
        (row,) = (await store.get_session("s1"))["segments"]
        assert (row["trans_text"], row["trans_status"]) == ("", "skipped")


def _cfg(tmp_path, backend: str = "whisper_cpp") -> Config:
    return Config.model_validate(
        {
            "stt": {"backend": "mock"},
            "stt_offline": {"backend": backend, "params": {"model_path": str(tmp_path / "m.bin")}},
            "translator": {"backend": "mock"},
            "data": {"dir": str(tmp_path)},
        }
    )


class TestLaunch:
    async def test_marks_processing_then_ready_and_the_job_is_findable(
        self, store, tmp_path, monkeypatch
    ):
        session = await _session_with_live_rows(store)
        _stub_whisper(monkeypatch, [("Hello everyone, welcome.", 0.0, 2.9)])
        registry = JobRegistry()
        cfg = _cfg(tmp_path)
        _wav(Path(cfg.data.recordings_dir) / "s1.wav")

        job_id = await refine_mod.launch(
            session=session, cfg=cfg, catalogue=ModelCatalogue.load(user=None), store=store,
            registry=registry, recordings_dir=Path(cfg.data.recordings_dir),
        )
        # Processing from the moment launch() returns: a list fetched now shows it.
        assert (await store.get_session("s1"))["status"] == "processing"
        assert registry.get(job_id).session_id == "s1"
        assert registry.active_for("s1").id == job_id

        for _ in range(200):
            if registry.get(job_id).status != "running":
                break
            await asyncio.sleep(0.01)
        assert registry.get(job_id).status == "done"
        after = await store.get_session("s1")
        assert after["status"] == "ready" and len(after["segments"]) == 1
        assert registry.active_for("s1") is None

    async def test_a_failure_keeps_the_old_rows_and_says_why(self, store, tmp_path, monkeypatch):
        session = await _session_with_live_rows(store)

        async def boom(pcm, **kwargs):
            raise RuntimeError("model file corrupt")
        monkeypatch.setattr(refine_mod, "transcribe_pcm", boom)
        registry = JobRegistry()
        cfg = _cfg(tmp_path)
        _wav(Path(cfg.data.recordings_dir) / "s1.wav")

        job_id = await refine_mod.launch(
            session=session, cfg=cfg, catalogue=ModelCatalogue.load(user=None), store=store,
            registry=registry, recordings_dir=Path(cfg.data.recordings_dir),
        )
        for _ in range(200):
            if registry.get(job_id).status != "running":
                break
            await asyncio.sleep(0.01)
        assert registry.get(job_id).status == "error"
        after = await store.get_session("s1")
        assert after["status"] == "failed"
        assert after["status_detail"] == "RuntimeError: model file corrupt"
        assert [r["segment_id"] for r in after["segments"]] == ["live-1", "live-2"]

    async def test_preconditions(self, store, tmp_path):
        session = await _session_with_live_rows(store)
        registry = JobRegistry()
        cfg = _cfg(tmp_path)
        cat = ModelCatalogue.load(user=None)
        rec = Path(cfg.data.recordings_dir)
        kw = dict(session=session, cfg=cfg, catalogue=cat, store=store, registry=registry, recordings_dir=rec)

        with pytest.raises(RefineError, match="no_recording"):
            await refine_mod.launch(**kw)
        _wav(rec / "s1.wav")
        with pytest.raises(RefineError, match="unsupported_backend"):
            await refine_mod.launch(**{**kw, "cfg": _cfg(tmp_path, backend="mock")})
        with pytest.raises(RefineError, match="recording"):
            await refine_mod.launch(**{**kw, "session": {**session, "status": "recording"}})
        registry.create(kind="diarize", phases=refine_mod.REFINE_PHASES_NO_TRANSLATE, session_id="s1")
        with pytest.raises(RefineError, match="busy"):
            await refine_mod.launch(**kw)
        # Refused before anything was touched.
        assert (await store.get_session("s1"))["status"] == "ready"


class TestRecoverStatuses:
    async def test_settles_what_a_crash_left(self, store):
        for sid, status in (("a", "recording"), ("b", "processing"), ("c", "ready")):
            await store.upsert_session(
                session_id=sid, title=sid, created_at="2026-01-01T00:00:00",
                src_lang="en", tgt_lang="zh", status=status,
            )
        assert await store.recover_statuses() == 2
        rows = {r["id"]: (r["status"], r["status_detail"]) for r in await store.list_sessions()}
        assert rows == {"a": ("ready", None), "b": ("failed", "interrupted"), "c": ("ready", None)}

    async def test_unknown_status_is_refused(self, store):
        with pytest.raises(ValueError):
            await store.set_session_status("x", "done")
