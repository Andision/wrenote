"""llama.cpp chat backend (Qwen3.5-4B-Instruct default).

Reuses the same single-worker-thread pattern as the translator so that
llama.cpp + Metal doesn't see concurrent C calls. Streams via the model's
own iterator, hopping each chunk back to the asyncio loop through an
asyncio.Queue so the WS pump can write to the socket without blocking.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import os
from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import Any

from ..core.events import BackendInfo
from ..core.registry import register_chat
from .base import ChatBackend, ChatMessage

log = logging.getLogger(__name__)


@register_chat("llama_cpp")
class LlamaCppChat(ChatBackend):
    def __init__(
        self,
        *,
        model_path: str,
        n_ctx: int = 8192,
        n_gpu_layers: int = -1,
        n_threads: int | None = None,
    ) -> None:
        self._model_path = str(Path(model_path).expanduser().resolve())
        self._n_ctx = n_ctx
        self._n_gpu_layers = n_gpu_layers
        self._n_threads = n_threads or max(1, (os.cpu_count() or 4) - 1)
        self._llm: Any = None
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="chat"
        )

    async def load(self) -> None:
        if self._llm is not None:
            return
        path = Path(self._model_path)
        if not path.exists():
            raise FileNotFoundError(f"Chat model not found at {self._model_path}")

        def _load() -> Any:
            from llama_cpp import Llama

            return Llama(
                model_path=self._model_path,
                n_ctx=self._n_ctx,
                n_gpu_layers=self._n_gpu_layers,
                n_threads=self._n_threads,
                verbose=False,
            )

        log.info(
            "Loading chat model from %s (n_gpu_layers=%d, n_ctx=%d)",
            self._model_path,
            self._n_gpu_layers,
            self._n_ctx,
        )
        loop = asyncio.get_event_loop()
        self._llm = await loop.run_in_executor(self._executor, _load)
        log.info("Chat model loaded")

    async def unload(self) -> None:
        def _drop() -> None:
            self._llm = None
        if self._llm is not None:
            try:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(self._executor, _drop)
            except RuntimeError:
                self._llm = None
        self._executor.shutdown(wait=False)

    async def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        max_tokens: int = 1024,
        temperature: float = 0.7,
    ) -> AsyncIterator[str]:
        if self._llm is None:
            raise RuntimeError("LlamaCppChat not loaded; call load() first")

        loop = asyncio.get_event_loop()
        # Sentinel object marks the end of stream (None would collide with
        # an actual empty-string chunk if the model ever emits one).
        DONE = object()
        queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=256)

        # llama.cpp's chat-completion streaming is a blocking generator. We
        # drive it on the dedicated executor and shovel each chunk back to
        # the asyncio loop via call_soon_threadsafe so the consumer (WS
        # pump) can await on a normal asyncio.Queue.
        def _produce() -> None:
            try:
                resp = self._llm.create_chat_completion(
                    messages=[{"role": m.role, "content": m.content} for m in messages],
                    max_tokens=max_tokens,
                    temperature=temperature,
                    stream=True,
                )
                for chunk in resp:
                    delta = chunk.get("choices", [{}])[0].get("delta", {}) or {}
                    piece = delta.get("content")
                    if piece:
                        loop.call_soon_threadsafe(queue.put_nowait, piece)
            except Exception as e:
                log.exception("chat stream failed")
                loop.call_soon_threadsafe(queue.put_nowait, e)
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, DONE)

        self._executor.submit(_produce)

        async def _iter() -> AsyncIterator[str]:
            while True:
                item = await queue.get()
                if item is DONE:
                    return
                if isinstance(item, Exception):
                    raise item
                yield item  # type: ignore[misc]

        return _iter()

    @property
    def info(self) -> BackendInfo:
        return BackendInfo(
            name="llama_cpp_chat",
            version="llama-cpp-python",
            model=Path(self._model_path).stem,
            device="metal-or-cuda",
            supported_languages=[],
            capabilities={
                "n_ctx": self._n_ctx,
                "n_gpu_layers": self._n_gpu_layers,
            },
        )
