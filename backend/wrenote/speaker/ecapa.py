"""ECAPA-TDNN speaker embedding backend (ONNX Runtime — torch-free).

The SpeechBrain ECAPA model is exported once to a single-file ONNX (see
``packaging/export_ecapa_onnx.py``) that takes a raw 16 kHz mono waveform and
returns the 192-dim embedding — bit-for-bit identical to the torch model
(verified cos-sim = 1.0), so clustering behaviour is unchanged. At runtime we
need only onnxruntime; no torch / speechbrain.

Per spike validation: on real podcast / conversation audio, ECAPA on segments
≥ 1 s gives ARI = 1.0 against pyannote pipeline ground truth.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

import numpy as np

from ..core.events import BackendInfo
from ..core.registry import register_speaker
from .base import SpeakerBackend

log = logging.getLogger(__name__)

EMBED_DIM = 192
DEFAULT_MODEL_PATH = Path("~/.wrenote/models/spkrec-ecapa-voxceleb.onnx").expanduser()


@register_speaker("ecapa")
class EcapaSpeakerBackend(SpeakerBackend):
    def __init__(self, *, model_path: str | None = None, device: str = "cpu") -> None:
        self._model_path = (
            str(Path(model_path).expanduser()) if model_path else str(DEFAULT_MODEL_PATH)
        )
        self._device = device  # informational; ONNX Runtime CPU EP
        self._session: Any = None
        self._input_name = "wav"

    async def load(self) -> None:
        if self._session is not None:
            return

        def _load() -> Any:
            import onnxruntime as ort

            path = Path(self._model_path)
            if not path.exists():
                raise FileNotFoundError(f"ECAPA ONNX model not found at {self._model_path}")
            opts = ort.SessionOptions()
            opts.inter_op_num_threads = 1
            opts.intra_op_num_threads = 1
            return ort.InferenceSession(
                self._model_path, sess_options=opts, providers=["CPUExecutionProvider"]
            )

        log.info("Loading ECAPA-TDNN speaker embedding model (ONNX)")
        self._session = await asyncio.to_thread(_load)
        self._input_name = self._session.get_inputs()[0].name
        log.info("ECAPA model loaded")

    async def unload(self) -> None:
        self._session = None

    async def embed(self, pcm: bytes, sample_rate: int = 16_000) -> np.ndarray:
        if self._session is None:
            raise RuntimeError("EcapaSpeakerBackend not loaded; call load() first")
        if sample_rate != 16_000:
            raise ValueError(f"ECAPA expects 16 kHz audio; got {sample_rate} Hz")

        def _embed() -> np.ndarray:
            audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
            if audio.size == 0:
                return np.zeros(EMBED_DIM, dtype=np.float32)
            out = self._session.run(None, {self._input_name: audio[None, :]})[0]
            return np.asarray(out, dtype=np.float32).reshape(-1)[:EMBED_DIM]

        return await asyncio.to_thread(_embed)

    @property
    def info(self) -> BackendInfo:
        return BackendInfo(
            name="ecapa",
            version="ecapa-onnx",
            model="spkrec-ecapa-voxceleb.onnx",
            device=self._device,
            capabilities={"embedding_dim": EMBED_DIM},
        )
