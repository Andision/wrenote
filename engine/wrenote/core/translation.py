"""Offline / batch (re)translation service.

The single source of truth for the per-segment translate step shared by the
``/translate`` endpoint and the diarize-retranslate phase. Lives in core (not
the transport layer) and depends only on the neutral language helper, the
store, and the job registry — never on ``server`` or the live ``pipeline``.
"""
from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from .jobs import JobRegistry
from .lang import text_lang_override
from .store import Store

log = logging.getLogger(__name__)

# How many earlier segments the translator sees along with the one it
# translates. One is usually what resolves a pronoun or a dropped subject;
# more mostly costs prompt tokens on a model that is small on purpose.
CONTEXT_SEGMENTS = 1


def context_before(
    texts: Sequence[str], idx: int, *, n: int = CONTEXT_SEGMENTS
) -> list[str]:
    """The ``n`` non-empty texts preceding ``texts[idx]``, oldest first."""
    if n <= 0:
        return []
    out: list[str] = []
    i = idx - 1
    while i >= 0 and len(out) < n:
        t = (texts[i] or "").strip()
        if t:
            out.append(t)
        i -= 1
    out.reverse()
    return out


async def translate_one_for_segment(
    *,
    translator: Any,
    text: str,
    audio_lang: str | None,
    tgt_lang: str,
    context: Sequence[str] = (),
) -> tuple[str, str]:
    """Translate one segment's text. Returns (translated_text, status).

    Status is "final" on a successful non-empty translation, "skipped" when the
    text is already in the target language or the translator returns nothing /
    errors.
    """
    src = text_lang_override(text, audio_lang=audio_lang or "en", tgt_lang=tgt_lang)
    if src == tgt_lang:
        return ("", "skipped")
    try:
        translated = await translator.translate(
            text, src=src, tgt=tgt_lang, context=context
        )
    except Exception as e:
        log.exception(
            "translate failed: src=%s tgt=%s text=%r err=%r",
            src, tgt_lang, text[:80], e,
        )
        return ("", "skipped")
    translated = (translated or "").strip()
    if not translated:
        log.warning(
            "translator returned empty: src=%s tgt=%s text=%r",
            src, tgt_lang, text[:80],
        )
        return ("", "skipped")
    return (translated, "final")


def has_real_translations(segments: list[dict[str, Any]]) -> bool:
    return any(
        (s.get("trans_status") == "final")
        and bool((s.get("trans_text") or "").strip())
        for s in segments
    )


def translation_candidates(
    segments: list[dict[str, Any]],
    *,
    only_missing: bool,
) -> list[dict[str, Any]]:
    return [
        s for s in segments
        if (s.get("orig_text") or "").strip()
        and (
            not only_missing
            or not (s.get("trans_text") or "")
            or s.get("trans_status") in ("skipped", "stale")
        )
    ]


async def translate_segments_for_session(
    *,
    store: Store,
    session_id: str,
    session: dict[str, Any],
    segments: list[dict[str, Any]],
    translator: Any,
    tgt_lang: str,
    registry: JobRegistry,
    job_id: str,
) -> int:
    if not segments:
        registry.advance(job_id, phase_inner=1.0)
        return 0
    # Context comes from the session's whole transcript in order, not from
    # the candidate list: when only the untranslated rows are candidates, the
    # line before one of them is usually a row that isn't.
    base = list(session.get("segments") or [])
    known = {r["segment_id"] for r in base}
    if not all(s["segment_id"] in known for s in segments):
        # Rows the session doesn't know yet (a resegmented transcript being
        # written): the candidate list is the whole transcript then.
        base = list(segments)
    ordered = sorted(base, key=lambda r: r.get("ord", 0))
    all_texts = [str(r.get("orig_text") or "") for r in ordered]
    idx_of = {r["segment_id"]: i for i, r in enumerate(ordered)}
    total = max(1, len(segments))
    done = 0
    for s in segments:
        text = (s.get("orig_text") or "").strip()
        if not text:
            continue
        audio_lang = s.get("orig_lang") or session.get("src_lang") or "en"
        if audio_lang == "auto":
            audio_lang = "en"
        idx = idx_of.get(s["segment_id"])
        translated, status = await translate_one_for_segment(
            translator=translator,
            text=text,
            audio_lang=audio_lang,
            tgt_lang=tgt_lang,
            context=context_before(all_texts, idx) if idx is not None else (),
        )
        await store.upsert_segment_trans(
            session_id=session_id,
            segment_id=s["segment_id"],
            ord_=s["ord"],
            trans_text=translated,
            trans_status=status,
            trans_lang=tgt_lang,
        )

        done += 1
        registry.advance(
            job_id,
            phase_inner=done / total,
            log_line=(
                f"Translated {done}/{total}"
                if done % 5 == 0 or done == total else None
            ),
        )
    return done
