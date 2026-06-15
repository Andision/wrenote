"""Speaker-identification backend abstract interface.

Per design.v1.1 (P2 extension). Backends are stateless feature extractors
that turn an audio chunk into a fixed-size embedding vector. The Pipeline
owns the online clustering state (centroids per session), so swapping
backends doesn't reset the session.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np

from ..core.events import BackendInfo


class SpeakerBackend(ABC):
    """Speaker embedding extractor.

    Lifecycle:
        ``__init__`` stores configuration only; no I/O.
        Callers must ``await load()`` before :meth:`embed`.
        ``unload()`` releases the model; can be followed by another ``load()``.

    Blocking work:
        Inference is typically a blocking C/PyTorch call. Implementations
        MUST wrap it in a dedicated executor (see the whisper.cpp /
        llama.cpp backends for the pattern) so the event loop is not blocked
        and concurrent callers cannot race on the model.
    """

    @abstractmethod
    async def load(self) -> None: ...

    @abstractmethod
    async def unload(self) -> None: ...

    @abstractmethod
    async def embed(self, pcm: bytes, sample_rate: int = 16_000) -> np.ndarray:
        """Return a 1-D float32 embedding vector for the given audio.

        ``pcm`` is int16 little-endian mono PCM. Vector dimensionality is
        backend-specific (ECAPA = 192, pyannote = 256, ...). The Pipeline
        treats it as an opaque vector for cosine-distance clustering.
        """

    @property
    @abstractmethod
    def info(self) -> BackendInfo: ...
