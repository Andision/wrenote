"""Meeting minutes (core/minutes.py, api/minutes.py).

The model is a function here, so the rules are testable without one: what
we ask for, how a reply is read (strict JSON, fenced JSON, prose around
JSON, no JSON at all), how a long transcript is split and merged, what the
rendered document looks like, and the API's answers.
"""
from __future__ import annotations

import json

import pytest

from wrenote.chat.base import ChatMessage
from wrenote.core import minutes as m

SEGS = [
    {"segment_id": "a", "ord": 0, "started_at": 0.0, "orig_text": "Let's start with the launch date.", "speaker": "Alice"},
    {"segment_id": "b", "ord": 1, "started_at": 65.0, "orig_text": "We agreed on March 3rd. Bob owns the release notes.", "speaker": "Bob"},
    {"segment_id": "c", "ord": 2, "started_at": 70.0, "orig_text": "", "speaker": None},
]

DOC = {
    "summary": "A short planning call.",
    "key_points": ["Launch date", "Release notes"],
    "decisions": ["Launch on March 3rd"],
    "action_items": [{"text": "Write the release notes", "owner": "Bob", "due": "March 1"}],
    "open_questions": [],
}


class TestTranscript:
    def test_lines_carry_time_and_speaker_and_skip_empty(self):
        assert m.transcript_lines(SEGS) == [
            "[0:00 Alice] Let's start with the launch date.",
            "[1:05 Bob] We agreed on March 3rd. Bob owns the release notes.",
        ]

    def test_hash_follows_the_text_only(self):
        a = m.transcript_hash(SEGS)
        assert a == m.transcript_hash([{**s, "speaker": "X"} for s in SEGS])
        assert a != m.transcript_hash([{**SEGS[0], "orig_text": "changed"}, *SEGS[1:]])

    def test_chunks_never_split_a_line(self):
        lines = [f"line {i} " + "x" * 90 for i in range(10)]
        chunks = m.chunk_lines(lines, max_chars=250)
        assert [len(c.split("\n")) for c in chunks] == [2, 2, 2, 2, 2]
        assert "\n".join(chunks) == "\n".join(lines)


class TestParseReply:
    def test_strict_json(self):
        assert m.parse_reply(json.dumps(DOC)) == DOC

    def test_fenced_json_with_prose_around(self):
        text = "Here are the minutes:\n```json\n" + json.dumps(DOC) + "\n```\nHope this helps."
        assert m.parse_reply(text) == DOC

    def test_json_inside_prose_without_a_fence(self):
        text = "Sure. " + json.dumps(DOC) + " Let me know."
        assert m.parse_reply(text) == DOC

    def test_no_json_becomes_a_summary(self):
        doc = m.parse_reply("The team met and agreed to launch in March.")
        assert doc["summary"] == "The team met and agreed to launch in March."
        assert doc["decisions"] == [] and doc["action_items"] == []

    def test_loose_shapes_are_coerced(self):
        doc = m.parse_reply(json.dumps({
            "summary": ["Two", "sentences."],
            "keyPoints": [{"point": "A"}, "B", ""],
            "decisions": "Just one",
            "actions": ["Do X", {"task": "Do Y", "assignee": "Ann", "deadline": "null"}, {"text": ""}],
        }))
        assert doc["summary"] == "Two sentences."
        assert doc["key_points"] == ["A", "B"]
        assert doc["decisions"] == ["Just one"]
        assert doc["action_items"] == [
            {"text": "Do X", "owner": None, "due": None},
            {"text": "Do Y", "owner": "Ann", "due": None},
        ]


class TestMarkdown:
    def test_headings_follow_the_language(self):
        zh = m.to_markdown(DOC, "zh")
        en = m.to_markdown(DOC, "en", title="Planning")
        assert zh.startswith("## 会议纪要") and "### 待办事项" in zh and "负责人: Bob" in zh
        assert en.startswith("# Planning") and "- [ ] Write the release notes (Owner: Bob; Due: March 1)" in en

    def test_empty_sections_are_left_out(self):
        text = m.to_markdown({**DOC, "decisions": [], "open_questions": []}, "en")
        assert "Decisions" not in text and "Open questions" not in text


