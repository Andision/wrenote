from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

from interpreter.core.diarize import (
    diarize_session,
    _normalize_source_segments,
    _pick_k,
    _trim_speech_bounds,
)
from interpreter.speaker.base import SpeakerBackend


class FakeAmplitudeSpeaker(SpeakerBackend):
    async def load(self) -> None:
        return None

    async def unload(self) -> None:
        return None

    async def embed(self, pcm: bytes, sample_rate: int = 16_000) -> np.ndarray:
        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        rms = float(np.sqrt(np.mean(audio * audio))) if audio.size else 0.0
        if rms < 0.18:
            return np.array([1.0, 0.0], dtype=np.float32)
        return np.array([0.0, 1.0], dtype=np.float32)

    @property
    def info(self):  # type: ignore[no-untyped-def]
        raise NotImplementedError


def _write_wav(path: Path, samples: np.ndarray, sr: int = 16_000) -> None:
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(samples.astype(np.int16).tobytes())


def test_trim_speech_bounds_removes_vad_hangover_silence() -> None:
    sr = 16_000
    leading = np.zeros(int(0.35 * sr), dtype=np.int16)
    tone_t = np.arange(int(0.85 * sr), dtype=np.float32) / sr
    tone = (0.12 * np.sin(2 * np.pi * 220 * tone_t) * 32767).astype(np.int16)
    trailing = np.zeros(int(0.80 * sr), dtype=np.int16)
    samples = np.concatenate([leading, tone, trailing])

    t0, t1 = _trim_speech_bounds(samples, sr, 0.0, len(samples) / sr)

    assert 0.18 <= t0 <= 0.36
    assert 1.18 <= t1 <= 1.36
    assert t1 - t0 < len(samples) / sr


def test_trim_speech_bounds_preserves_quiet_edge_before_loud_speaker() -> None:
    sr = 16_000

    def tone(duration_s: float, amp: float, freq: float) -> np.ndarray:
        t = np.arange(int(duration_s * sr), dtype=np.float32) / sr
        return (amp * np.sin(2 * np.pi * freq * t) * 32767).astype(np.int16)

    samples = np.concatenate(
        [
            tone(1.0, 0.08, 220),
            tone(1.0, 0.30, 330),
            np.zeros(int(0.50 * sr), dtype=np.int16),
        ]
    )

    t0, t1 = _trim_speech_bounds(samples, sr, 0.0, len(samples) / sr)

    assert t0 <= 0.12
    assert 1.88 <= t1 <= 2.16


def test_pick_k_allows_clear_two_speaker_small_n() -> None:
    embeddings = np.array(
        [
            [1.00, 0.00],
            [0.98, 0.02],
            [0.00, 1.00],
            [0.02, 0.98],
        ],
        dtype=np.float32,
    )
    embeddings /= np.linalg.norm(embeddings, axis=1, keepdims=True)

    assert _pick_k(embeddings, max_k=5) == 2


def test_pick_k_keeps_ambiguous_small_n_single_speaker() -> None:
    embeddings = np.array(
        [
            [1.00, 0.00],
            [0.99, 0.01],
            [0.98, 0.02],
            [0.97, 0.03],
        ],
        dtype=np.float32,
    )
    embeddings /= np.linalg.norm(embeddings, axis=1, keepdims=True)

    assert _pick_k(embeddings, max_k=5) == 1


def test_normalize_source_segments_collapses_previous_resegmentation() -> None:
    rows = [
        {
            "segment_id": "u-0000-r00",
            "started_at": 0.0,
            "ended_at": 1.0,
            "orig_text": "First turn.",
        },
        {
            "segment_id": "u-0000-r01",
            "started_at": 1.0,
            "ended_at": 2.0,
            "orig_text": "Second turn.",
        },
        {
            "segment_id": "u-0001-r00",
            "started_at": 2.0,
            "ended_at": 3.0,
            "orig_text": "Are",
        },
        {
            "segment_id": "u-0001-r01",
            "started_at": 3.0,
            "ended_at": 4.0,
            "orig_text": "you ready?",
        },
    ]

    out = _normalize_source_segments(rows)

    assert [row["segment_id"] for row in out] == ["u-0000", "u-0001"]
    assert out[0]["orig_text"] == "First turn. -Second turn."
    assert out[1]["orig_text"] == "Are you ready?"


