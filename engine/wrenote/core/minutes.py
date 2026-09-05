"""Meeting minutes: what the chat model can write from a transcript.

A transcript is the record; the minutes are what people actually read
afterwards — what was discussed, what was decided, who has to do what. The
chat model already sees the transcript for questions; this asks it, once,
for a fixed document and keeps the answer in the store, per language.

The model is asked for JSON with a fixed shape (:data:`SECTIONS`). Small
models mostly comply and sometimes wrap it in prose or a code fence, so the
parse is lenient: the first ``{…}`` block is taken, and if there is none the
whole reply becomes the summary rather than nothing at all. A transcript
longer than the model's window is done in pieces — minutes per chunk, then
one more call to merge them — which is the plain map-reduce every summariser
ends up with.

What comes out is stored as JSON; :func:`to_markdown` renders it for export
and the clipboard with headings in the minutes' own language.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from ..chat.base import ChatBackend, ChatMessage
from .jobs import JobRegistry, Phase
from .store import Store

log = logging.getLogger(__name__)

# The document. Lists are of strings, except action_items (see ACTION_KEYS).
SECTIONS = ("summary", "key_points", "decisions", "action_items", "open_questions")
ACTION_KEYS = ("text", "owner", "due")

# Per call to the model. CJK runs ~1.5 tokens per character, so 16k chars is
# ~24k tokens at worst — inside the chat model's 32k window with the prompt
# and the answer. Latin text is far under that.
CHUNK_CHARS = 16_000

MINUTES_PHASES = [
    Phase("load_model", 0.05),
    Phase("summarize", 0.83),
    Phase("merge", 0.10),
    Phase("persist", 0.02),
]

_LANG_NAMES = {
    "en": "English", "zh": "Chinese", "ja": "Japanese", "ko": "Korean",
    "es": "Spanish", "fr": "French", "de": "German", "ru": "Russian",
    "pt": "Portuguese", "it": "Italian",
}

# Headings for the rendered document. Document content, not UI: it travels
# with the text into a file, so it is in the minutes' language, not the
# interface's.
HEADINGS: dict[str, dict[str, str]] = {
    "en": {
        "title": "Meeting minutes", "summary": "Summary", "key_points": "Key points",
        "decisions": "Decisions", "action_items": "Action items",
        "open_questions": "Open questions", "owner": "Owner", "due": "Due",
    },
    "zh": {
        "title": "会议纪要", "summary": "摘要", "key_points": "要点",
        "decisions": "决定事项", "action_items": "待办事项",
        "open_questions": "待定问题", "owner": "负责人", "due": "期限",
    },
}

_SYSTEM = (
    "You write meeting minutes from a transcript. Reply with one JSON object "
    "and nothing else — no prose before or after, no code fence. Keys:\n"
    '  "summary": a paragraph (3–6 sentences) of what the meeting was about and how it went;\n'
    '  "key_points": list of strings — the main things discussed, in order;\n'
    '  "decisions": list of strings — only things actually decided or agreed;\n'
    '  "action_items": list of objects {"text", "owner", "due"} — tasks someone took on; '
    'owner and due are null when the transcript does not say;\n'
    '  "open_questions": list of strings — raised but not settled.\n'
    "Use an empty list when a section has nothing. Do not invent names, dates or "
    "decisions that are not in the transcript. Write everything in {language}."
)

_USER = "Transcript:\n\n{transcript}"

_USER_CHUNK = (
    "This is part {index} of {count} of a longer transcript. Write the minutes for "
    "this part only.\n\nTranscript (part {index}/{count}):\n\n{transcript}"
)

_USER_MERGE = (
    "Below are minutes written separately for {count} consecutive parts of one "
    "meeting, as JSON. Merge them into one JSON object of the same shape for the "
    "whole meeting: one summary paragraph, deduplicated lists in meeting order, "
    "every action item kept.\n\n{parts}"
)


def transcript_hash(segments: list[dict[str, Any]]) -> str:
    """Identity of the text the minutes were written from."""
    h = hashlib.sha1()
    for s in segments:
        h.update((s.get("orig_text") or "").strip().encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()


def transcript_lines(segments: list[dict[str, Any]]) -> list[str]:
    """One line per spoken segment: ``[m:ss Speaker] text``."""
    lines: list[str] = []
    for s in segments:
        text = (s.get("orig_text") or "").strip()
        if not text:
            continue
        t = float(s.get("started_at") or 0.0)
        stamp = f"{int(t // 60)}:{int(t % 60):02d}"
        spk = s.get("speaker") or ""
        lines.append(f"[{stamp}{' ' + spk if spk else ''}] {text}")
    return lines


def chunk_lines(lines: list[str], max_chars: int = CHUNK_CHARS) -> list[str]:
    """Join lines into pieces of at most ``max_chars``, never splitting a line."""
    chunks: list[str] = []
    cur: list[str] = []
    size = 0
    for line in lines:
        if cur and size + len(line) + 1 > max_chars:
            chunks.append("\n".join(cur))
            cur, size = [], 0
        cur.append(line)
        size += len(line) + 1
    if cur:
        chunks.append("\n".join(cur))
    return chunks


def empty_minutes() -> dict[str, Any]:
    return {"summary": "", "key_points": [], "decisions": [], "action_items": [], "open_questions": []}


def _str_list(v: Any) -> list[str]:
    if isinstance(v, str):
        return [v.strip()] if v.strip() else []
    if not isinstance(v, list):
        return []
    out: list[str] = []
    for item in v:
        if isinstance(item, str):
            if item.strip():
                out.append(item.strip())
        elif isinstance(item, dict):
            text = str(item.get("text") or item.get("point") or item.get("decision") or "").strip()
            if text:
                out.append(text)
    return out


def _action_items(v: Any) -> list[dict[str, Any]]:
    if not isinstance(v, list):
        return []
    out: list[dict[str, Any]] = []
    for item in v:
        if isinstance(item, str):
            if item.strip():
                out.append({"text": item.strip(), "owner": None, "due": None})
        elif isinstance(item, dict):
            text = str(item.get("text") or item.get("task") or item.get("action") or "").strip()
            if not text:
                continue
            owner = item.get("owner") or item.get("assignee")
            due = item.get("due") or item.get("deadline")
            out.append({
                "text": text,
                "owner": str(owner).strip() if owner and str(owner).strip().lower() not in ("null", "none") else None,
                "due": str(due).strip() if due and str(due).strip().lower() not in ("null", "none") else None,
            })
    return out


def normalize(doc: Any) -> dict[str, Any]:
    """Coerce whatever the model returned into the fixed shape."""
    out = empty_minutes()
    if not isinstance(doc, dict):
        return out
    summary = doc.get("summary")
    if isinstance(summary, list):
        summary = " ".join(str(x) for x in summary)
    out["summary"] = str(summary or "").strip()
    out["key_points"] = _str_list(doc.get("key_points") or doc.get("keyPoints") or doc.get("topics"))
    out["decisions"] = _str_list(doc.get("decisions"))
    out["action_items"] = _action_items(doc.get("action_items") or doc.get("actionItems") or doc.get("actions"))
    out["open_questions"] = _str_list(doc.get("open_questions") or doc.get("openQuestions") or doc.get("questions"))
    return out


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def parse_reply(text: str) -> dict[str, Any]:
    """The model's reply → a minutes document.

    Takes the JSON from a code fence or the first balanced ``{…}``; a reply
    with no usable JSON becomes a summary-only document, so the user gets
    the model's words rather than an error.
    """
    text = text.strip()
    if not text:
        return empty_minutes()
    candidates: list[str] = []
    m = _FENCE.search(text)
    if m:
        candidates.append(m.group(1))
    start = text.find("{")
    if start >= 0:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    candidates.append(text[start : i + 1])
                    break
    for cand in candidates:
        try:
            doc = json.loads(cand)
        except json.JSONDecodeError:
            continue
        if isinstance(doc, dict):
            return normalize(doc)
    fallback = empty_minutes()
    fallback["summary"] = text
    return fallback


def is_empty(doc: dict[str, Any]) -> bool:
    return not any(doc.get(k) for k in SECTIONS)


def to_markdown(doc: dict[str, Any], lang: str, *, title: str | None = None) -> str:
    """Render the document; headings follow the minutes' language."""
    h = HEADINGS.get(lang.split("-")[0].lower(), HEADINGS["en"])
    lines: list[str] = [f"## {h['title']}" if title is None else f"# {title}", ""]
    if doc.get("summary"):
        lines += [f"### {h['summary']}", "", doc["summary"], ""]
    for key in ("key_points", "decisions"):
        if doc.get(key):
            lines += [f"### {h[key]}", ""]
            lines += [f"- {x}" for x in doc[key]]
            lines.append("")
    if doc.get("action_items"):
        lines += [f"### {h['action_items']}", ""]
        for a in doc["action_items"]:
            tail = []
            if a.get("owner"):
                tail.append(f"{h['owner']}: {a['owner']}")
            if a.get("due"):
                tail.append(f"{h['due']}: {a['due']}")
            lines.append(f"- [ ] {a['text']}" + (f" ({'; '.join(tail)})" if tail else ""))
        lines.append("")
    if doc.get("open_questions"):
        lines += [f"### {h['open_questions']}", ""]
        lines += [f"- {x}" for x in doc["open_questions"]]
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


