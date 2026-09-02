"""Transcript segment text editing (Layer 1) + the stale-translation contract."""
from __future__ import annotations

import asyncio

import wrenote.core.store as store_mod
from wrenote.core.translation import translation_candidates


def test_translation_candidates_includes_stale():
    segs = [
        {"orig_text": "a", "trans_text": "x", "trans_status": "stale"},
        {"orig_text": "b", "trans_text": "y", "trans_status": "final"},
    ]
    cands = translation_candidates(segs, only_missing=True)
    assert [c["orig_text"] for c in cands] == ["a"]  # stale re-translated, final left alone


def _seed_one_segment():
    async def seed():
        s = store_mod.Store(store_mod.DEFAULT_DB_PATH)
        await s.open()
        await s.upsert_session(
            session_id="s1", title="T", created_at="2026-01-01T00:00:00",
            src_lang="en", tgt_lang="zh",
        )
        await s.upsert_segment_orig(
            session_id="s1", segment_id="a", ord_=0, started_at=0.0, ended_at=1.0,
            orig_text="helo", orig_status="final", orig_lang="en",
        )
        await s.upsert_segment_trans(
            session_id="s1", segment_id="a", ord_=0, trans_text="你好",
            trans_status="final", trans_lang="zh",
        )
        await s.close()

    asyncio.run(seed())


def test_edit_original_marks_translation_stale_but_keeps_it(client):
    _seed_one_segment()
    assert client.patch("/v1/sessions/s1/segments/a", json={"orig_text": "hello"}).status_code == 200
    seg = client.get("/v1/sessions/s1").json()["segments"][0]
    assert seg["orig_text"] == "hello"
    assert seg["trans_status"] == "stale"   # flagged for re-translation
    assert seg["trans_text"] == "你好"        # but kept (shown dimmed) until refreshed


def test_edit_translation_is_manual_final(client):
    _seed_one_segment()
    assert client.patch("/v1/sessions/s1/segments/a", json={"trans_text": "你好啊"}).status_code == 200
    seg = client.get("/v1/sessions/s1").json()["segments"][0]
    assert seg["trans_text"] == "你好啊"
    assert seg["trans_status"] == "final"


def test_edit_missing_segment_404(client):
    assert client.patch("/v1/sessions/nope/segments/x", json={"orig_text": "y"}).status_code == 404


def test_edit_requires_a_field_400(client):
    assert client.patch("/v1/sessions/any/segments/any", json={}).status_code == 400
