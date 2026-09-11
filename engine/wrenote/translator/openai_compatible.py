"""Translation over ``POST /v1/chat/completions``.

The same endpoint the chat backend uses, asked a different question: the
prompt is :mod:`wrenote.translator.prompt`, identical to the one the llama.cpp
backend builds, so pointing this at a local `llama-server` running Hy-MT2
reproduces today's behaviour over a socket.

Not streamed: :meth:`translate` returns one string, and the pipeline emits a
segment's translation when it is whole.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping, Sequence
from typing import Any

from ..core.events import BackendInfo
from ..core.openai_compat import ChatCompletionsClient, strip_reasoning
from ..core.registry import register_translator
from .base import TranslatorBackend
from .prompt import LANG_NAMES, build_prompt

log = logging.getLogger(__name__)


@register_translator("openai_compatible")
class OpenAICompatibleTranslator(TranslatorBackend):
    def __init__(
        self,
        *,
        base_url: str = "",
        model: str = "",
        api_key: str = "",
        api_key_env: str = "",
        headers: Mapping[str, str] | None = None,
        extra_body: Mapping[str, Any] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 512,
        max_concurrency: int = 1,
        **_ignored: object,
    ) -> None:
        # See OpenAICompatibleChat.__init__ on the swallowed kwargs: `n_ctx`
        # and `n_gpu_layers` outlive a backend switch in the config file.
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._glossary_text: str = ""
        self._endpoint = ChatCompletionsClient(
            base_url=base_url,
            model=model,
            api_key=api_key,
            api_key_env=api_key_env,
            headers=headers,
            extra_body=extra_body,
            max_concurrency=max_concurrency,
        )

    async def load(self) -> None:
        self._endpoint.open()
        log.info(
            "translator over HTTP at %s (model=%s)",
            self._endpoint.url,
            self._endpoint.model or "server default",
        )

    async def unload(self) -> None:
        await self._endpoint.aclose()

    async def translate(
        self,
        text: str,
        *,
        src: str,
        tgt: str,
        timeout_s: float = 10.0,
        context: Sequence[str] = (),
    ) -> str:
        text = text.strip()
        if not text:
            return ""
        prompt = build_prompt(
            text, src=src, tgt=tgt, context=context, glossary_text=self._glossary_text
        )
        # `wait_for` rather than trusting the HTTP timeout alone: the interface
        # promises asyncio.TimeoutError at `timeout_s`, and the pipeline turns
        # exactly that into a TRANSLATION_TIMEOUT the session can carry on from.
        raw = await asyncio.wait_for(
            self._endpoint.complete(
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
            name="openai_compatible_translator",
            version="http",
            model=self._endpoint.model or "server default",
            device="local-http" if self._endpoint.local else "remote-http",
            supported_languages=list(LANG_NAMES),
            capabilities={
                "base_url": self._endpoint.base_url,
                "temperature": self._temperature,
            },
        )
