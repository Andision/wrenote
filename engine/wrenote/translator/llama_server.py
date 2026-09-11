"""Translation on a `llama-server` this engine starts and supervises.

Same prompt as every other translator (:mod:`wrenote.translator.prompt`) and
the same HTTP path as ``openai_compatible``; the only difference is who
started the process on the other end. See :mod:`wrenote.chat.llama_server`.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from pathlib import Path

from ..core.config import DEFAULT_DATA_DIR
from ..core.events import BackendInfo
from ..core.llama_server import ManagedEndpoint, resolve_binary
from ..core.openai_compat import strip_reasoning
from ..core.registry import register_translator
from .base import TranslatorBackend
from .prompt import LANG_NAMES, build_prompt

log = logging.getLogger(__name__)


@register_translator("llama_server")
class LlamaServerTranslator(TranslatorBackend):
    def __init__(
        self,
        *,
        model_path: str,
        binary: str = "",
        n_ctx: int = 4096,
        n_gpu_layers: int = -1,
        temperature: float = 0.0,
        max_tokens: int = 512,
        extra_args: Sequence[str] = (),
        state_dir: str = "",
        runtimes_dir: str = "",
        max_concurrency: int = 1,
        **_ignored: object,
    ) -> None:
        self._model_path = Path(model_path).expanduser()
        self._binary = binary
        self._runtimes_dir = runtimes_dir
        self._n_ctx = n_ctx
        self._n_gpu_layers = n_gpu_layers
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._extra_args = list(extra_args)
        self._max_concurrency = max_concurrency
        self._state_dir = Path(state_dir or Path(DEFAULT_DATA_DIR).expanduser())
        self._glossary_text: str = ""
        self._endpoint: ManagedEndpoint | None = None

    async def load(self) -> None:
        if self._endpoint is not None:
            return
        endpoint = ManagedEndpoint(
            model_path=self._model_path,
            state_dir=self._state_dir,
            label="translator",
            binary=resolve_binary(
                self._binary,
                runtimes_dir=Path(self._runtimes_dir) if self._runtimes_dir else None,
            ),
            n_ctx=self._n_ctx,
            n_gpu_layers=self._n_gpu_layers,
            extra_args=self._extra_args,
            max_concurrency=self._max_concurrency,
        )
        await endpoint.open()
        self._endpoint = endpoint

    async def unload(self) -> None:
        endpoint, self._endpoint = self._endpoint, None
        if endpoint is not None:
            await endpoint.aclose()

    async def translate(
        self,
        text: str,
        *,
        src: str,
        tgt: str,
        timeout_s: float = 10.0,
        context: Sequence[str] = (),
    ) -> str:
        if self._endpoint is None:
            raise RuntimeError("LlamaServerTranslator not loaded; call load() first")
        text = text.strip()
        if not text:
            return ""
        prompt = build_prompt(
            text, src=src, tgt=tgt, context=context, glossary_text=self._glossary_text
        )
        raw = await asyncio.wait_for(
            self._endpoint.client.complete(
                [{"role": "user", "content": prompt}],
                max_tokens=self._max_tokens,
                temperature=self._temperature,
                timeout_s=timeout_s,
            ),
            timeout=timeout_s,
        )
        return strip_reasoning(raw).strip()

    def set_glossary(self, pairs: list[tuple[str, str]]) -> None:
        from ..core.glossary import mt_glossary_text

        self._glossary_text = mt_glossary_text(pairs)

    @property
    def info(self) -> BackendInfo:
        return BackendInfo(
            name="llama_server_translator",
            version="llama-server",
            model=self._model_path.stem,
            device="managed-http",
            supported_languages=list(LANG_NAMES),
            capabilities={
                "n_ctx": self._n_ctx,
                "n_gpu_layers": self._n_gpu_layers,
                "temperature": self._temperature,
            },
        )
