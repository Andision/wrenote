"""STT backend abstract interface.

Per design.v1.1 §4.2.1.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from ..core.events import AudioSegment, BackendInfo, TranscriptEvent
from ..core.lang import LanguagePolicy

PartialCallback = Callable[[TranscriptEvent], Awaitable[None]]


@dataclass(frozen=True)
class StreamUpdate:
    """What a streaming recogniser knows after the latest audio."""

    text: str
    #: The recogniser has decided the utterance is over (trailing silence, or
    #: the length cap). The caller reads ``text`` as final and calls reset().
    endpoint: bool = False
    #: Seconds from the start of the current utterance (the last reset) to
    #: its first token, when the backend knows; None otherwise.
    start_offset_s: float | None = None


class STTStream(ABC):
    """A recogniser that takes audio as it arrives and answers each time.

    The streaming-native path: no segmentation outside, no re-decoding of a
    growing buffer. Audio goes in chunk by chunk; ``text`` after each chunk
    is the utterance so far and only ever grows; ``endpoint`` says the
    utterance ended. One stream per session, reset between utterances.

    Blocking work runs off the event loop, as with :class:`STTBackend`.
    """

    @abstractmethod
    async def feed(self, pcm: bytes) -> StreamUpdate:
        """Take one chunk of 16 kHz mono int16 PCM; return the state after it."""

    @abstractmethod
    async def flush(self) -> StreamUpdate:
        """No more audio is coming: decode what is buffered and return the end state."""

    @abstractmethod
    async def reset(self) -> None:
        """Start the next utterance; the text so far is dropped."""

    @abstractmethod
    async def close(self) -> None:
        """Release the stream."""


class STTBackend(ABC):
    """Speech-to-text backend.

    Lifecycle:
        ``__init__`` only stores configuration — no I/O.
        Callers must ``await load()`` before calling :meth:`transcribe_segment`.
        ``unload()`` may be followed by another ``load()`` (hot reload).

    Blocking work:
        Real inference is almost always a blocking C-extension call. Each
        implementation MUST wrap such work in ``asyncio.to_thread`` (or an
        equivalent executor) so the FastAPI event loop is not blocked.
    """

    @abstractmethod
    async def load(self) -> None:
        """Load the model into memory; raise on failure."""

    @abstractmethod
    async def unload(self) -> None:
        """Release the model. Idempotent (safe to call multiple times)."""

    @abstractmethod
    async def transcribe_segment(
        self,
        segment: AudioSegment,
        *,
        src_lang: str | None = None,
        on_partial: PartialCallback | None = None,
        context: str = "",
    ) -> TranscriptEvent:
        """Transcribe a complete VAD-segmented audio segment.

        Returns the *final* :class:`TranscriptEvent` (``type='final'``).

        ``context`` is the text recognised just before this segment (the tail
        of it — see :func:`wrenote.core.segmentation.context_tail`). Whisper
        conditions on it the way it conditions on the previous window of a
        long file, which is what keeps a sentence the VAD cut in two from
        being decoded as two unrelated sentences. Backends without a decoder
        prompt ignore it.

        If ``on_partial`` is provided, the implementation MAY invoke it during
        decoding to emit growing-buffer partials (``type='partial'``). P1-a
        implementations may ignore ``on_partial`` and return only the final
        event; growing-buffer support is a P1-c upgrade.

        Chunking strategy is a backend-internal detail.
        """

    def open_stream(
        self, *, min_silence_ms: int, max_segment_ms: int
    ) -> STTStream | None:
        """A live stream for one session, or None for a backend that only does
        whole segments (the pipeline then segments with the VAD and calls
        :meth:`transcribe_segment`). ``min_silence_ms`` is how much trailing
        silence ends an utterance; ``max_segment_ms`` caps one utterance.
        """
        return None

    def set_language_policy(self, policy: LanguagePolicy) -> None:  # noqa: B027 — optional hook, no-op default
        """Which languages this session may be in, and how sure detection
        must be to leave the main one (see :class:`LanguagePolicy`).

        Default no-op; backends that run language id (Whisper) override.
        Set per session before :meth:`transcribe_segment`.
        """

    def set_initial_prompt(self, prompt: str) -> None:  # noqa: B027 — optional hook, no-op default
        """Soft-bias decoding toward a custom vocabulary (glossary terms).

        Default no-op; backends with a decoder prompt (Whisper) override.
        Set per session before :meth:`transcribe_segment`.
        """

    @property
    @abstractmethod
    def info(self) -> BackendInfo:
        """Backend metadata (name, model, device, etc.)."""
