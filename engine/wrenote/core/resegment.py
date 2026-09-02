"""Manual re-segmentation: split one segment in two, or merge with the next.

Pure transforms over the ordered segment list (the dicts ``store.get_session``
returns / ``store.replace_segments`` accepts). The endpoints apply one of these
then persist the whole list via ``replace_segments`` — the same path diarize
uses. ``ord`` is always renumbered 0..n-1 so the list stays canonical.

Re-segmentation changes text boundaries, so any affected translation is reset
to "skipped" (split) or flagged (merge) — the Translate action refreshes it.
Timestamps on a split are interpolated by character offset (good enough without
word-level timing; tighter if word timestamps are added later).
"""
from __future__ import annotations

import uuid
from typing import Any

Segment = dict[str, Any]


def _ordered(segments: list[Segment]) -> list[Segment]:
    return [dict(s) for s in sorted(segments, key=lambda s: s.get("ord", 0))]


def _renumber(segs: list[Segment]) -> list[Segment]:
    for i, s in enumerate(segs):
        s["ord"] = i
    return segs


def _index_of(segs: list[Segment], segment_id: str) -> int:
    for i, s in enumerate(segs):
        if s.get("segment_id") == segment_id:
            return i
    raise ValueError("segment not found")


def split_segment(segments: list[Segment], segment_id: str, offset: int) -> list[Segment]:
    """Split ``segment_id``'s original text at character ``offset`` into two
    segments. Times are interpolated by offset; both halves lose their (now
    mismatched) translation. Returns the new, renumbered list."""
    segs = _ordered(segments)
    idx = _index_of(segs, segment_id)
    seg = segs[idx]
    orig = seg.get("orig_text") or ""
    offset = max(0, min(offset, len(orig)))
    left_text = orig[:offset].strip()
    right_text = orig[offset:].strip()
    if not left_text or not right_text:
        raise ValueError("split point produces an empty half")

    start = float(seg.get("started_at") or 0.0)
    end = float(seg.get("ended_at") or start)
    frac = offset / len(orig) if orig else 0.5
    mid = start + (end - start) * frac

    left = dict(seg)
    left.update(orig_text=left_text, ended_at=mid, trans_text="", trans_status="skipped")
    right = dict(seg)
    right.update(
        segment_id=uuid.uuid4().hex,
        orig_text=right_text,
        started_at=mid,
        trans_text="",
        trans_status="skipped",
    )
    return _renumber([*segs[:idx], left, right, *segs[idx + 1:]])


def merge_with_next(segments: list[Segment], segment_id: str) -> list[Segment]:
    """Merge ``segment_id`` with the segment that follows it. Text + translation
    are concatenated, the time span widens, the first segment's speaker wins.
    Returns the new, renumbered list."""
    segs = _ordered(segments)
    idx = _index_of(segs, segment_id)
    if idx + 1 >= len(segs):
        raise ValueError("no following segment to merge")
    a, b = segs[idx], segs[idx + 1]

    def _join(x: str, y: str) -> str:
        return " ".join(p for p in (x.strip(), y.strip()) if p)

    merged = dict(a)
    merged["orig_text"] = _join(a.get("orig_text") or "", b.get("orig_text") or "")
    merged["trans_text"] = _join(a.get("trans_text") or "", b.get("trans_text") or "")
    both_final = a.get("trans_status") == "final" and b.get("trans_status") == "final"
    if not merged["trans_text"]:
        merged["trans_status"] = "skipped"
    elif both_final:
        merged["trans_status"] = "final"
    else:
        merged["trans_status"] = "stale"
    merged["ended_at"] = b.get("ended_at") or a.get("ended_at")
    return _renumber([*segs[:idx], merged, *segs[idx + 2:]])
