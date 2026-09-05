"""Event types flowing through the pipeline.

Per design.v1.1 §4.1. Internal events (AudioChunk, AudioSegment) only flow
between pipeline stages. Client-facing events serialize to JSON and travel
over the WebSocket.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class BackendInfo(BaseModel):
    """Metadata about a backend instance, surfaced via /info and ReadyEvent."""

    name: str
    version: str = "unknown"
    model: str = "unknown"
    device: str = "cpu"
    supported_languages: list[str] = Field(default_factory=list)
    capabilities: dict[str, Any] = Field(default_factory=dict)


# ---------- Internal events (pipeline-only, not sent to client) ----------


class AudioChunk(BaseModel):
    """A small slice of PCM audio received from the client."""

    pcm: bytes
    sample_rate: int = 16000
    seq: int
    server_ts: float


class AudioSegment(BaseModel):
    """A complete VAD-segmented voice segment (input to STT)."""

    segment_id: str
    pcm: bytes
    sample_rate: int = 16000
    t0: float
    t1: float


# ---------- Client-facing events (JSON over WebSocket) ----------


class ReadyEvent(BaseModel):
    """Sent once after the session is initialised; reports backend metadata."""

    type: Literal["ready"] = "ready"
    stt: BackendInfo
    vad: BackendInfo
    translator: BackendInfo
    speaker: BackendInfo | None = None


class VADEvent(BaseModel):
    """VAD state transition: a segment started or ended."""

    type: Literal["speech_start", "speech_end"]
    segment_id: str
    ts: float


class TranscriptEvent(BaseModel):
    """STT output. `type='partial'` may be emitted multiple times during a
    segment as the transcription grows; `type='final'` is emitted once when
    the segment is fully decoded."""

    type: Literal["partial", "final"]
    segment_id: str
    text: str
    lang: str
    t0: float | None = None
    t1: float | None = None
    confidence: float | None = None
    # Online-clustered speaker label, e.g. "Speaker 1", "Speaker 2", or
    # "unknown" for segments too short to embed reliably. ``None`` means
    # speaker identification is disabled / not yet computed (e.g. partials).
    speaker: str | None = None


class TranslationEvent(BaseModel):
    """Translated text for a transcript.

    ``partial=True`` means this is a best-effort translation of a growing
    partial transcript; expect newer translations (and a final one) to
    overwrite it. ``partial=False`` means it is the translation of the
    segment's final transcript and should be considered authoritative.

    ``skipped=True`` is a special final form emitted when the detected source
    language equals the target — no translation was performed (the original
    text already is the target). UIs should treat this as "no translation
    needed" rather than "pending".
    """

    type: Literal["translation"] = "translation"
    segment_id: str
    text: str
    src_lang: str
    tgt_lang: str
    partial: bool = False
    skipped: bool = False
    # Mirrors TranscriptEvent.speaker so the UI can colour the translation
    # column without having to cross-reference the original.
    speaker: str | None = None


class ErrorEvent(BaseModel):
    """Error surfaced to the client. See design.v1.1 §5.4 for codes."""

    type: Literal["error"] = "error"
    code: str
    msg: str
    recoverable: bool = True


class MetricsEvent(BaseModel):
    """Runtime metrics snapshot (queue depths, rolling latency averages)."""

    type: Literal["metric"] = "metric"
    queues: dict[str, int]
    latencies: dict[str, float]
    ts: float


ClientEvent = (
    ReadyEvent
    | VADEvent
    | TranscriptEvent
    | TranslationEvent
    | ErrorEvent
    | MetricsEvent
)
"""Union of all events that can be sent over the WebSocket."""