class TestWriteMinutes:
    async def test_one_call_for_a_short_transcript(self):
        calls: list[list[ChatMessage]] = []

        async def complete(messages):
            calls.append(messages)
            return json.dumps(DOC)

        doc = await m.write_minutes(SEGS, lang="zh", complete=complete)
        assert doc == DOC
        assert len(calls) == 1
        system, user = calls[0]
        assert "Write everything in Chinese" in system.content
        assert "[1:05 Bob]" in user.content

    async def test_long_transcript_is_mapped_then_merged(self):
        segs = [{"ord": i, "started_at": float(i), "orig_text": f"Point number {i} " + "x" * 60} for i in range(40)]
        calls: list[str] = []
        progress: list[float] = []

        async def complete(messages):
            user = messages[-1].content
            calls.append(user)
            if user.startswith("Below are minutes"):
                return json.dumps({**DOC, "summary": "merged"})
            return json.dumps({"summary": user[:20], "decisions": ["d"]})

        doc = await m.write_minutes(
            segs, lang="en", complete=complete, chunk_chars=800,
            on_progress=lambda f, _line: progress.append(f),
        )
        assert doc["summary"] == "merged"
        assert len(calls) > 2 and calls[-1].startswith("Below are minutes")
        assert all("part " in c.lower() for c in calls[:-1])
        assert progress[0] == 0.0 and progress[-1] == 1.0

    async def test_a_merge_the_model_fumbles_still_keeps_every_part(self):
        segs = [{"ord": i, "started_at": float(i), "orig_text": "y" * 100} for i in range(10)]

        async def complete(messages):
            user = messages[-1].content
            if user.startswith("Below are minutes"):
                return ""  # nothing usable
            return json.dumps({"summary": "part", "decisions": ["one"]})

        doc = await m.write_minutes(segs, lang="en", complete=complete, chunk_chars=400)
        assert doc["decisions"] and all(d == "one" for d in doc["decisions"])
        assert doc["summary"].count("part") == len(doc["decisions"])

    async def test_empty_transcript_is_refused(self):
        async def complete(messages):
            raise AssertionError("must not be called")
        with pytest.raises(ValueError):
            await m.write_minutes([{"orig_text": ""}], lang="en", complete=complete)


def _record(client, sid="s1"):
    with client.websocket_connect("/v1/ws") as ws:
        ws.send_json({"type": "start", "config": {"session_id": sid, "title": "Planning", "tgt": "zh"}})
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "stop"})


class TestApi:
    def test_missing_session_404(self, client):
        assert client.get("/v1/sessions/nope/minutes").status_code == 404
        assert client.post("/v1/sessions/nope/minutes").status_code == 404

    def test_no_transcript_400(self, client):
        _record(client)
        r = client.post("/v1/sessions/s1/minutes")
        assert r.status_code == 400 and r.json()["detail"] == "no_transcript"
        assert client.get("/v1/sessions/s1/minutes").json() == {"minutes": [], "job_id": None, "job_lang": None}

    def test_bad_lang_400(self, client):
        _record(client)
        assert client.post("/v1/sessions/s1/minutes", json={"lang": "zh; drop"}).status_code == 400
        assert client.get("/v1/sessions/s1/minutes/markdown?lang=x/y").status_code == 400

    def test_markdown_and_export_with_stored_minutes(self, client):
        _record(client)
        store = client.app.state.store

        async def seed():
            await store.upsert_segment_orig(
                session_id="s1", segment_id="a", ord_=0, started_at=0.0, ended_at=1.0,
                orig_text="hello", orig_status="final",
            )
            await store.upsert_minutes(
                session_id="s1", lang="zh", content=json.dumps(DOC), generated_at="2026-01-01T00:00:00",
                model="mock", transcript_hash="old",
            )
        # The store is async; the TestClient's portal runs a coroutine on its loop.
        client.portal.call(seed)

        got = client.get("/v1/sessions/s1/minutes").json()
        assert got["minutes"][0]["lang"] == "zh" and got["minutes"][0]["stale"] is True
        assert got["minutes"][0]["content"] == DOC

        md = client.get("/v1/sessions/s1/minutes/markdown?lang=zh")
        assert md.status_code == 200 and md.text.startswith("# Planning\n\n### 摘要")
        assert client.get("/v1/sessions/s1/minutes/markdown?lang=en").status_code == 404

        exp = client.get("/v1/sessions/s1/export?fmt=md&content=original&minutes=zh").text
        assert exp.index("会议纪要") < exp.index("hello")
        txt = client.get("/v1/sessions/s1/export?fmt=txt&content=original&minutes=zh").text
        assert "###" not in txt and "待办事项" in txt and "- Write the release notes" in txt
        assert client.get("/v1/sessions/s1/export?fmt=srt&minutes=zh").status_code == 400
        assert client.get("/v1/sessions/s1/export?fmt=md&minutes=en").status_code == 404

        assert client.delete("/v1/sessions/s1/minutes?lang=zh").json()["status"] == "ok"
        assert client.get("/v1/sessions/s1/minutes").json()["minutes"] == []
