"""A session's languages (core/lang.py LanguagePolicy) and how Whisper's
per-segment detection is held to them.

The Whisper backend is exercised with a stand-in model: what matters is the
decision made from the probabilities, not the probabilities themselves.
"""
from __future__ import annotations

import numpy as np
import pytest

from wrenote.core.events import AudioSegment
from wrenote.core.lang import LanguagePolicy, choose_language
from wrenote.stt.whisper_cpp import WhisperCppBackend


class TestChooseLanguage:
    def test_free_detection_without_a_main_language(self):
        assert choose_language({"en": 0.2, "ja": 0.7, "zh": 0.1}, LanguagePolicy()) == ("ja", 0.7)
        assert choose_language({}, LanguagePolicy()) == ("en", 0.0)

    def test_only_allowed_languages_count(self):
        # Whisper's favourite is Japanese — a Chinese speaker's usual fate — but
        # the session is Chinese with some English, so Japanese is not an option.
        policy = LanguagePolicy(main="zh", secondary=("en",))
        assert choose_language({"ja": 0.5, "zh": 0.3, "en": 0.2}, policy) == ("zh", 0.3)

    def test_a_secondary_language_needs_confidence(self):
        policy = LanguagePolicy(main="zh", secondary=("en",), override_confidence=0.6)
        assert choose_language({"zh": 0.35, "en": 0.55}, policy) == ("zh", 0.35)
        assert choose_language({"zh": 0.2, "en": 0.75}, policy) == ("en", 0.75)

    def test_the_main_language_never_needs_it(self):
        policy = LanguagePolicy(main="en", secondary=("zh",))
        assert choose_language({"en": 0.05, "zh": 0.04}, policy) == ("en", 0.05)

    def test_allowed_and_detects(self):
        assert LanguagePolicy(main="zh", secondary=("en", "zh")).allowed == ("zh", "en")
        assert LanguagePolicy(main="zh").detects is False
        assert LanguagePolicy(main="zh", secondary=("en",)).detects is True
        assert LanguagePolicy().detects is True


class FakeModel:
    """Answers language id from a fixed distribution and records what it
    was asked to transcribe in."""

    def __init__(self, probs: dict[str, float]) -> None:
        self.probs = probs
        self.langs: list[str] = []

    def auto_detect_language(self, audio):
        best = max(self.probs, key=self.probs.get)
        return (best, np.float32(self.probs[best])), {k: np.float32(v) for k, v in self.probs.items()}

    def transcribe(self, audio, **kwargs):
        self.langs.append(kwargs["language"])
        return []


def _backend(probs: dict[str, float]) -> tuple[WhisperCppBackend, FakeModel]:
    b = WhisperCppBackend(model_path="/nonexistent.bin")
    fake = FakeModel(probs)
    b._model = fake  # the model is a dependency; the decision is what's under test
    return b, fake


def _segment(seconds: float = 2.0) -> AudioSegment:
    pcm = (np.random.default_rng(0).standard_normal(int(16000 * seconds)) * 1000).astype(np.int16)
    return AudioSegment(segment_id="s", pcm=pcm.tobytes(), t0=0.0, t1=seconds)


@pytest.mark.parametrize(
    ("probs", "expected"),
    [
        ({"zh": 0.4, "en": 0.5, "ja": 0.1}, "zh"),  # English under threshold → main
        ({"zh": 0.1, "en": 0.85, "ja": 0.05}, "en"),  # a real switch
        ({"ja": 0.9, "zh": 0.06, "en": 0.04}, "zh"),  # Japanese isn't on the list
    ],
)
async def test_whisper_follows_the_policy(probs, expected):
    b, fake = _backend(probs)
    b.set_language_policy(LanguagePolicy(main="zh", secondary=("en",), override_confidence=0.6))
    ev = await b.transcribe_segment(_segment(), src_lang="zh")
    assert ev.lang == expected and fake.langs == [expected]
    assert ev.confidence == pytest.approx(probs[expected])


async def test_pinned_without_secondaries_never_detects():
    b, fake = _backend({"en": 0.99})
    b.set_language_policy(LanguagePolicy(main="zh"))
    ev = await b.transcribe_segment(_segment(), src_lang="zh")
    assert ev.lang == "zh" and fake.langs == ["zh"] and ev.confidence is None


async def test_short_audio_is_the_main_language():
    b, fake = _backend({"en": 0.99, "zh": 0.01})
    b.set_language_policy(LanguagePolicy(main="zh", secondary=("en",)))
    ev = await b.transcribe_segment(_segment(0.2), src_lang="zh")
    assert ev.lang == "zh" and ev.text == "" and fake.langs == []


def test_ws_start_applies_the_policy(client):
    """The start config's languages reach the backend as a policy."""
    import wrenote.ws as ws_mod
    seen: list[LanguagePolicy] = []
    orig = ws_mod.make_stt

    def spy(backend, params):
        stt = orig(backend, params)
        real = stt.set_language_policy

        def record(policy):
            seen.append(policy)
            real(policy)
        stt.set_language_policy = record  # type: ignore[method-assign]
        return stt

    from unittest import mock
    with mock.patch.object(ws_mod, "make_stt", spy), client.websocket_connect("/v1/ws") as ws:
        ws.send_json({"type": "start", "config": {"session_id": "p1", "src": "zh", "secondary_langs": ["en", " "], "lang_override_confidence": 0.7}})
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "stop"})
    assert seen == [LanguagePolicy(main="zh", secondary=("en",), override_confidence=0.7)]
