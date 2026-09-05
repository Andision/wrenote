"""Segment boundaries and the context that crosses them (core/segmentation.py,
stt/whisper_cpp.compose_prompt, translator prompts, core/translation.context_before).

Pure functions; each rule gets the smallest array or string that would break it.
"""
from __future__ import annotations

import numpy as np

from wrenote.core.segmentation import context_tail, find_cut_point
from wrenote.core.translation import context_before
from wrenote.stt.whisper_cpp import compose_prompt
from wrenote.translator.llama_cpp import LlamaCppTranslator

RATE = 16000


def _tone(ms: int, amp: int = 8000) -> np.ndarray:
    n = RATE * ms // 1000
    return (np.sin(np.arange(n) * 0.3) * amp).astype(np.int16)


def _silence(ms: int) -> np.ndarray:
    return np.zeros(RATE * ms // 1000, dtype=np.int16)


class TestFindCutPoint:
    def test_lands_in_the_quiet_gap_of_the_tail(self):
        # 20 s of speech, a 300 ms breath at 18.0–18.3 s, more speech to 21 s.
        pcm = np.concatenate([_tone(18000), _silence(300), _tone(2700)]).tobytes()
        cut = find_cut_point(pcm, tail_ms=4000)
        t = cut / 2 / RATE
        assert 18.0 <= t <= 18.3
        assert cut % 2 == 0

    def test_only_the_tail_is_searched(self):
        # The one true gap is at 5 s, far outside the last 4 s: the cut is
        # then the quietest moment of the tail, not that gap.
        pcm = np.concatenate([_tone(5000), _silence(500), _tone(15000)]).tobytes()
        cut = find_cut_point(pcm, tail_ms=4000)
        assert cut / 2 / RATE >= 16.5

    def test_uniform_audio_cuts_at_the_start_of_the_tail(self):
        # No gap at all: ties go to the earliest frame, so the segment gives
        # up the whole tail rather than nothing.
        pcm = np.full(RATE * 10, 5000, dtype=np.int16).tobytes()
        cut = find_cut_point(pcm, tail_ms=4000, frame_ms=100)
        assert abs(cut / 2 / RATE - 6.05) < 0.01

    def test_too_short_to_cut(self):
        assert find_cut_point(b"") == 0
        one_frame = np.ones(RATE // 10, dtype=np.int16).tobytes()
        assert find_cut_point(one_frame) == len(one_frame)

    def test_never_cuts_at_zero(self):
        # Silence everywhere, tail covers the whole buffer: frame 0 is still
        # off limits, so the closed segment is never empty.
        pcm = _silence(1000).tobytes()
        assert find_cut_point(pcm, tail_ms=10_000) > 0


class TestContextTail:
    def test_short_text_is_whole(self):
        assert context_tail("hello there") == "hello there"

    def test_cuts_on_a_word_boundary(self):
        text = " ".join(f"word{i}" for i in range(100))
        tail = context_tail(text, max_chars=50)
        assert len(tail) <= 50
        assert tail.startswith("word")
        assert text.endswith(tail)

    def test_cjk_is_cut_by_character(self):
        text = "今天的会议我们讨论了三个问题" * 30
        assert len(context_tail(text, max_chars=40)) == 40

    def test_whitespace_is_normalised(self):
        assert context_tail("a \n  b\tc") == "a b c"


class TestComposePrompt:
    def test_context_then_glossary(self):
        assert compose_prompt(context="we said this", glossary="Glossary: Wrenote.") == (
            "we said this Glossary: Wrenote."
        )

    def test_empty_parts_vanish(self):
        assert compose_prompt(context="", glossary="") == ""
        assert compose_prompt(context="  ", glossary="Glossary: X.") == "Glossary: X."


class TestContextBefore:
    def test_previous_non_empty_texts_oldest_first(self):
        texts = ["a", "", "b", "c", "d"]
        assert context_before(texts, 4, n=2) == ["b", "c"]
        assert context_before(texts, 2, n=3) == ["a"]
        assert context_before(texts, 0) == []

    def test_zero_is_off(self):
        assert context_before(["a", "b"], 1, n=0) == []


class TestLlamaPrompt:
    def test_context_is_marked_and_the_last_block_is_the_text(self):
        tr = LlamaCppTranslator(model_path="/nonexistent.gguf")
        prompt = tr._build_prompt("It broke again.", src="en", tgt="zh", context=["The build was red."])
        assert "Context" in prompt
        assert "do not translate" in prompt
        assert prompt.index("The build was red.") < prompt.index("It broke again.")
        assert prompt.rstrip().endswith("It broke again.")

    def test_no_context_keeps_the_plain_prompt(self):
        tr = LlamaCppTranslator(model_path="/nonexistent.gguf")
        prompt = tr._build_prompt("Hi", src="en", tgt="zh")
        assert "Context" not in prompt
        assert prompt.endswith("\n\nHi")
