"""Chat over ``POST /v1/chat/completions`` — anything that speaks OpenAI's shape.

One backend for three cases that used to want three: a model server on this
machine (`llama-server`, Ollama, LM Studio), a shim in front of a CLI agent
(``docs/plans/CLI_AGENT_BACKENDS.md`` §0 — that is how the tools that do this
well are built), and a hosted API. They differ by ``base_url``, not by code.

Nothing is loaded here, so nothing can crash the engine and nothing has to be
released: ``load()`` opens a connection pool and ``unload()`` closes it. That
is the point of ``docs/plans/LLM_OUT_OF_PROCESS.md``.
"""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any

from ..core.events import BackendInfo
from ..core.openai_compat import ChatCompletionsClient
from ..core.registry import register_chat
from .base import ChatBackend, ChatMessage

log = logging.getLogger(__name__)


@register_chat("openai_compatible")
class OpenAICompatibleChat(ChatBackend):
    def __init__(
        self,
        *,
        base_url: str = "",
        model: str = "",
        api_key: str = "",
        api_key_env: str = "",
        headers: Mapping[str, str] | None = None,
        extra_body: Mapping[str, Any] | None = None,
        timeout_s: float = 120.0,
        **_ignored: object,
    ) -> None:
        # Extra kwargs are swallowed for the same reason MockChat swallows
        # them: switching `chat.backend` to this one leaves `n_ctx` and
        # `n_gpu_layers` sitting in the config, and a TypeError at start-up is
        # a poor way to learn that llama.cpp's tuning doesn't apply over HTTP.
        self._endpoint = ChatCompletionsClient(
            base_url=base_url,
            model=model,
            api_key=api_key,
            api_key_env=api_key_env,
            headers=headers,
            extra_body=extra_body,
            timeout_s=timeout_s,
        )

    async def load(self) -> None:
        self._endpoint.open()
        log.info(
            "chat over HTTP at %s (model=%s)",
            self._endpoint.url,
            self._endpoint.model or "server default",
        )

    async def unload(self) -> None:
        await self._endpoint.aclose()

    async def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        max_tokens: int = 1024,
        temperature: float = 0.7,
    ) -> AsyncIterator[str]:
        return self._endpoint.stream(
            [{"role": m.role, "content": m.content} for m in messages],
            max_tokens=max_tokens,
            temperature=temperature,
        )

    @property
    def info(self) -> BackendInfo:
        return BackendInfo(
            name="openai_compatible_chat",
            version="http",
            model=self._endpoint.model or "server default",
            # What the person reading /v1/info wants to know is not the
            # accelerator — there isn't one here — but whether this leaves
            # the machine.
            device="local-http" if self._endpoint.local else "remote-http",
            capabilities={"base_url": self._endpoint.base_url},
        )
