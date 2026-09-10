"""Transcript export formatters + endpoint guards."""
from __future__ import annotations

import pytest

from wrenote.core import export

SEGS = [
    {
        "segment_id": "a", "ord": 0, "started_at": 1.0, "ended_at": 3.5,
        "orig_text": "Hello there", "orig_status": "final", "orig_lang": "en",
        "trans_text": "你好", "trans_status": "final", "trans_lang": "zh",
        "speaker": "Speaker 1",
    },
    {
        "segment_id": "b", "ord": 1, "started_at": 3.5, "ended_at": 5.0,
        "orig_text": "How are you", "orig_status": "final", "orig_lang": "en",
        "trans_text": "", "trans_status": "skipped", "trans_lang": "zh",
        "speaker": "unknown",
    },
]
SESSION = {
    "title": "Daily sync", "created_at": "2026-06-03T09:30:00",
    "src_lang": "en", "tgt_lang": "zh", "segments": SEGS,
}


def test_srt_original_timestamps_and_numbering():
    srt = export.to_srt(SEGS, "original")
    assert "1\n00:00:01,000 --> 00:00:03,500\nHello there" in srt
    assert "2\n00:00:03,500 --> 00:00:05,000\nHow are you" in srt


def test_srt_translation_skips_untranslated():
    srt = export.to_srt(SEGS, "translation")
    assert "你好" in srt
    assert "How are you" not in srt  # segment b has no real translation
    assert srt.startswith("1\n")  # renumbered from 1, not 2


def test_vtt_header_and_dot_separator():
    vtt = export.to_vtt(SEGS, "original")
    assert vtt.startswith("WEBVTT\n\n")
    assert "00:00:01.000 --> 00:00:03.500" in vtt  # '.' not ','


def test_markdown_has_title_speaker_and_translation_blockquote():
    md = export.to_markdown(SESSION, SEGS, "both")
    assert md.startswith("# Daily sync")
    assert "en → zh" in md
    assert "**[00:01] Speaker 1**" in md
    assert "Hello there" in md
    assert "> 你好" in md  # translation as a blockquote under the original
    assert "unknown" not in md  # the "unknown" speaker is suppressed


def test_plain_text_shape():
    txt = export.to_plain_text(SEGS, "original")
    assert "[00:01] Speaker 1: Hello there" in txt
    assert "[00:03]: How are you" in txt  # no speaker prefix for "unknown"


def test_both_includes_orig_and_trans_lines():
    assert export.to_plain_text(SEGS[:1], "both") == "[00:01] Speaker 1: Hello there\n    你好\n"


def test_export_transcript_dispatch_and_bad_format():
    text, mime, ext = export.export_transcript(SESSION, "srt", "original")
    assert ext == "srt" and "x-subrip" in mime and "Hello there" in text
    with pytest.raises(ValueError):
        export.export_transcript(SESSION, "docx", "both")


def test_empty_segments_produce_empty_output():
    assert export.to_srt([], "original") == ""
    assert export.to_vtt([], "original") == "WEBVTT\n\n"


# ---------- endpoint guards (formatting itself is covered above) ----------


def test_export_missing_session_404(client):
    assert client.get("/v1/sessions/nope/export?fmt=md").status_code == 404


def test_export_bad_content_400(client):
    # content is validated before the session lookup, so any sid works.
    assert client.get("/v1/sessions/any/export?content=bogus").status_code == 400


