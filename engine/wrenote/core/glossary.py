"""Custom-vocabulary glossary — dual-purpose, fed to both STT and translation.

A glossary entry is ``{"term": str, "translation": str, "note": str}``. Two uses:

* **STT (Whisper)** — the terms become an ``initial_prompt`` that *soft-biases*
  Whisper toward those spellings (names, jargon, proper nouns). It's a nudge,
  not a hard constraint, and Whisper's prompt context is small, so we cap it.
* **Translation** — ``term → translation`` pairs are injected into the
  translator's prompt so key terms render consistently.

Pure helpers here (no I/O); the backends expose ``set_initial_prompt`` /
``set_glossary`` hooks that :func:`apply_to_backends` drives.
"""
from __future__ import annotations

from typing import Any

Entry = dict[str, Any]

# Whisper's prompt context is ~224 tokens; keep the term list well under that.
_STT_PROMPT_MAX_CHARS = 600


def _terms(entries: list[Entry]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for e in entries:
        t = str(e.get("term") or "").strip()
        if t and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    return out


def stt_initial_prompt(entries: list[Entry], max_chars: int = _STT_PROMPT_MAX_CHARS) -> str:
    """Build a capped Whisper initial prompt from the glossary terms.

    Terms are added until the budget is hit (whole terms only — never a
    truncated word). Empty when there are no terms.
    """
    terms = _terms(entries)
    if not terms:
        return ""
    prefix = "Glossary: "
    picked: list[str] = []
    length = len(prefix) + 1  # trailing period
    for t in terms:
        add = len(t) + (2 if picked else 0)  # ", "
        if length + add > max_chars:
            break
        picked.append(t)
        length += add
    return prefix + ", ".join(picked) + "."


def mt_pairs(entries: list[Entry]) -> list[tuple[str, str]]:
    """``(term, translation)`` for entries that have both — for the MT prompt."""
    out: list[tuple[str, str]] = []
    for e in entries:
        term = str(e.get("term") or "").strip()
        trans = str(e.get("translation") or "").strip()
        if term and trans:
            out.append((term, trans))
    return out


def mt_glossary_text(pairs: list[tuple[str, str]]) -> str:
    """One-line instruction listing fixed term translations, or '' if none."""
    if not pairs:
        return ""
    joined = "; ".join(f"{term} → {trans}" for term, trans in pairs)
    return f"Translate these terms exactly as given: {joined}."


def apply_to_backends(
    entries: list[Entry], *, stt: Any = None, translator: Any = None
) -> None:
    """Push the glossary into whichever backends are given. No-op hooks on
    backends that don't support it (mock, etc.), so this is always safe."""
    if stt is not None:
        prompt = stt_initial_prompt(entries)
        if prompt:
            stt.set_initial_prompt(prompt)
    if translator is not None:
        pairs = mt_pairs(entries)
        if pairs:
            translator.set_glossary(pairs)
