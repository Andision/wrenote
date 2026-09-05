"""Search (core/search.py, api/search.py) and the session list's pages.

The index is trigram FTS5, so the rules here are about its edges: Chinese
matches without a word list, a two-character query still finds things (by
another route), the search string is a phrase, a question becomes terms,
and the excerpts handed to the chat model are the hits with their
neighbours in order.
"""
from __future__ import annotations

import pytest

from wrenote.core import search as s
from wrenote.core.store import Store
from wrenote.core.transcript import MAX_TRANSCRIPT_CHARS, build_chat_system_prompt


class TestQueries:
    def test_phrase_query_quotes_and_needs_three_chars(self):
        assert s.phrase_query("budget review") == '"budget review"'
        assert s.phrase_query('say "hi"') == '"say ""hi"""'
        assert s.phrase_query("预算") is None
        assert s.phrase_query("  ab ") is None

    def test_like_pattern_escapes(self):
        assert s.like_pattern("50%_off") == "%50\\%\\_off%"

    def test_terms_query_from_a_question(self):
        q = s.terms_query("What did we decide about the Q3 budget?")
        assert q == '"what" OR "did" OR "decide" OR "about" OR "the" OR "budget"'
        assert s.terms_query("我们讨论了预算吗") == '"我们讨" OR "们讨论" OR "讨论了" OR "论了预" OR "了预算" OR "预算吗"'
        assert s.terms_query("预算") is None
        assert s.terms_query("") is None

    def test_excerpts_are_hits_with_neighbours_in_order(self):
        segs = [{"segment_id": str(i), "ord": i} for i in range(10)]
        hits = [{"segment_id": "7"}, {"segment_id": "2"}, {"segment_id": "zzz"}]
        assert [x["segment_id"] for x in s.pick_excerpts(hits, segs)] == ["1", "2", "3", "6", "7", "8"]
        assert s.pick_excerpts([{"segment_id": "0"}], segs, around=0) == [segs[0]]


@pytest.fixture
async def store(tmp_path):
    st = Store(tmp_path / "data.db")
    await st.open()
    for sid, title, created in (("s1", "Budget review", "2026-01-02T00:00:00"),
                                ("s2", "Standup", "2026-01-01T00:00:00")):
        await st.upsert_session(session_id=sid, title=title, created_at=created, src_lang="en", tgt_lang="zh")
    rows = [
        ("s1", "a", 0, "We need to cut the marketing budget by ten percent.", "我们需要把市场预算削减百分之十。"),
        ("s1", "b", 1, "Agreed, let's revisit in March.", "同意，三月再看。"),
        ("s2", "c", 0, "Nothing about money today.", ""),
    ]
    for sid, seg, ord_, orig, trans in rows:
        await st.upsert_segment_orig(session_id=sid, segment_id=seg, ord_=ord_, started_at=float(ord_),
                                     ended_at=ord_ + 1.0, orig_text=orig, orig_status="final")
        await st.upsert_segment_trans(session_id=sid, segment_id=seg, ord_=ord_, trans_text=trans,
                                      trans_status="final" if trans else "skipped", trans_lang="zh")
    yield st
    await st.close()


class TestStoreSearch:
    async def test_english_and_chinese_hits(self, store):
        hits = await store.search_segments(s.phrase_query("marketing budget"))
        assert [(h["session_id"], h["segment_id"]) for h in hits] == [("s1", "a")]
        assert hits[0]["session_title"] == "Budget review"
        assert hits[0]["orig_text"].startswith("We need")
        zh = await store.search_segments(s.phrase_query("市场预算"))
        assert [h["segment_id"] for h in zh] == ["a"]

    async def test_short_query_falls_back_to_like(self, store):
        hits = await store.search_segments("", like=s.like_pattern("预算"))
        assert [h["segment_id"] for h in hits] == ["a"]

    async def test_scoped_to_a_session(self, store):
        assert {h["segment_id"] for h in await store.search_segments('"money" OR "budget"')} == {"a", "c"}
        assert [h["segment_id"] for h in await store.search_segments('"money" OR "budget"', session_id="s2")] == ["c"]

    async def test_titles(self, store):
        assert [r["id"] for r in await store.search_session_titles(s.like_pattern("budget"))] == ["s1"]


