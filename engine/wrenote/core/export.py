"""Transcript export formatters: plain text, Markdown, and SRT/VTT subtitles.

Pure functions over the session's segment dicts (as returned by
:meth:`wrenote.core.store.Store.get_session`) — no I/O, so they're trivially
unit-tested. ``content`` selects which text to emit:

* ``"original"``    — the transcribed text only
* ``"translation"`` — the translated text only (segments without a real
  translation are skipped)
* ``"both"``        — original, with the translation on a second line
"""
from __future__ import annotations

import re
from typing import Any

Segment = dict[str, Any]
Content = str  # "original" | "translation" | "both"

_FORMATS = {
    "txt": ("text/plain; charset=utf-8", "txt"),
    "md": ("text/markdown; charset=utf-8", "md"),
    "srt": ("application/x-subrip; charset=utf-8", "srt"),
    "vtt": ("text/vtt; charset=utf-8", "vtt"),
}


def _seg_texts(seg: Segment, content: Content) -> list[str]:
    """The line(s) of text to emit for one segment under ``content`` (possibly
    empty when there's nothing to show)."""
    orig = (seg.get("orig_text") or "").strip()
    trans = (seg.get("trans_text") or "").strip()
    trans_ok = bool(trans) and seg.get("trans_status") == "final"
    if content == "original":
        return [orig] if orig else []
    if content == "translation":
        return [trans] if trans_ok else []
    lines = []
    if orig:
        lines.append(orig)
    if trans_ok and trans != orig:
        lines.append(trans)
    return lines


def _speaker(seg: Segment) -> str:
    spk = (seg.get("speaker") or "").strip()
    return "" if spk in ("", "unknown") else spk


def _clock(seconds: float) -> str:
    """``m:ss`` (or ``h:mm:ss`` past an hour) — for Markdown/text headers."""
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _stamp(seconds: float, sep: str) -> str:
    """``HH:MM:SS<sep>mmm`` — ``sep`` is ',' for SRT, '.' for VTT."""
    ms = max(0, round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def to_plain_text(segments: list[Segment], content: Content) -> str:
    out: list[str] = []
    for seg in segments:
        texts = _seg_texts(seg, content)
        if not texts:
            continue
        spk = _speaker(seg)
        prefix = f"[{_clock(seg.get('started_at') or 0.0)}]"
        if spk:
            prefix += f" {spk}"
        out.append(f"{prefix}: {texts[0]}")
        out.extend(f"    {extra}" for extra in texts[1:])
    return "\n".join(out) + ("\n" if out else "")


def to_markdown(session: dict[str, Any], segments: list[Segment], content: Content) -> str:
    title = session.get("title") or "Untitled session"
    created = str(session.get("created_at") or "")[:19].replace("T", " ")
    src = session.get("src_lang") or ""
    tgt = session.get("tgt_lang") or ""
    lines = [f"# {title}", ""]
    meta = " · ".join(x for x in [created, f"{src} → {tgt}" if src and tgt else ""] if x)
    if meta:
        lines += [f"_{meta}_", ""]
    for seg in segments:
        texts = _seg_texts(seg, content)
        if not texts:
            continue
        spk = _speaker(seg)
        header = f"**[{_clock(seg.get('started_at') or 0.0)}]"
        header += f" {spk}**" if spk else "**"
        lines.append(header)
        lines.append(texts[0])
        lines.extend(f"> {extra}" for extra in texts[1:])
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _subtitle(segments: list[Segment], content: Content, *, vtt: bool) -> str:
    sep = "." if vtt else ","
    cues: list[str] = []
    idx = 1
    for seg in segments:
        texts = _seg_texts(seg, content)
        if not texts:
            continue
        start = float(seg.get("started_at") or 0.0)
        end = float(seg.get("ended_at") or 0.0)
        if end <= start:
            end = start + 1.0  # subtitles need a positive duration
        head = f"{_stamp(start, sep)} --> {_stamp(end, sep)}"
        body = "\n".join(texts)
        cues.append(f"{idx}\n{head}\n{body}" if not vtt else f"{head}\n{body}")
        idx += 1
    joined = "\n\n".join(cues)
    if vtt:
        return "WEBVTT\n\n" + joined + ("\n" if joined else "")
    return joined + ("\n" if joined else "")


def to_srt(segments: list[Segment], content: Content) -> str:
    return _subtitle(segments, content, vtt=False)


def to_vtt(segments: list[Segment], content: Content) -> str:
    return _subtitle(segments, content, vtt=True)


def with_minutes(text: str, minutes_md: str, fmt: str) -> str:
    """Put the minutes before the transcript. Markdown keeps its heading
    level (the document title stays the session's); plain text gets the
    same content without the marks."""
    if fmt == "md":
        lines = text.split("\n")
        # After the "# title" and the "_meta_" line, before the first segment.
        head_end = 0
        for i, line in enumerate(lines[:4]):
            if line.startswith("# ") or line.startswith("_") or line == "":
                head_end = i + 1
            else:
                break
        return "\n".join(lines[:head_end]) + "\n" + minutes_md + "\n---\n\n" + "\n".join(lines[head_end:])
    plain = re.sub(r"^#+\s*", "", minutes_md, flags=re.M)
    plain = plain.replace("- [ ] ", "- ")
    return plain + "\n" + ("-" * 40) + "\n\n" + text


def export_transcript(
    session: dict[str, Any], fmt: str, content: Content
) -> tuple[str, str, str]:
    """Return ``(text, mime_type, extension)`` for the requested format.

    ``fmt`` is one of txt/md/srt/vtt; raises ``ValueError`` otherwise.
    """
    if fmt not in _FORMATS:
        raise ValueError(f"unknown export format: {fmt!r}")
    segments = session.get("segments", []) or []
    if fmt == "srt":
        text = to_srt(segments, content)
    elif fmt == "vtt":
        text = to_vtt(segments, content)
    elif fmt == "md":
        text = to_markdown(session, segments, content)
    else:
        text = to_plain_text(segments, content)
    mime, ext = _FORMATS[fmt]
    return text, mime, ext