def test_export_happy_path_through_http(client):
    """Seed a real session+segment into the app's DB, then export it over HTTP —
    exercises the one link the unit tests don't: get_session → export → response."""
    import asyncio
    from pathlib import Path

    import wrenote.core.store as store_mod

    async def seed():
        # The same temp DB the app uses.
        s = store_mod.Store(Path(client.app.state.config.data.db_path))
        await s.open()
        await s.upsert_session(
            session_id="s1", title="Demo", created_at="2026-06-03T09:00:00",
            src_lang="en", tgt_lang="zh",
        )
        await s.upsert_segment_orig(
            session_id="s1", segment_id="a", ord_=0, started_at=1.0, ended_at=3.0,
            orig_text="Hello world", orig_status="final", orig_lang="en", speaker="Speaker 1",
        )
        await s.close()

    asyncio.run(seed())

    r = client.get("/v1/sessions/s1/export?fmt=srt&content=original")
    assert r.status_code == 200
    assert "00:00:01,000 --> 00:00:03,000" in r.text
    assert "Hello world" in r.text

    md = client.get("/v1/sessions/s1/export?fmt=md&content=original")
    assert md.status_code == 200
    assert md.text.startswith("# Demo")


# ---------- saving, rather than handing the client a blob ----------------


def _seed(client, sid: str = "s1", title: str = "Demo") -> None:
    import asyncio
    from pathlib import Path

    import wrenote.core.store as store_mod

    async def seed():
        s = store_mod.Store(Path(client.app.state.config.data.db_path))
        await s.open()
        await s.upsert_session(
            session_id=sid, title=title, created_at="2026-06-03T09:00:00",
            src_lang="en", tgt_lang="zh",
        )
        await s.upsert_segment_orig(
            session_id=sid, segment_id="a", ord_=0, started_at=1.0, ended_at=3.0,
            orig_text="Hello world", orig_status="final", orig_lang="en",
        )
        await s.close()

    asyncio.run(seed())


class TestSave:
    """The client used to save an export with a blob download, which in a
    WebView lands somewhere it can neither choose nor name — so the user got a
    file and no idea whether, or where. The engine is local, so it writes the
    file and answers with the path."""

    def test_writes_the_file_and_answers_with_the_path(self, client):
        from pathlib import Path

        _seed(client)
        r = client.post("/v1/sessions/s1/export/save", json={"fmt": "md", "content": "original"})
        assert r.status_code == 200
        body = r.json()
        path = Path(body["path"])
        assert path.is_file() and path.read_text(encoding="utf-8").startswith("# Demo")
        assert body["filename"] == "Demo.md"
        assert body["dir"] == client.app.state.config.data.exports_dir
        assert body["bytes"] == len(path.read_bytes())

    def test_saving_twice_keeps_both(self, client):
        _seed(client)
        first = client.post("/v1/sessions/s1/export/save", json={"fmt": "txt"}).json()
        second = client.post("/v1/sessions/s1/export/save", json={"fmt": "txt"}).json()
        assert first["filename"] == "Demo.txt"
        assert second["filename"] == "Demo (2).txt"

    def test_a_title_that_is_not_a_filename(self, client):
        """Session titles are free text — a slash in one must not write
        outside the exports directory."""
        from pathlib import Path

        _seed(client, title="../../etc/passwd: notes")
        body = client.post("/v1/sessions/s1/export/save", json={"fmt": "txt"}).json()
        path = Path(body["path"])
        assert path.parent == Path(client.app.state.config.data.exports_dir)
        assert "/" not in path.name and ":" not in path.name

    def test_an_untitled_session_still_gets_a_name(self, client):
        _seed(client, title="")
        body = client.post("/v1/sessions/s1/export/save", json={"fmt": "txt"}).json()
        # Falls back to the session id, not to an empty name.
        assert body["filename"] == "s1.txt"

    def test_the_same_guards_as_the_GET(self, client):
        assert client.post("/v1/sessions/nope/export/save", json={"fmt": "md"}).status_code == 404
        assert client.post(
            "/v1/sessions/any/export/save", json={"content": "bogus"}
        ).status_code == 400
        _seed(client)
        assert client.post(
            "/v1/sessions/s1/export/save", json={"fmt": "srt", "minutes": "zh"}
        ).status_code == 400

    def test_the_directory_is_a_config_key(self, client):
        assert client.app.state.config.data.exports_dir.endswith("exports")
        assert (
            client.get("/v1/info").json()["paths"]["exports_dir"]
            == client.app.state.config.data.exports_dir
        )