Complete = Callable[[list[ChatMessage]], Awaitable[str]]


async def complete_with(backend: ChatBackend, messages: list[ChatMessage]) -> str:
    """One full (non-streamed) reply from the chat backend."""
    parts: list[str] = []
    chunks = await backend.chat(messages, max_tokens=1800, temperature=0.2)
    async for piece in chunks:
        parts.append(piece)
    return "".join(parts)


async def write_minutes(
    segments: list[dict[str, Any]],
    *,
    lang: str,
    complete: Complete,
    on_progress: Callable[[float, str | None], None] | None = None,
    chunk_chars: int = CHUNK_CHARS,
) -> dict[str, Any]:
    """Minutes for ``segments`` in ``lang``, via ``complete`` (the model).

    Raises ``ValueError`` when there is no transcript. ``on_progress`` gets
    (fraction, log line) as chunks finish; the merge step is reported at 1.0.
    """
    lines = transcript_lines(segments)
    if not lines:
        raise ValueError("no transcript")
    language = _LANG_NAMES.get(lang.split("-")[0].lower(), lang)
    system = ChatMessage(role="system", content=_SYSTEM.replace("{language}", language))
    chunks = chunk_lines(lines, chunk_chars)

    def report(frac: float, line: str | None = None) -> None:
        if on_progress is not None:
            on_progress(frac, line)

    if len(chunks) == 1:
        report(0.0, "Writing minutes")
        reply = await complete([system, ChatMessage(role="user", content=_USER.format(transcript=chunks[0]))])
        report(1.0, None)
        return parse_reply(reply)

    parts: list[dict[str, Any]] = []
    for i, chunk in enumerate(chunks, start=1):
        report((i - 1) / len(chunks), f"Part {i}/{len(chunks)}")
        user = ChatMessage(
            role="user",
            content=_USER_CHUNK.format(index=i, count=len(chunks), transcript=chunk),
        )
        parts.append(parse_reply(await complete([system, user])))
    report(1.0, "Merging parts")
    merge_user = ChatMessage(
        role="user",
        content=_USER_MERGE.format(
            count=len(parts),
            parts="\n\n".join(json.dumps(p, ensure_ascii=False) for p in parts),
        ),
    )
    merged = parse_reply(await complete([system, merge_user]))
    if is_empty(merged):
        # The merge failed to parse: concatenating the parts loses only
        # deduplication, never content.
        merged = empty_minutes()
        merged["summary"] = "\n\n".join(p["summary"] for p in parts if p["summary"])
        for key in ("key_points", "decisions", "action_items", "open_questions"):
            for p in parts:
                merged[key].extend(p[key])
    return merged


