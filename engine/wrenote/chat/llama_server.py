"""Chat on a `llama-server` this engine starts and supervises.

The local case, once local and remote stopped being different things: the
weights come from the model catalogue exactly as they do for the in-process
backend, and everything after that is the HTTP path step 1 already proved. A
llama.cpp segfault now kills a subprocess instead of the recording.

Takes the same ``model_path`` / ``n_ctx`` / ``n_gpu_layers`` the in-process
backend takes, so switching a slot between them is a backend name and nothing
else — see ``catalogue.backend_can_run``.
"""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Sequence
from pathlib import Path

from ..core.config import DEFAULT_DATA_DIR
from ..core.events import BackendInfo
from ..core.llama_server import ManagedEndpoint, resolve_binary
from ..core.registry import register_chat
from .base import ChatBackend, ChatMessage

log = logging.getLogger(__name__)


@register_chat("llama_server")
class LlamaServerChat(ChatBackend):
    def __init__(
        self,
        *,
        model_path: str,
        binary: str = "",
        n_ctx: int = 8192,
        n_gpu_layers: int = -1,
        extra_args: Sequence[str] = (),
        state_dir: str = "",
        runtimes_dir: str = "",
        **_ignored: object,
    ) -> None:
        self._model_path = Path(model_path).expanduser()
        self._binary = binary
        self._runtimes_dir = runtimes_dir
        self._n_ctx = n_ctx
        self._n_gpu_layers = n_gpu_layers
        self._extra_args = list(extra_args)
        self._state_dir = Path(state_dir or Path(DEFAULT_DATA_DIR).expanduser())
        self._endpoint: ManagedEndpoint | None = None

    async def load(self) -> None:
        if self._endpoint is not None:
            return
        endpoint = ManagedEndpoint(
            model_path=self._model_path,
            state_dir=self._state_dir,
            label="chat",
            binary=resolve_binary(
                self._binary,
                runtimes_dir=Path(self._runtimes_dir) if self._runtimes_dir else None,
            ),
            n_ctx=self._n_ctx,
            n_gpu_layers=self._n_gpu_layers,
            extra_args=self._extra_args,
        )
        await endpoint.open()
        self._endpoint = endpoint

    async def unload(self) -> None:
        endpoint, self._endpoint = self._endpoint, None
        if endpoint is not None:
            await endpoint.aclose()

    async def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        max_tokens: int = 1024,
        temperature: float = 0.7,
    ) -> AsyncIterator[str]:
        if self._endpoint is None:
            raise RuntimeError("LlamaServerChat not loaded; call load() first")
        return self._endpoint.client.stream(
            [{"role": m.role, "content": m.content} for m in messages],
            max_tokens=max_tokens,
            temperature=temperature,
        )

    @property
    def info(self) -> BackendInfo:
        return BackendInfo(
            name="llama_server_chat",
            version="llama-server",
            model=self._model_path.stem,
            # Local, and stays local: the server is a subprocess on loopback.
            device="managed-http",
            capabilities={"n_ctx": self._n_ctx, "n_gpu_layers": self._n_gpu_layers},
        )
