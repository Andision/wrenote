"""Manual re-segmentation: split/merge pure transforms + endpoints."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

import wrenote.core.store as store_mod
from wrenote.core import resegment


def _segs():
    return [
        {"segment_id": "a", "ord": 0, "started_at": 0.0, "ended_at": 4.0,
         "orig_text": "hello world", "orig_status": "final", "orig_lang": "en",
         "trans_text": "你好世界", "trans_status": "final", "trans_lang": "zh", "speaker": "S1"},
        {"segment_id": "b", "ord": 1, "started_at": 4.0, "ended_at": 6.0,
         "orig_text": "again", "orig_status": "final", "orig_lang": "en",
         "trans_text": "再次", "trans_status": "final", "trans_lang": "zh", "speaker": "S1"},
    ]


def test_split_divides_text_times_and_resets_translation():
    out = resegment.split_segment(_segs(), "a", offset=5)  # "hello" | " world"
    assert len(out) == 3
    left, right, _b = out
    assert left["orig_text"] == "hello" and right["orig_text"] == "world"
    assert [s["ord"] for s in out] == [0, 1, 2]  # renumbered
    assert left["segment_id"] == "a" and right["segment_id"] != "a"  # right gets a new id
    # times interpolated by offset (5/11 of 0..4)
    assert abs(left["ended_at"] - 4.0 * (5 / 11)) < 1e-6
    assert right["started_at"] == left["ended_at"]
    # translation no longer maps → reset on both halves
    assert left["trans_status"] == "skipped" and left["trans_text"] == ""
    assert right["trans_status"] == "skipped"


def test_split_empty_half_rejected():
    with pytest.raises(ValueError):
        resegment.split_segment(_segs(), "a", offset=0)


def test_merge_concatenates_and_spans():
    out = resegment.merge_with_next(_segs(), "a")
    assert len(out) == 1
    m = out[0]
    assert m["orig_text"] == "hello world again"
    assert m["trans_text"] == "你好世界 再次"
    assert m["trans_status"] == "final"  # both halves were final
    assert m["started_at"] == 0.0 and m["ended_at"] == 6.0
    assert m["ord"] == 0


def test_merge_last_segment_rejected():
    with pytest.raises(ValueError):
        resegment.merge_with_next(_segs(), "b")  # nothing after b


# ---------- endpoints ----------


def _seed(client, *, two=True):
    async def seed():
        s = store_mod.Store(Path(client.app.state.config.data.db_path))
        await s.open()
        await s.upsert_session(
            session_id="s1", title="T", created_at="2026-01-01T00:00:00",
            src_lang="en", tgt_lang="zh",
        )
        await s.upsert_segment_orig(
            session_id="s1", segment_id="a", ord_=0, started_at=0.0, ended_at=4.0,
            orig_text="hello world", orig_status="final", orig_lang="en",
        )
        if two:
            await s.upsert_segment_orig(
                session_id="s1", segment_id="b", ord_=1, started_at=4.0, ended_at=6.0,
                orig_text="again", orig_status="final", orig_lang="en",
            )
        await s.close()

    asyncio.run(seed())


def test_split_endpoint(client):
    _seed(client, two=False)
    r = client.post("/v1/sessions/s1/segments/a/split", json={"offset": 5})
    assert r.status_code == 200 and r.json()["n_segments"] == 2
    texts = [s["orig_text"] for s in client.get("/v1/sessions/s1").json()["segments"]]
    assert texts == ["hello", "world"]


def test_merge_endpoint(client):
    _seed(client, two=True)
    r = client.post("/v1/sessions/s1/segments/a/merge")
    assert r.status_code == 200 and r.json()["n_segments"] == 1
    assert client.get("/v1/sessions/s1").json()["segments"][0]["orig_text"] == "hello world again"


def test_split_bad_offset_400(client):
    _seed(client, two=False)
    assert client.post("/v1/sessions/s1/segments/a/split", json={}).status_code == 400


def test_merge_missing_session_404(client):
    assert client.post("/v1/sessions/nope/segments/a/merge").status_code == 404
