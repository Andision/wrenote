"""Language-routing helpers shared by the live pipeline and the offline
(re)translation path.

Kept dependency-free on purpose: both :mod:`wrenote.core.pipeline` and the
server's translation service import this, so it must not import either.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

# What a language other than the main one has to score before it is
# believed. Whisper's language id on a short window is noisy — clean
# single-language audio scores 0.9+, the wrong language on mixed audio
# scored ~0.57 in the smoke test — so 0.6 keeps a stray English word in a
# Chinese sentence from flipping the whole segment, while a real switch
# to English still gets through.
DEFAULT_OVERRIDE_CONFIDENCE = 0.6


@dataclass(frozen=True)
class LanguagePolicy:
    """How a session's source language is decided per segment.

    ``main`` is what the meeting is in. ``secondary`` are the other languages
    that may come up (a Chinese meeting with English in it). Detection
    chooses only among ``main`` + ``secondary`` — never Japanese for a
    Chinese speaker, a classic Whisper slip — and a secondary language wins
    only with ``override_confidence`` or better; anything less is the main
    language. ``main`` None means free detection, the pre-policy behaviour.
    No secondaries means the main language is forced, no detection at all.
    """

    main: str | None = None
    secondary: tuple[str, ...] = field(default_factory=tuple)
    override_confidence: float = DEFAULT_OVERRIDE_CONFIDENCE

    @property
    def allowed(self) -> tuple[str, ...]:
        if self.main is None:
            return ()
        return (self.main, *(s for s in self.secondary if s != self.main))

    @property
    def detects(self) -> bool:
        """Whether language id has to run at all."""
        return self.main is None or bool(self.secondary)


def choose_language(
    probs: Mapping[str, float], policy: LanguagePolicy
) -> tuple[str, float]:
    """Pick the segment's language from Whisper's distribution under ``policy``.

    Returns (language, its probability). With no main language: the most
    probable language, whatever it is. Otherwise: the most probable of the
    allowed set, unless it is a secondary one scoring under the override
    threshold, in which case the main language (with its own score).
    """
    if policy.main is None:
        if not probs:
            return ("en", 0.0)
        lang = max(probs, key=lambda k: float(probs[k]))
        return (lang, float(probs[lang]))
    allowed = policy.allowed
    scores = {lang: float(probs.get(lang, 0.0)) for lang in allowed}
    best = max(allowed, key=lambda k: scores[k])
    if best != policy.main and scores[best] < policy.override_confidence:
        return (policy.main, scores[policy.main])
    return (best, scores[best])


def text_lang_override(text: str, audio_lang: str, tgt_lang: str) -> str:
    """Re-classify the source language from the transcribed text.

    Whisper's audio-side lang-id can be fooled by a single foreign loanword
    in otherwise native speech (e.g. "实话说这真不是我的handband" gets
    tagged as English because of "handband"). Looking at actual Unicode
    character classes in the transcript is far more reliable for the
    CJK / Latin split that matters for translation routing.

    Returns ``tgt_lang`` when the script analysis says the text is already
    in the target — that's the signal the translation loop uses to skip.
    Otherwise returns ``audio_lang`` unchanged.
    """
    if not text:
        return audio_lang

    cjk = hira = kata = hangul = latin = 0
    for ch in text:
        cp = ord(ch)
        if 0x4E00 <= cp <= 0x9FFF or 0x3400 <= cp <= 0x4DBF:
            cjk += 1  # CJK Unified + Extension-A (covers zh; also kanji in ja)
        elif 0x3040 <= cp <= 0x309F:
            hira += 1
        elif 0x30A0 <= cp <= 0x30FF:
            kata += 1
        elif 0xAC00 <= cp <= 0xD7AF:
            hangul += 1
        elif ch.isalpha():
            latin += 1

    total = cjk + hira + kata + hangul + latin
    if total == 0:
        return audio_lang

    # Strong markers for non-Han CJK languages — these scripts are unique.
    if hangul / total >= 0.2:
        return "ko" if tgt_lang in ("ko",) else audio_lang
    if (hira + kata) / total >= 0.15:
        return "ja" if tgt_lang in ("ja",) else audio_lang

    # CJK Han characters: only confidently call "zh" when target is zh and
    # there are no JP/KR markers (kanji alone is ambiguous between zh/ja).
    if (
        tgt_lang == "zh"
        and cjk > 0
        and hira == 0
        and kata == 0
        and hangul == 0
        and cjk / total >= 0.4
    ):
        return "zh"

    # Predominantly Latin script: tell zh-target translator the source is
    # genuinely non-CJK (don't override audio guess for the specific Latin
    # language — Whisper is more reliable than character classes there).
    return audio_lang
