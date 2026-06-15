"""Silero VAD backend (ONNX Runtime — torch-free).

Per design.v1.1 §4.2.2. Runs Silero's v5 16 kHz VAD directly via onnxruntime,
so the realtime path carries no torch dependency. We replicate the model's
stateful handling in numpy: each 512-sample (32 ms) window is prefixed with a
64-sample lookback ``context`` and the LSTM ``state`` is carried across windows;
both reset per backend instance (one per session). :meth:`is_speech` buffers
leftover samples between calls so it works with the pipeline's ~100 ms chunks.

Per-window probability threshold (no internal hysteresis); the Pipeline's
``min_silence_ms`` parameter handles smoothing at a higher level.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

import numpy as np

from ..core.events import AudioChunk, BackendInfo
from ..core.registry import register_vad
from .base import VADBackend

log = logging.getLogger(__name__)

WINDOW_SAMPLES = 512  # 32 ms at 16 kHz; Silero's expected window size
CONTEXT_SAMPLES = 64  # v5 lookback prepended to each window (matches OnnxWrapper)
EXPECTED_RATE = 16_000
STATE_SHAPE = (2, 1, 128)  # LSTM (h, c) state carried across windows

# Bundled model asset; shipped with the package so VAD works with no download.
DEFAULT_MODEL_PATH = Path(__file__).resolve().parent / "assets" / "silero_vad.onnx"


@register_vad("silero")
class SileroVAD(VADBackend):
    def __init__(self, *, threshold: float = 0.5, model_path: str | None = None) -> None:
        self._threshold = threshold
        self._model_path = (
            str(Path(model_path).expanduser()) if model_path else str(DEFAULT_MODEL_PATH)
        )
        self._session: Any = None
        self._state = np.zeros(STATE_SHAPE, dtype=np.float32)
        self._context = np.zeros((1, CONTEXT_SAMPLES), dtype=np.float32)
        self._buffer: np.ndarray = np.zeros(0, dtype=np.float32)
        self._last_speaking = False

    async def load(self) -> None:
        if self._session is not None:
            return

        def _load() -> Any:
            import onnxruntime as ort

            opts = ort.SessionOptions()
            opts.inter_op_num_threads = 1
            opts.intra_op_num_threads = 1
            return ort.InferenceSession(
                self._model_path, sess_options=opts, providers=["CPUExecutionProvider"]
            )

        log.info("Loading Silero VAD (ONNX, threshold=%.2f)", self._threshold)
        self._session = await asyncio.to_thread(_load)
        self._reset_states()
        log.info("Silero VAD loaded")

    def _reset_states(self) -> None:
        self._state = np.zeros(STATE_SHAPE, dtype=np.float32)
        self._context = np.zeros((1, CONTEXT_SAMPLES), dtype=np.float32)

    async def is_speech(self, chunk: AudioChunk) -> bool:
        if self._session is None:
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

        speaking, leftover = await asyncio.to_thread(self._process, combined, self._last_speaking)
        self._last_speaking = speaking
        self._buffer = leftover
        return speaking

    def _process(self, buf: np.ndarray, last: bool) -> tuple[bool, np.ndarray]:
        """Run each full window through the ONNX model, carrying context+state.

        Mirrors silero_vad's OnnxWrapper.__call__: prefix the 64-sample context,
        feed [input, state, sr], then keep the tail as the next context and the
        returned state for the next window.
        """
        assert self._session is not None
        speaking = last
        sr = np.array(EXPECTED_RATE, dtype=np.int64)
        num = buf.size // WINDOW_SAMPLES
        for i in range(num):
            window = buf[i * WINDOW_SAMPLES : (i + 1) * WINDOW_SAMPLES].reshape(1, WINDOW_SAMPLES)
            x = np.concatenate([self._context, window], axis=1).astype(np.float32)
            out, new_state = self._session.run(
                None, {"input": x, "state": self._state, "sr": sr}
            )
            self._state = new_state
            self._context = x[:, -CONTEXT_SAMPLES:]
            speaking = float(out[0, 0]) > self._threshold
        leftover = buf[num * WINDOW_SAMPLES :]
        return speaking, leftover

    @property
    def info(self) -> BackendInfo:
        return BackendInfo(
            name="silero_vad",
            version="silero-vad-onnx",
            model="silero_vad.onnx",
            device="cpu",
            capabilities={"threshold": self._threshold},
        )
