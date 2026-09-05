"""Transcript snapshotting + chat system-prompt assembly.

Pulled out of ``server.py`` so the transport layer just calls a function and
serializes the result. Pure (no I/O), so it's trivially unit-testable.
"""
from __future__ import annotations

from typing import Any

# Soft cap on the transcript portion (chars). The chat model is 32K-token
# native; reserving a chunk for system framing, chat history, and the
# generated response leaves ~80K chars of transcript headroom (CJK runs
# ~1.5 tokens/char, Latin ~0.25). Past this we keep the *tail* — the user
# is far likelier to ask about recent content than the opening minutes.
MAX_TRANSCRIPT_CHARS = 80_000

_CHAT_SYSTEM_TEMPLATE = (
    "You are an assistant helping the user understand and reason about a "
    "live conversation transcript. The transcript below is everything the "
    "session has captured so far. Be concise, cite times when useful, and "
    "answer in the same language the user writes to you in.{trunc_note}\n\n"
    "=== TRANSCRIPT ===\n{transcript}\n=== END TRANSCRIPT ==="
)

_TRUNC_NOTE = (
    "\n\nNote: only the most recent portion of the transcript is shown "
    "below — earlier content was trimmed to fit the context window. "
    "If the user asks about something not present, say so."
)


def build_transcript_snapshot(segments: list[dict[str, Any]]) -> tuple[str, bool]:
    """Return (snapshot, truncated). Most recent end of the transcript wins
    when we have to trim."""
    lines: list[str] = []
    for s in segments:
        text = (s.get("orig_text") or "").strip()
        if not text:
            continue
        t = s.get("started_at") or 0.0
        spk = s.get("speaker") or ""
        prefix = f"[{t:.1f}s{' ' + spk if spk else ''}]"
        lines.append(f"{prefix} {text}")
    if not lines:
        return "(no speech captured yet)", False

    full = "\n".join(lines)
    if len(full) <= MAX_TRANSCRIPT_CHARS:
        return full, False

    # Drop oldest lines until we fit. Walk back from the end.
    kept: list[str] = []
    running = 0
    for line in reversed(lines):
        # +1 for the newline that will join them.
        if running + len(line) + 1 > MAX_TRANSCRIPT_CHARS:
            break
        kept.append(line)
        running += len(line) + 1
    kept.reverse()
    return "\n".join(kept), True


_EXCERPTS_NOTE = (
    "\n\nThe transcript is long. Lines that look relevant to the user's "
    "latest question were pulled from the trimmed part and are listed first, "
    "each with its time; the most recent portion follows in full."
)


def is_truncated(segments: list[dict[str, Any]]) -> bool:
    _snapshot, truncated = build_transcript_snapshot(segments)
    return truncated


def build_chat_system_prompt(
    segments: list[dict[str, Any]],
    excerpts: list[dict[str, Any]] | None = None,
) -> str:
    """Assemble the system message that frames the transcript for the chat
    model, including the truncation note when the snapshot was trimmed.

    ``excerpts`` are segments retrieved for the user's question from the
    part a trimmed snapshot dropped (see core/search.py); they go ahead of
    the snapshot so a question about the first hour of a two-hour meeting
    still has something to be answered from.
    """
    transcript, truncated = build_transcript_snapshot(segments)
    trunc_note = _TRUNC_NOTE if truncated else ""
    if truncated and excerpts:
        lines, _ = build_transcript_snapshot(excerpts)
        transcript = f"=== RELEVANT EXCERPTS ===\n{lines}\n=== RECENT TRANSCRIPT ===\n{transcript}"
        trunc_note += _EXCERPTS_NOTE
    return _CHAT_SYSTEM_TEMPLATE.format(transcript=transcript, trunc_note=trunc_note)