def row_to_public(row: dict[str, Any], current_hash: str) -> dict[str, Any]:
    """A stored row as the API returns it: parsed content plus staleness."""
    try:
        content = normalize(json.loads(row["content"]))
    except (json.JSONDecodeError, TypeError):
        content = empty_minutes()
    return {
        "lang": row["lang"],
        "content": content,
        "generated_at": row["generated_at"],
        "model": row.get("model") or "",
        "stale": row.get("transcript_hash", "") != current_hash,
    }


async def run_job(
    *,
    job_id: str,
    registry: JobRegistry,
    session: dict[str, Any],
    lang: str,
    backend_loader: Callable[[], Awaitable[ChatBackend]],
    store: Store,
) -> dict[str, Any]:
    """The job body: load the model, write, persist. Caller owns the job's end."""
    sid = session["id"]
    segments = list(session.get("segments") or [])
    registry.advance(job_id, phase_idx=0, phase_inner=0.0, log_line="Loading chat model")
    backend = await backend_loader()
    registry.advance(job_id, phase_inner=1.0)

    registry.advance(job_id, phase_idx=1, phase_inner=0.0)

    async def complete(messages: list[ChatMessage]) -> str:
        return await complete_with(backend, messages)

    doc = await write_minutes(
        segments,
        lang=lang,
        complete=complete,
        on_progress=lambda f, line: registry.advance(job_id, phase_inner=f, log_line=line),
    )
    registry.advance(job_id, phase_idx=2, phase_inner=1.0)

    registry.advance(job_id, phase_idx=3, phase_inner=0.0, log_line="Saving")
    generated_at = datetime.now(UTC).isoformat()
    await store.upsert_minutes(
        session_id=sid,
        lang=lang,
        content=json.dumps(doc, ensure_ascii=False),
        generated_at=generated_at,
        model=backend.info.model,
        transcript_hash=transcript_hash(segments),
    )
    registry.advance(job_id, phase_inner=1.0)
    return {"session_id": sid, "lang": lang, "generated_at": generated_at}
