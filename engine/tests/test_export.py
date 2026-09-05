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