class TestListPages:
    async def test_keyset_pages_do_not_skip_or_repeat(self, tmp_path):
        st = Store(tmp_path / "data.db")
        await st.open()
        try:
            for i in range(7):
                await st.upsert_session(session_id=f"s{i}", title=str(i), created_at=f"2026-01-0{i + 1}T00:00:00",
                                        src_lang="en", tgt_lang="zh")
            first = await st.list_sessions(limit=3)
            assert [r["id"] for r in first] == ["s6", "s5", "s4"]
            second = await st.list_sessions(limit=3, before=(first[-1]["created_at"], first[-1]["id"]))
            assert [r["id"] for r in second] == ["s3", "s2", "s1"]
            third = await st.list_sessions(limit=3, before=(second[-1]["created_at"], second[-1]["id"]))
            assert [r["id"] for r in third] == ["s0"]
        finally:
            await st.close()


class TestChatPrompt:
    def test_excerpts_only_when_trimmed(self):
        short = [{"segment_id": "a", "started_at": 0.0, "orig_text": "hi"}]
        assert "RELEVANT EXCERPTS" not in build_chat_system_prompt(short, short)
        long_ = [{"segment_id": str(i), "started_at": float(i), "orig_text": "x" * 200} for i in range(MAX_TRANSCRIPT_CHARS // 200 + 50)]
        prompt = build_chat_system_prompt(long_, [long_[0]])
        assert "=== RELEVANT EXCERPTS ===" in prompt and "=== RECENT TRANSCRIPT ===" in prompt
        assert prompt.index("[0.0s]") < prompt.index("=== RECENT TRANSCRIPT ===")


class TestApi:
    def _seed(self, client):
        store = client.app.state.store

        async def seed():
            await store.upsert_session(session_id="s1", title="Budget review", created_at="2026-01-02T00:00:00",
                                       src_lang="en", tgt_lang="zh")
            await store.upsert_segment_orig(session_id="s1", segment_id="a", ord_=0, started_at=0.0, ended_at=1.0,
                                            orig_text="cut the marketing budget", orig_status="final")
        client.portal.call(seed)

    def test_search_hits_and_titles(self, client):
        self._seed(client)
        body = client.get("/v1/search?q=marketing").json()
        assert body["segments"][0]["segment_id"] == "a"
        assert body["segments"][0]["orig_text"] == "cut the marketing budget"
        assert [r["id"] for r in body["sessions"]] == []
        assert [r["id"] for r in client.get("/v1/search?q=budget").json()["sessions"]] == ["s1"]
        assert client.get("/v1/search?q=ma").json()["segments"][0]["segment_id"] == "a"

    def test_search_validation(self, client):
        assert client.get("/v1/search").status_code == 422
        assert client.get("/v1/search?q=%20%20").status_code == 400
        assert client.get("/v1/search?q=x&session_id=../etc").status_code == 400

    def test_sessions_pages(self, client):
        self._seed(client)
        store = client.app.state.store

        async def more():
            await store.upsert_session(session_id="s2", title="Two", created_at="2026-01-03T00:00:00",
                                       src_lang="en", tgt_lang="zh")
        client.portal.call(more)
        page = client.get("/v1/sessions?limit=1").json()
        assert [r["id"] for r in page["sessions"]] == ["s2"] and page["next_cursor"]
        page2 = client.get(f"/v1/sessions?limit=1&cursor={page['next_cursor']}").json()
        assert [r["id"] for r in page2["sessions"]] == ["s1"] and page2["next_cursor"] is None
        assert client.get("/v1/sessions?limit=1&cursor=garbage").status_code == 400
        assert len(client.get("/v1/sessions").json()["sessions"]) == 2
