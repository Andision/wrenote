"""ECAPA-TDNN speaker embedding backend (via SpeechBrain).

Per spike validation: on real podcast / conversation audio, ECAPA on
segments ≥ 1 s gives ARI = 1.0 against pyannote pipeline ground truth.
192-dim embeddings, CPU inference ~36 ms / segment on M1 Max — negligible
compared to STT.

Non-gated model, no HuggingFace token required.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import logging
from typing import Any

import numpy as np

from ..core.events import BackendInfo
from ..core.registry import register_speaker
from .base import SpeakerBackend

log = logging.getLogger(__name__)


@register_speaker("ecapa")
class EcapaSpeakerBackend(SpeakerBackend):
    def __init__(
        self,
        *,
        cache_dir: str = "/tmp/spkrec-ecapa-voxceleb",
        device: str = "cpu",
    ) -> None:
        self._cache_dir = cache_dir
        self._device = device
        self._model: Any = None
        # Same single-thread executor pattern as STT / Translator: torch
        # models are not safe under concurrent threaded calls.
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="ecapa"
        )

    async def load(self) -> None:
        if self._model is not None:
            return

        def _load() -> Any:
            from speechbrain.inference.speaker import EncoderClassifier

            return EncoderClassifier.from_hparams(
                source="speechbrain/spkrec-ecapa-voxceleb",
                savedir=self._cache_dir,
                run_opts={"device": self._device},
            )

        log.info("Loading ECAPA-TDNN speaker embedding model")
        loop = asyncio.get_event_loop()
        self._model = await loop.run_in_executor(self._executor, _load)
        log.info("ECAPA model loaded")

    async def unload(self) -> None:
        def _drop() -> None:
            self._model = None
        if self._model is not None:
            try:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(self._executor, _drop)
            except RuntimeError:
                self._model = None
        self._executor.shutdown(wait=False)

    async def embed(self, pcm: bytes, sample_rate: int = 16_000) -> np.ndarray:
        if self._model is None:
            raise RuntimeError("EcapaSpeakerBackend not loaded; call load() first")
        if sample_rate != 16_000:
            raise ValueError(f"ECAPA expects 16 kHz audio; got {sample_rate} Hz")

        def _embed() -> np.ndarray:
            import torch

            audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
            if audio.size == 0:
                return np.zeros(192, dtype=np.float32)
            with torch.no_grad():
                wav = torch.from_numpy(audio).unsqueeze(0)
                emb = self._model.encode_batch(wav)
            return emb.squeeze().cpu().numpy().astype(np.float32)

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self._executor, _embed)

    @property
    def info(self) -> BackendInfo:
        return BackendInfo(
            name="ecapa",
            version="speechbrain-ecapa-tdnn",
            model="spkrec-ecapa-voxceleb",
            device=self._device,
            capabilities={"embedding_dim": 192},
        )