async def test_diarize_session_clusters_whole_session_windows(tmp_path: Path) -> None:
    sr = 16_000

    def tone(duration_s: float, amp: float, freq: float) -> np.ndarray:
        t = np.arange(int(duration_s * sr), dtype=np.float32) / sr
        return (amp * np.sin(2 * np.pi * freq * t) * 32767).astype(np.int16)

    chunks = [
        tone(1.4, 0.10, 220),
        tone(1.4, 0.30, 330),
        tone(1.4, 0.10, 220),
        tone(1.4, 0.30, 330),
    ]
    samples = np.concatenate(chunks)
    wav_path = tmp_path / "session.wav"
    _write_wav(wav_path, samples, sr=sr)

    segments = []
    t0 = 0.0
    for i, chunk in enumerate(chunks):
        dur = len(chunk) / sr
        segments.append(
            {
                "segment_id": f"s{i}",
                "started_at": t0,
                "ended_at": t0 + dur,
                "orig_text": f"turn {i}",
                "orig_lang": "en",
                "trans_lang": "zh",
            }
        )
        t0 += dur

    result = await diarize_session(
        wav_path=wav_path,
        segments=segments,
        speaker=FakeAmplitudeSpeaker(),
    )

    assert result.labels == {
        "s0": "Speaker 1",
        "s1": "Speaker 2",
        "s2": "Speaker 1",
        "s3": "Speaker 2",
    }
    assert [row["segment_id"] for row in result.segments] == ["s0", "s1", "s2", "s3"]
    assert [row["speaker"] for row in result.segments] == [
        "Speaker 1",
        "Speaker 2",
        "Speaker 1",
        "Speaker 2",
    ]


async def test_diarize_session_resegments_dialogue_markers(tmp_path: Path) -> None:
    sr = 16_000

    def tone(duration_s: float, amp: float, freq: float) -> np.ndarray:
        t = np.arange(int(duration_s * sr), dtype=np.float32) / sr
        return (amp * np.sin(2 * np.pi * freq * t) * 32767).astype(np.int16)

    chunks = [
        tone(2.0, 0.10, 220),
        tone(2.0, 0.30, 330),
        tone(2.0, 0.10, 220),
        tone(2.0, 0.30, 330),
    ]
    wav_path = tmp_path / "dialogue.wav"
    _write_wav(wav_path, np.concatenate(chunks), sr=sr)

    result = await diarize_session(
        wav_path=wav_path,
        segments=[
                {
                    "segment_id": "u-0000",
                    "started_at": 0.0,
                    "ended_at": 8.0,
                    "orig_text": (
                        "-Host asks a question. -Guest gives an answer. "
                        "-Host follows up. -Guest closes."
                    ),
                    "orig_lang": "en",
                    "trans_lang": "zh",
                }
        ],
        speaker=FakeAmplitudeSpeaker(),
    )

    assert [row["segment_id"] for row in result.segments] == [
        "u-0000-r00",
        "u-0000-r01",
        "u-0000-r02",
        "u-0000-r03",
    ]
    assert [row["orig_text"] for row in result.segments] == [
        "Host asks a question.",
        "Guest gives an answer.",
        "Host follows up.",
        "Guest closes.",
    ]
    assert [row["speaker"] for row in result.segments] == [
        "Speaker 1",
        "Speaker 2",
        "Speaker 1",
        "Speaker 2",
    ]
    assert result.labels == {
        "u-0000-r00": "Speaker 1",
        "u-0000-r01": "Speaker 2",
        "u-0000-r02": "Speaker 1",
        "u-0000-r03": "Speaker 2",
    }


async def test_diarize_session_keeps_unmarked_text_intact(tmp_path: Path) -> None:
    sr = 16_000

    def tone(duration_s: float, amp: float, freq: float) -> np.ndarray:
        t = np.arange(int(duration_s * sr), dtype=np.float32) / sr
        return (amp * np.sin(2 * np.pi * freq * t) * 32767).astype(np.int16)

    chunks = [
        tone(2.0, 0.10, 220),
        tone(2.0, 0.30, 330),
    ]
    wav_path = tmp_path / "unmarked.wav"
    _write_wav(wav_path, np.concatenate(chunks), sr=sr)

    result = await diarize_session(
        wav_path=wav_path,
        segments=[
            {
                "segment_id": "u-0000",
                "started_at": 0.0,
                "ended_at": 4.0,
                "orig_text": "Host asks a question and guest gives an answer.",
                "orig_lang": "en",
                "trans_lang": "zh",
            }
        ],
        speaker=FakeAmplitudeSpeaker(),
    )

    assert [row["segment_id"] for row in result.segments] == ["u-0000"]
    assert result.segments[0]["orig_text"] == (
        "Host asks a question and guest gives an answer."
    )
