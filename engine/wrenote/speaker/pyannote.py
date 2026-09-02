"""pyannote/embedding speaker embedding backend (256-dim).

Per design: this is the alternative to the default ECAPA backend. Requires
the user to accept the `pyannote/embedding` license on HuggingFace and
have ``HF_TOKEN`` in the environment for the first download (after that
the model lives in ``~/.cache/huggingface/`` and inference is fully
offline).

To use:
    1. Visit https://hf.co/pyannote/embedding and Accept
    2. export HF_TOKEN=hf_...
    3. set speaker.backend: pyannote in config.yaml
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import os
from typing import Any

import numpy as np

from ..core.events import BackendInfo
from ..core.registry import register_speaker
from .base import SpeakerBackend

log = logging.getLogger(__name__)


@register_speaker("pyannote")
class PyannoteSpeakerBackend(SpeakerBackend):
    def __init__(
        self,
        *,
        model_name: str = "pyannote/embedding",
        device: str = "auto",
    ) -> None:
        self._model_name = model_name
        self._device = device
        self._model: Any = None
        self._torch_device: Any = None
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="pyannote-embed"
        )

    async def load(self) -> None:
        if self._model is not None:
            return
        token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
        if not token:
            log.warning(
                "HF_TOKEN not set — first-time download of %s will fail. "
                "Accept the model license at https://hf.co/%s and export HF_TOKEN.",
                self._model_name, self._model_name,
            )

        def _load() -> tuple[Any, Any]:
            import torch
            from pyannote.audio import Model

            kwargs = {}
            if token:
                kwargs["token"] = token
            model = Model.from_pretrained(self._model_name, **kwargs)
            model.eval()
            if self._device == "auto":
                if torch.backends.mps.is_available():
                    dev = torch.device("mps")
                elif torch.cuda.is_available():
                    dev = torch.device("cuda")
                else:
                    dev = torch.device("cpu")
            else:
                dev = torch.device(self._device)
            model.to(dev)
            return model, dev

        log.info("Loading %s ...", self._model_name)
        loop = asyncio.get_event_loop()
        self._model, self._torch_device = await loop.run_in_executor(self._executor, _load)
        log.info("Pyannote embedding loaded on device=%s", self._torch_device)

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
            raise RuntimeError("PyannoteSpeakerBackend not loaded; call load() first")
        if sample_rate != 16_000:
            raise ValueError(f"pyannote/embedding expects 16 kHz audio; got {sample_rate} Hz")

        def _embed() -> np.ndarray:
            import torch

            audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
            if audio.size == 0:
                return np.zeros(256, dtype=np.float32)
            wav = torch.from_numpy(audio).unsqueeze(0).unsqueeze(0).to(self._torch_device)
            with torch.no_grad():
                emb = self._model(wav)
            return emb.squeeze().cpu().numpy().astype(np.float32)

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self._executor, _embed)

    @property
    def info(self) -> BackendInfo:
        dev = str(self._torch_device) if self._torch_device else self._device
        return BackendInfo(
            name="pyannote",
            version="pyannote.audio-4.x",
            model=self._model_name,
            device=dev,
            capabilities={"embedding_dim": 256, "gated": True},
        )
