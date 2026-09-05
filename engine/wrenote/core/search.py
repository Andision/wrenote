"""Finding things again: the query side of the full-text index.

The index (``segments_fts`` in core/store.py) uses SQLite's trigram
tokenizer, the one tokenizer that makes Chinese searchable without a word
segmenter: every three-character window is a token, so any substring of
three or more characters matches, in any script. The price is that a
one- or two-character query cannot use it; those fall back to a plain
``LIKE`` scan, which at a personal library's size is fine.

Two callers: the search box (a person's words, best-first, with snippets)
and the chat (the user's question, turned into terms, to pull the relevant
lines of a transcript too long to show the model whole).
"""
from __future__ import annotations

import re
from typing import Any

MIN_TRIGRAM = 3

_CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿豈-﫿가-힯]+")
_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9'’_-]*")


def _quote(term: str) -> str:
    return '"' + term.replace('"', '""') + '"'


def phrase_query(text: str) -> str | None:
    """The FTS5 query for a person's search string: the string as a phrase.

    None when the string is too short for the index (use :func:`like_pattern`).
    """
    text = " ".join(text.split())
    if len(text) < MIN_TRIGRAM:
        return None
    return _quote(text)


def like_pattern(text: str) -> str:
    """A ``LIKE`` pattern for the short queries the index can't take."""
    text = " ".join(text.split())
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def terms_query(question: str, *, max_terms: int = 24) -> str | None:
    """An OR of the searchable terms in a question, for retrieval.

    Latin words of three or more characters as they are; a run of CJK as
    its three-character windows (that is what the index holds). None when
    nothing in the question can be looked up.
    """
    terms: list[str] = []
    seen: set[str] = set()

    def add(t: str) -> None:
        if t not in seen:
            seen.add(t)
            terms.append(t)

    for word in _WORD.findall(question):
        if len(word) >= MIN_TRIGRAM:
            add(word.lower())
    for run in _CJK.findall(question):
        if len(run) < MIN_TRIGRAM:
            continue
        for i in range(len(run) - MIN_TRIGRAM + 1):
            add(run[i : i + MIN_TRIGRAM])
    if not terms:
        return None
    return " OR ".join(_quote(t) for t in terms[:max_terms])


def pick_excerpts(
    hits: list[dict[str, Any]],
    segments: list[dict[str, Any]],
    *,
    max_hits: int = 20,
    around: int = 1,
) -> list[dict[str, Any]]:
    """The segments to show the model: the best hits with a neighbour on
    each side, in transcript order, each once."""
    by_id = {s["segment_id"]: i for i, s in enumerate(segments)}
    wanted: set[int] = set()
    for h in hits[:max_hits]:
        i = by_id.get(h["segment_id"])
        if i is None:
            continue
        for j in range(i - around, i + around + 1):
            if 0 <= j < len(segments):
                wanted.add(j)
    return [segments[i] for i in sorted(wanted)]

