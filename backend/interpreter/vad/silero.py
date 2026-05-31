"""Silero VAD backend (torch-based).

Per-window probability threshold (no internal hysteresis); the Pipeline's
``min_silence_ms`` parameter handles smoothing at a higher level.

Per design.v1.1 §4.2.2. Silero's 16 kHz model expects 512-sample windows
(32 ms); :meth:`is_speech` buffers leftover samples between calls so it
works with the pipeline's ~100 ms ``AudioChunk`` size.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import numpy as np

from ..core.events import AudioChunk, BackendInfo
from ..core.registry import register_vad
from .base import VADBackend

log = logging.getLogger(__name__)

WINDOW_SAMPLES = 512  # 32 ms at 16 kHz; Silero's expected window size
EXPECTED_RATE = 16_000


@register_vad("silero")
class SileroVAD(VADBackend):
    def __init__(self, *, threshold: float = 0.5) -> None:
        self._threshold = threshold
        self._model: Any = None
        self._torch: Any = None
        self._buffer: np.ndarray = np.zeros(0, dtype=np.float32)
        self._last_speaking = False

    async def load(self) -> None:
        if self._model is not None:
            return

        def _load() -> tuple[Any, Any]:
            import torch
            from silero_vad import load_silero_vad

            return torch, load_silero_vad(onnx=False)

        log.info("Loading Silero VAD (threshold=%.2f)", self._threshold)
        self._torch, self._model = await asyncio.to_thread(_load)
        log.info("Silero VAD loaded")

    async def is_speech(self, chunk: AudioChunk) -> bool:
        if self._model is None:
            raise RuntimeError("SileroVAD not loaded; call load() first")
        if chunk.sample_rate != EXPECTED_RATE:
            raise ValueError(
                f"SileroVAD requires {EXPECTED_RATE}Hz audio; got {chunk.sample_rate}Hz"
            )

        samples = np.frombuffer(chunk.pcm, dtype=np.int16).astype(np.float32) / 32768.0
        if samples.size == 0:
            return self._last_speaking

        combined = np.concatenate([self._buffer, samples])
        if combined.size < WINDOW_SAMPLES:
            self._buffer = combined
            return self._last_speaking

        def _process(buf: np.ndarray, last: bool) -> tuple[bool, np.ndarray]:
            torch = self._torch
            assert self._model is not None
            speaking = last
            num = buf.size // WINDOW_SAMPLES
            for i in range(num):
                window = buf[i * WINDOW_SAMPLES : (i + 1) * WINDOW_SAMPLES]
                with torch.no_grad():
                    prob = self._model(torch.from_numpy(window), EXPECTED_RATE).item()
                speaking = prob > self._threshold
            leftover = buf[num * WINDOW_SAMPLES :]
            return speaking, leftover

        speaking, leftover = await asyncio.to_thread(_process, combined, self._last_speaking)
        self._last_speaking = speaking
        self._buffer = leftover
        return speaking

    @property
    def info(self) -> BackendInfo:
        return BackendInfo(
            name="silero_vad",
            version="silero-vad-6.2.1",
            model="silero_vad",
            device="cpu",
            capabilities={"threshold": self._threshold},
        )
