"""One HTTP client for every language model the engine talks to.

Both places the engine uses an LLM — the chat panel and the translator — need
exactly one thing from it: ``POST {base_url}/chat/completions``. That is the
shape `llama-server`, Ollama, LM Studio, vLLM, a CLI shim like
`claw-orchestrator` and the hosted APIs all speak, so pointing a ``base_url``
somewhere is the whole configuration. See ``docs/plans/LLM_OUT_OF_PROCESS.md``:
this module is step 1, and step 2 (the engine supervising its own
`llama-server`) reuses it unchanged rather than adding a second code path.

Nothing here knows about chat or translation. It takes ``{"role", "content"}``
dicts and returns text, streaming or not, which is all either caller needed
from ``create_chat_completion`` in the first place.

**Privacy.** A ``base_url`` on loopback is still local inference — the app's
"nothing leaves your device" claim holds for a `llama-server` on 127.0.0.1 and
does not hold for anything else. :func:`remote_slots` is that distinction, and
the client renders it; see ``GET /v1/models/status``.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import os
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

import httpx2 as httpx

if TYPE_CHECKING:
    from .config import Config

log = logging.getLogger(__name__)

#: Backends that reach a model over HTTP rather than loading it. The list also
#: lives in :mod:`wrenote.core.catalogue`, which cannot import this module
#: without dragging an HTTP client into every import of the config; a test
#: asserts the two agree.
HTTP_BACKENDS = ("openai_compatible",)

#: Slots that may be answered over HTTP. Speech recognition is not among them
#: and is not going to be: the live path is coupled to the VAD, to partials and
#: to per-segment language policy, so it is not a request/response shape. See
#: "What stays embedded" in docs/plans/LLM_OUT_OF_PROCESS.md.
ENDPOINT_SLOTS = ("translator", "chat")

#: One message, as the wire wants it. Not ``chat.base.ChatMessage``: ``core``
#: does not import backends, and the translator has no chat history anyway.
Message = Mapping[str, str]


class LLMHTTPError(RuntimeError):
    """The endpoint answered, but not with a completion.

    Carries the status and a short excerpt of the body — enough to tell a wrong
    ``model`` from an expired key from a server that isn't running — and never
    the request headers, which is where the API key is.
    """

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def chat_completions_url(base_url: str) -> str:
    """The endpoint to POST to, from whatever the user wrote in the config.

    Accepts the three things people paste: the OpenAI-style base that already
    ends in ``/v1``, a bare origin (``http://127.0.0.1:8080``), and the full
    completions URL. A bare origin gets ``/v1`` — every server in this family
    mounts there, and guessing wrong is a 404 the user can read.
    """
    url = base_url.strip().rstrip("/")
    if not url:
        raise ValueError("base_url is empty")
    if url.endswith("/chat/completions"):
        return url
    if not urlsplit(url).path.strip("/"):
        url = f"{url}/v1"
    return f"{url}/chat/completions"


def is_loopback_url(url: str) -> bool:
    """Whether ``url`` addresses this machine, so inference is still local.

    A LAN address is *not* loopback: another box on the network is somewhere
    else as far as the privacy claim is concerned, even if it is under the
    same desk.
    """
    host = (urlsplit(url.strip()).hostname or "").strip()
    if not host:
        return False
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def strip_reasoning(text: str) -> str:
    """Drop a leading ``<think>…</think>`` block.

    Reasoning models are ordinary members of this family — a shim in front of a
    CLI agent is usually one — and they put their scratchpad in ``content``
    where a non-streaming caller has no way to tell it from the answer. The
    chat panel wants the model's prose as it comes; a translation that begins
    "Let me consider the register here" is just wrong, so the translator asks
    for this. Servers that put reasoning in ``reasoning_content`` instead are
    already handled: this module only ever reads ``content``.
    """
    body = text.lstrip()
    if not body.startswith("<think>"):
        return text
    end = body.find("</think>")
    return body if end < 0 else body[end + len("</think>") :].lstrip()


class ChatCompletionsClient:
    """A configured ``/chat/completions`` endpoint, streaming or not.

    ``api_key_env`` names an environment variable to read the key from at
    request time, which is the way to use a hosted API without writing the key
    into ``~/.wrenote/config.yaml`` (and, via ``GET /v1/info``, into whatever
    the user pastes into a bug report — see :meth:`Config.redacted_dump`).

    ``max_concurrency`` defaults to 1 because that is what the engine does
    today: both llama.cpp backends serialise on a single worker thread, and the
    live pipeline's partial- and final-translation loops rely on nothing more
    than that. Raise it for a hosted endpoint that is happy to be asked twice
    at once.
    """

    def __init__(
        self,
        *,
        base_url: str,
        model: str = "",
        api_key: str = "",
        api_key_env: str = "",
        headers: Mapping[str, str] | None = None,
        extra_body: Mapping[str, Any] | None = None,
        timeout_s: float = 120.0,
        connect_timeout_s: float = 10.0,
        max_concurrency: int = 1,
    ) -> None:
        self._base_url = str(base_url or "")
        self._model = str(model or "")
        self._api_key = str(api_key or "")
        self._api_key_env = str(api_key_env or "")
        self._headers = {str(k): str(v) for k, v in (headers or {}).items()}
        self._extra_body = dict(extra_body or {})
        self._timeout_s = float(timeout_s)
        self._connect_timeout_s = float(connect_timeout_s)
        self._sem = asyncio.Semaphore(max(1, int(max_concurrency)))
        self._client: httpx.AsyncClient | None = None

    # --- lifecycle --------------------------------------------------------

    @property
    def model(self) -> str:
        return self._model

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def url(self) -> str:
        return chat_completions_url(self._base_url)

    @property
    def local(self) -> bool:
        """Whether this endpoint is on loopback (see :func:`is_loopback_url`)."""
        return is_loopback_url(self._base_url)

    def open(self) -> None:
        """Build the connection pool. Cheap, and deliberately does not probe.

        A reachability check here would cost a round trip on every start-up and
        buy an error message the first request produces anyway — and some
        shims implement ``/chat/completions`` and nothing else, so there is no
        probe that works everywhere.
        """
        if self._client is not None:
            return
        # Validate now rather than at the first token: "base_url is empty" is a
        # config mistake, and it should read as one.
        chat_completions_url(self._base_url)
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self._timeout_s, connect=self._connect_timeout_s),
        )

    async def aclose(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            await client.aclose()

    # --- requests ---------------------------------------------------------

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        max_tokens: int = 512,
        temperature: float = 0.0,
        timeout_s: float | None = None,
    ) -> str:
        """The whole reply, in one response. Used where streaming buys nothing."""
        client = self._require_open()
        payload = self._payload(messages, max_tokens=max_tokens, temperature=temperature, stream=False)
        async with self._sem:
            try:
                # `timeout=None` means *no* timeout in httpx, not "the
                # client's": the sentinel is what asks for the default.
                timeout: Any = httpx.USE_CLIENT_DEFAULT
                if timeout_s is not None:
                    timeout = httpx.Timeout(
                        timeout_s, connect=min(self._connect_timeout_s, timeout_s)
                    )
                resp = await client.post(
                    self.url,
                    json=payload,
                    headers=self._request_headers(),
                    timeout=timeout,
                )
            except httpx.TimeoutException as e:
                raise TimeoutError(f"{self.url} did not answer in time") from e
            except httpx.RequestError as e:
                raise LLMHTTPError(f"cannot reach {self.url}: {type(e).__name__}: {e}") from e
            self._raise_for_status(resp, await resp.aread())
            return _content_of(resp.json())

    def stream(
        self,
        messages: Sequence[Message],
        *,
        max_tokens: int = 1024,
        temperature: float = 0.7,
    ) -> AsyncIterator[str]:
        """Text chunks as the model produces them.

        Returns the iterator rather than being one, so the caller's ``await``
        happens before the first byte — matching ``ChatBackend.chat``.
        """
        client = self._require_open()
        payload = self._payload(messages, max_tokens=max_tokens, temperature=temperature, stream=True)
        return self._stream(client, payload)

    async def _stream(
        self, client: httpx.AsyncClient, payload: dict[str, Any]
    ) -> AsyncIterator[str]:
        async with self._sem:
            try:
                async with client.stream(
                    "POST", self.url, json=payload, headers=self._request_headers()
                ) as resp:
                    if resp.status_code >= 400:
                        self._raise_for_status(resp, await resp.aread())
                    # A server that ignored `stream: true` answers with one
                    # JSON object. Yielding nothing would look like a model
                    # with nothing to say, so read it as the reply it is.
                    content_type = resp.headers.get("content-type", "")
                    if "text/event-stream" not in content_type.lower():
                        body = await resp.aread()
                        text = _content_of(_loads(body.decode("utf-8", "replace")))
                        if text:
                            yield text
                        return
                    async for piece in _sse_deltas(resp):
                        yield piece
            except httpx.TimeoutException as e:
                raise TimeoutError(f"{self.url} stopped answering") from e
            except httpx.RequestError as e:
                raise LLMHTTPError(f"cannot reach {self.url}: {type(e).__name__}: {e}") from e

    # --- internals --------------------------------------------------------

    def _require_open(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("endpoint not opened; call load() first")
        return self._client

    def _payload(
        self, messages: Sequence[Message], *, max_tokens: int, temperature: float, stream: bool
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "messages": [{"role": m["role"], "content": m["content"]} for m in messages],
            "stream": stream,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        # `llama-server` hosts one model and ignores the field; a hosted API
        # requires it. Omitted when unset so the local case needs no answer to
        # "which model?" that it doesn't have.
        if self._model:
            body["model"] = self._model
        # Last word to the user: this is where `top_p`, a provider's own
        # `reasoning_effort`, or `max_completion_tokens` for an endpoint that
        # rejects `max_tokens` go.
        body.update(self._extra_body)
        return body

    def _request_headers(self) -> dict[str, str]:
        headers = {"accept": "application/json", **self._headers}
        key = self._api_key or (os.environ.get(self._api_key_env, "") if self._api_key_env else "")
        if key:
            headers.setdefault("authorization", f"Bearer {key}")
        return headers

    def _raise_for_status(self, resp: httpx.Response, body: bytes) -> None:
        if resp.status_code < 400:
            return
        excerpt = body.decode("utf-8", "replace").strip().replace("\n", " ")[:200]
        hint = ""
        if resp.status_code in (401, 403):
            hint = " (the endpoint rejected the API key)"
        elif resp.status_code == 404:
            hint = " (wrong base_url, or the model is not served here)"
        raise LLMHTTPError(
            f"{self.url} returned {resp.status_code}{hint}: {excerpt}",
            status=resp.status_code,
        )


async def _sse_deltas(resp: httpx.Response) -> AsyncIterator[str]:
    """Assistant text out of an OpenAI-style ``text/event-stream``.

    Only ``choices[0].delta.content`` is text. ``reasoning_content`` (DeepSeek,
    Qwen and the shims that copy them) is deliberately dropped, a trailing
    usage-only chunk carries no ``choices``, and a mid-stream ``error`` object
    is the server's way of failing after the headers were already sent.
    """
    async for raw in resp.aiter_lines():
        line = raw.strip()
        if not line or line.startswith(":") or not line.startswith("data:"):
            continue
        data = line[len("data:") :].strip()
        if data == "[DONE]":
            return
        chunk = _loads(data)
        if not isinstance(chunk, dict):
            continue
        if chunk.get("error"):
            raise LLMHTTPError(f"the endpoint failed mid-stream: {chunk['error']}")
        choices = chunk.get("choices") or []
        if not choices:
            continue
        delta = choices[0].get("delta") or {}
        piece = delta.get("content")
        if piece:
            yield str(piece)


def _loads(text: str) -> Any:
    """JSON, or ``None`` for a line we can't read. A single malformed chunk is
    not worth killing a reply that is otherwise arriving fine."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        log.debug("ignoring an unparseable chunk from the endpoint")
        return None


def _content_of(data: Any) -> str:
    """The assistant text out of a non-streaming completion."""
    if not isinstance(data, dict):
        raise LLMHTTPError("the endpoint's reply was not a JSON object")
    if data.get("error"):
        raise LLMHTTPError(f"the endpoint returned an error: {data['error']}")
    choices = data.get("choices") or []
    if not choices:
        raise LLMHTTPError("the endpoint returned no choices")
    message = choices[0].get("message") or {}
    return str(message.get("content") or "")


def slot_is_http(cfg: Config, slot: str) -> bool:
    """Whether ``slot`` is switched on *and* answered over HTTP."""
    from .catalogue import OPTIONAL_SLOTS

    section = getattr(cfg, slot, None)
    if section is None or section.backend not in HTTP_BACKENDS:
        return False
    return not (slot in OPTIONAL_SLOTS and not section.enabled)


def remote_slots(cfg: Config) -> list[str]:
    """Which model slots are configured to send text off this machine.

    An HTTP backend pointed at loopback is not one of them: that is the engine
    talking to a model server on the same box, which is what the local case
    becomes in step 2 of ``LLM_OUT_OF_PROCESS.md``. A slot the user switched
    off isn't either — it has no backend at all, and neither is one whose
    endpoint was never filled in.
    """
    from .catalogue import SLOTS

    remote: list[str] = []
    for slot in SLOTS:
        if not slot_is_http(cfg, slot):
            continue
        endpoint = getattr(getattr(cfg, slot), "endpoint", None)
        if endpoint is None or not endpoint.configured:
            continue
        if not is_loopback_url(endpoint.base_url):
            remote.append(slot)
    return remote


def endpoint_status(cfg: Config, slot: str) -> dict[str, Any]:
    """What the settings UI needs to render one slot's endpoint.

    The key itself never comes back — only whether there is one. A field the
    user cannot read is the point: they set it once, and the form afterwards
    says "saved" rather than round-tripping a secret through the browser on
    every settings open.
    """
    section = getattr(cfg, slot)
    endpoint = section.endpoint
    return {
        "active": slot_is_http(cfg, slot),
        "base_url": endpoint.base_url,
        "model": endpoint.model,
        "has_api_key": bool(endpoint.api_key),
        "api_key_env": endpoint.api_key_env,
        "timeout_s": endpoint.timeout_s,
        "configured": endpoint.configured,
        # The privacy verdict, decided here so the client renders one flag
        # rather than re-implementing the loopback rule in TypeScript.
        "local": endpoint.configured and is_loopback_url(endpoint.base_url),
    }
