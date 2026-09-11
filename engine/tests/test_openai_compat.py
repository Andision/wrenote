"""The OpenAI-compatible HTTP backends: chat, translator, and what they leak.

Everything runs against ``httpx2.MockTransport`` — no server, no network. What
is worth pinning is the wire shape (both directions), the privacy rule that
decides what the UI claims, and the promise the translator interface makes
about timeouts.
"""
from __future__ import annotations

import asyncio
import json
import re

import httpx2 as httpx
import pytest

from wrenote.chat.base import ChatMessage
from wrenote.core import openai_compat as oc
from wrenote.core.config import Config
from wrenote.core.registry import make_chat, make_translator


def sse(*chunks: dict | str) -> bytes:
    """An OpenAI-style event stream, terminated the way servers terminate it."""
    lines = [f"data: {c if isinstance(c, str) else json.dumps(c)}\n\n" for c in chunks]
    return "".join(lines).encode()


def delta(text: str) -> dict:
    return {"choices": [{"index": 0, "delta": {"content": text}}]}


def completion(text: str) -> dict:
    return {"choices": [{"index": 0, "message": {"role": "assistant", "content": text}}]}


def mount(backend, handler) -> list[httpx.Request]:
    """Point a backend's endpoint at ``handler``, returning the requests it sees.

    The backend has to be ``load()``ed first — that is what builds the client
    this replaces, and doing it this way keeps ``load()`` in the test path.
    """
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    backend._endpoint._client = httpx.AsyncClient(transport=httpx.MockTransport(record))
    return seen


# ---------- the URL people paste ----------


@pytest.mark.parametrize(
    ("written", "expected"),
    [
        ("http://127.0.0.1:8080/v1", "http://127.0.0.1:8080/v1/chat/completions"),
        ("http://127.0.0.1:8080/v1/", "http://127.0.0.1:8080/v1/chat/completions"),
        # A bare origin: every server in this family mounts at /v1.
        ("http://localhost:11434", "http://localhost:11434/v1/chat/completions"),
        # Already the full URL — don't append a second time.
        (
            "https://api.example.com/v1/chat/completions",
            "https://api.example.com/v1/chat/completions",
        ),
        # A shim under a path keeps it.
        ("http://127.0.0.1:9000/claude", "http://127.0.0.1:9000/claude/chat/completions"),
    ],
)
def test_chat_completions_url(written, expected):
    assert oc.chat_completions_url(written) == expected


def test_empty_base_url_is_a_config_error():
    with pytest.raises(ValueError, match="base_url"):
        oc.chat_completions_url("   ")


# ---------- what "local" means ----------


@pytest.mark.parametrize(
    "url",
    ["http://127.0.0.1:8080/v1", "http://localhost:1234/v1", "http://[::1]:8080",
     "http://127.2.3.4:8080"],
)
def test_loopback_urls_are_local(url):
    assert oc.is_loopback_url(url)


@pytest.mark.parametrize(
    "url",
    # A box on the LAN is somewhere else as far as the privacy claim goes.
    ["https://api.openai.com/v1", "http://192.168.1.10:8080/v1", "http://gpu-box:8080", ""],
)
def test_everything_else_is_remote(url):
    assert not oc.is_loopback_url(url)


def _cfg(**slots) -> Config:
    base = {
        "stt": {"backend": "mock"},
        "stt_offline": {"backend": "mock"},
        "vad": {"backend": "disabled"},
        "translator": {"backend": "mock"},
        "speaker": {"backend": "disabled"},
        "chat": {"backend": "mock"},
    }
    base.update(slots)
    return Config.model_validate(base)


def test_remote_slots_names_only_what_leaves_the_machine():
    cfg = _cfg(
        chat={"backend": "openai_compatible",
              "endpoint": {"base_url": "https://api.example.com/v1"}},
        translator={"backend": "openai_compatible",
                    "endpoint": {"base_url": "http://127.0.0.1:8080/v1"}},
    )
    assert oc.remote_slots(cfg) == ["chat"]


def test_a_switched_off_feature_is_not_remote():
    """It has no backend at all — nothing is constructed, nothing is sent."""
    cfg = _cfg(
        chat={
            "backend": "openai_compatible",
            "enabled": False,
            "endpoint": {"base_url": "https://api.example.com/v1"},
        },
    )
    assert oc.remote_slots(cfg) == []


def test_the_default_config_is_local():
    assert oc.remote_slots(_cfg()) == []


# ---------- chat ----------


async def test_chat_streams_deltas_in_order():
    backend = make_chat("openai_compatible", {"base_url": "http://127.0.0.1:8080/v1"})
    await backend.load()
    mount(backend, lambda _r: httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        content=sse(delta("Hel"), delta("lo"), delta(" there"), "[DONE]"),
    ))
    out = [p async for p in await backend.chat([ChatMessage(role="user", content="hi")])]
    assert "".join(out) == "Hello there"
    await backend.unload()


async def test_chat_ignores_reasoning_and_a_usage_only_final_chunk():
    """A reasoning model's scratchpad is not the reply, and the usage chunk
    that some servers send after the last delta carries no `choices`."""
    backend = make_chat("openai_compatible", {"base_url": "http://127.0.0.1:8080/v1"})
    await backend.load()
    mount(backend, lambda _r: httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        content=sse(
            {"choices": [{"delta": {"reasoning_content": "hmm, the user wants…"}}]},
            delta("42"),
            {"choices": [], "usage": {"total_tokens": 9}},
            "[DONE]",
        ),
    ))
    out = [p async for p in await backend.chat([ChatMessage(role="user", content="hi")])]
    assert "".join(out) == "42"
    await backend.unload()


async def test_chat_reads_a_server_that_ignored_stream_true():
    """Yielding nothing would look like a model with nothing to say."""
    backend = make_chat("openai_compatible", {"base_url": "http://127.0.0.1:8080/v1"})
    await backend.load()
    mount(backend, lambda _r: httpx.Response(200, json=completion("whole answer")))
    out = [p async for p in await backend.chat([ChatMessage(role="user", content="hi")])]
    assert "".join(out) == "whole answer"
    await backend.unload()


async def test_chat_sends_the_history_as_given():
    backend = make_chat(
        "openai_compatible", {"base_url": "http://127.0.0.1:8080/v1", "model": "qwen3"}
    )
    await backend.load()
    seen = mount(backend, lambda _r: httpx.Response(
        200, headers={"content-type": "text/event-stream"}, content=sse("[DONE]"),
    ))
    messages = [
        ChatMessage(role="system", content="you are terse"),
        ChatMessage(role="user", content="who?"),
    ]
    async for _ in await backend.chat(messages, max_tokens=64, temperature=0.2):
        pass
    body = json.loads(seen[0].content)
    assert body["messages"] == [
        {"role": "system", "content": "you are terse"},
        {"role": "user", "content": "who?"},
    ]
    assert body["model"] == "qwen3" and body["stream"] is True
    assert body["max_tokens"] == 64 and body["temperature"] == 0.2
    await backend.unload()


async def test_no_model_key_when_the_server_hosts_one():
    """`llama-server` has exactly one model and no name for it."""
    backend = make_chat("openai_compatible", {"base_url": "http://127.0.0.1:8080/v1"})
    await backend.load()
    seen = mount(backend, lambda _r: httpx.Response(
        200, headers={"content-type": "text/event-stream"}, content=sse("[DONE]"),
    ))
    async for _ in await backend.chat([ChatMessage(role="user", content="hi")]):
        pass
    assert "model" not in json.loads(seen[0].content)
    await backend.unload()


async def test_a_rejected_key_says_so_without_printing_it():
    backend = make_chat(
        "openai_compatible",
        {"base_url": "https://api.example.com/v1", "api_key": "sk-do-not-log-me"},
    )
    await backend.load()
    mount(backend, lambda _r: httpx.Response(401, json={"error": {"message": "bad key"}}))
    with pytest.raises(oc.LLMHTTPError) as e:
        async for _ in await backend.chat([ChatMessage(role="user", content="hi")]):
            pass
    assert "401" in str(e.value) and "API key" in str(e.value)
    assert "sk-do-not-log-me" not in str(e.value)
    await backend.unload()


async def test_an_error_after_the_headers_still_raises():
    """A server that has already sent 200 fails by putting an error in the stream."""
    backend = make_chat("openai_compatible", {"base_url": "http://127.0.0.1:8080/v1"})
    await backend.load()
    mount(backend, lambda _r: httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        content=sse(delta("par"), {"error": {"message": "context overflow"}}),
    ))
    with pytest.raises(oc.LLMHTTPError, match="mid-stream"):
        async for _ in await backend.chat([ChatMessage(role="user", content="hi")]):
            pass
    await backend.unload()


async def test_the_key_travels_in_the_authorization_header():
    backend = make_chat(
        "openai_compatible",
        {"base_url": "https://api.example.com/v1", "api_key": "sk-abc",
         "headers": {"x-title": "wrenote"}},
    )
    await backend.load()
    seen = mount(backend, lambda _r: httpx.Response(
        200, headers={"content-type": "text/event-stream"}, content=sse("[DONE]"),
    ))
    async for _ in await backend.chat([ChatMessage(role="user", content="hi")]):
        pass
    assert seen[0].headers["authorization"] == "Bearer sk-abc"
    assert seen[0].headers["x-title"] == "wrenote"
    await backend.unload()


async def test_the_key_can_come_from_the_environment(monkeypatch):
    """So it never has to be written into ~/.wrenote/config.yaml."""
    monkeypatch.setenv("WRENOTE_TEST_KEY", "sk-from-env")
    backend = make_chat(
        "openai_compatible",
        {"base_url": "https://api.example.com/v1", "api_key_env": "WRENOTE_TEST_KEY"},
    )
    await backend.load()
    seen = mount(backend, lambda _r: httpx.Response(
        200, headers={"content-type": "text/event-stream"}, content=sse("[DONE]"),
    ))
    async for _ in await backend.chat([ChatMessage(role="user", content="hi")]):
        pass
    assert seen[0].headers["authorization"] == "Bearer sk-from-env"
    await backend.unload()


async def test_llama_cpp_tuning_left_in_the_config_is_ignored_not_fatal():
    """Switching `chat.backend` doesn't rewrite the params under it."""
    backend = make_chat(
        "openai_compatible",
        {"base_url": "http://127.0.0.1:8080/v1", "n_ctx": 32768, "n_gpu_layers": -1},
    )
    assert backend.info.name == "openai_compatible_chat"


def test_info_says_whether_it_leaves_the_machine():
    local = make_chat("openai_compatible", {"base_url": "http://127.0.0.1:8080/v1"})
    remote = make_chat("openai_compatible", {"base_url": "https://api.example.com/v1"})
    assert local.info.device == "local-http"
    assert remote.info.device == "remote-http"


async def test_chat_before_load_is_a_clear_error():
    backend = make_chat("openai_compatible", {"base_url": "http://127.0.0.1:8080/v1"})
    with pytest.raises(RuntimeError, match="load"):
        await backend.chat([ChatMessage(role="user", content="hi")])


# ---------- translator ----------


async def test_translate_returns_the_message_content():
    backend = make_translator("openai_compatible", {"base_url": "http://127.0.0.1:8080/v1"})
    await backend.load()
    mount(backend, lambda _r: httpx.Response(200, json=completion("  你好  ")))
    assert await backend.translate("hello", src="en", tgt="zh") == "你好"
    await backend.unload()


async def test_translate_sends_the_same_prompt_the_local_backend_builds():
    from wrenote.translator.prompt import build_prompt

    backend = make_translator("openai_compatible", {"base_url": "http://127.0.0.1:8080/v1"})
    await backend.load()
    seen = mount(backend, lambda _r: httpx.Response(200, json=completion("你好")))
    await backend.translate("hello", src="en", tgt="zh", context=["earlier line"])
    sent = json.loads(seen[0].content)["messages"][0]["content"]
    assert sent == build_prompt("hello", src="en", tgt="zh", context=["earlier line"])
    assert "earlier line" in sent and "do not translate them" in sent
    await backend.unload()


async def test_the_glossary_reaches_the_prompt():
    backend = make_translator("openai_compatible", {"base_url": "http://127.0.0.1:8080/v1"})
    backend.set_glossary([("Kubernetes", "Kubernetes")])
    await backend.load()
    seen = mount(backend, lambda _r: httpx.Response(200, json=completion("x")))
    await backend.translate("we run Kubernetes", src="en", tgt="zh")
    assert "Kubernetes" in json.loads(seen[0].content)["messages"][0]["content"]
    await backend.unload()


async def test_a_reasoning_models_scratchpad_is_not_the_translation():
    backend = make_translator("openai_compatible", {"base_url": "http://127.0.0.1:8080/v1"})
    await backend.load()
    mount(backend, lambda _r: httpx.Response(
        200, json=completion("<think>register is informal here</think>\n你好"),
    ))
    assert await backend.translate("hello", src="en", tgt="zh") == "你好"
    await backend.unload()


async def test_empty_text_never_reaches_the_endpoint():
    backend = make_translator("openai_compatible", {"base_url": "http://127.0.0.1:8080/v1"})
    await backend.load()
    seen = mount(backend, lambda _r: httpx.Response(500))
    assert await backend.translate("   ", src="en", tgt="zh") == ""
    assert seen == []
    await backend.unload()


async def test_a_slow_endpoint_raises_the_timeout_the_pipeline_catches():
    """The interface promises asyncio.TimeoutError at `timeout_s`; the pipeline
    turns exactly that into a recoverable TRANSLATION_TIMEOUT."""
    backend = make_translator("openai_compatible", {"base_url": "http://127.0.0.1:8080/v1"})
    await backend.load()

    async def slow(_request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return httpx.Response(200, json=completion("too late"))

    mount(backend, slow)
    with pytest.raises(asyncio.TimeoutError):
        await backend.translate("hello", src="en", tgt="zh", timeout_s=0.05)
    await backend.unload()


async def test_an_unreachable_endpoint_names_itself():
    backend = make_translator("openai_compatible", {"base_url": "http://127.0.0.1:8080/v1"})
    await backend.load()

    def refuse(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    mount(backend, refuse)
    with pytest.raises(oc.LLMHTTPError, match=re.escape("127.0.0.1:8080")):
        await backend.translate("hello", src="en", tgt="zh")
    await backend.unload()


# ---------- odds and ends ----------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("<think>a</think>b", "b"),
        ("  <think>a</think>\n\nb", "b"),
        ("no reasoning here", "no reasoning here"),
        # An unclosed block is not a block: better to show it than to eat the reply.
        ("<think>never closed", "<think>never closed"),
    ],
)
def test_strip_reasoning(raw, expected):
    assert oc.strip_reasoning(raw) == expected


def test_a_config_dump_never_carries_a_key():
    cfg = _cfg(
        chat={
            "backend": "openai_compatible",
            "endpoint": {
                "base_url": "https://api.example.com/v1",
                "api_key": "sk-secret",
                "api_key_env": "OPENAI_API_KEY",
            },
        },
    )
    params = cfg.redacted_dump()["chat"]["endpoint"]
    assert params["api_key"] == "***"
    # The *name* of the variable is not a secret, and hiding it would leave the
    # user unable to see which one is read.
    assert params["api_key_env"] == "OPENAI_API_KEY"
    assert params["base_url"] == "https://api.example.com/v1"
    # The real dump still has it — only what leaves the process is masked.
    assert cfg.model_dump()["chat"]["endpoint"]["api_key"] == "sk-secret"


def test_an_unset_key_reads_as_unset_not_hidden():
    cfg = _cfg(chat={"backend": "openai_compatible", "endpoint": {"api_key": ""}})
    assert cfg.redacted_dump()["chat"]["endpoint"]["api_key"] == ""


# ---------- what the engine reports about itself ----------


@pytest.fixture
def remote_client(monkeypatch, tmp_path):
    """An app whose chat slot is a hosted endpoint, with a key in the config.

    Constructing the backend opens no socket, so the app starts without
    anything on the other end — which is the point: a misconfigured or
    unreachable endpoint must not stop the engine from booting.
    """
    from fastapi.testclient import TestClient

    import wrenote.core.config as config_mod
    import wrenote.server as server

    monkeypatch.setattr(config_mod, "USER_CONFIG", tmp_path / "config.yaml")
    cfg = Config.model_validate({
        "stt": {"backend": "mock"},
        "stt_offline": {"backend": "mock"},
        "vad": {"backend": "disabled"},
        "speaker": {"backend": "disabled"},
        "translator": {"backend": "openai_compatible",
                       "endpoint": {"base_url": "http://127.0.0.1:8080/v1"}},
        "chat": {"backend": "openai_compatible",
                 "endpoint": {"base_url": "https://api.example.com/v1",
                              "api_key": "sk-secret"}},
        "data": {"dir": str(tmp_path), "exports_dir": str(tmp_path / "exports")},
        "compute": {"runtimes_index_url": ""},
        "update": {"check": False, "index_url": ""},
    })
    with TestClient(server.create_app(cfg)) as c:
        yield c


def test_the_engine_starts_with_no_endpoint_answering(remote_client):
    assert remote_client.get("/health").json()["status"] == "ok"
    assert remote_client.app.state.models.chat_backend is not None


def test_status_names_the_remote_slot_and_nothing_to_download(remote_client):
    body = remote_client.get("/v1/models/status").json()
    # The local `llama-server` case is not "remote" — only the hosted one is.
    assert body["remote"] == ["chat"]
    # Neither slot has a file to fetch, so a first run has nothing to ask for.
    assert [m for m in body["models"] if m["key"] in ("chat", "translator")] == []
    assert body["selected"]["chat"] is None


def test_info_does_not_hand_the_api_key_back(remote_client):
    body = remote_client.get("/v1/info").json()
    assert body["config"]["chat"]["endpoint"]["api_key"] == "***"
    assert "sk-secret" not in json.dumps(body)


# ---------- configuring an endpoint from Settings ----------


@pytest.fixture
def panel_client(monkeypatch, tmp_path):
    """An app on local models, as a fresh install is — the state the settings
    panel starts from when someone goes looking for the endpoint fields."""
    from fastapi.testclient import TestClient

    import wrenote.core.config as config_mod
    import wrenote.server as server

    monkeypatch.setattr(config_mod, "USER_CONFIG", tmp_path / "config.yaml")
    cfg = Config.model_validate({
        "stt": {"backend": "mock"},
        "stt_offline": {"backend": "mock"},
        "vad": {"backend": "disabled"},
        "speaker": {"backend": "disabled"},
        "translator": {"backend": "mock"},
        "chat": {"backend": "mock"},
        "data": {"dir": str(tmp_path), "exports_dir": str(tmp_path / "exports")},
        "compute": {"runtimes_index_url": ""},
        "update": {"check": False, "index_url": ""},
    })
    with TestClient(server.create_app(cfg)) as c:
        yield c


def test_status_offers_an_endpoint_for_the_slots_that_can_have_one(panel_client):
    endpoints = panel_client.get("/v1/models/status").json()["endpoints"]
    # Speech recognition is absent, and that absence is the contract: the live
    # path is not a request/response shape and is not going to be offered.
    assert sorted(endpoints) == ["chat", "translator"]
    assert endpoints["chat"] == {
        "active": False, "base_url": "", "model": "", "has_api_key": False,
        "api_key_env": "", "timeout_s": 120.0, "configured": False, "local": False,
    }


def test_saving_an_endpoint_switches_the_slot_and_applies_at_once(panel_client):
    r = panel_client.post("/v1/models/endpoint", json={
        "kind": "chat", "base_url": "http://127.0.0.1:8080/v1",
        "model": "qwen3", "active": True,
    })
    assert r.status_code == 200
    body = r.json()
    # Chat is held by ModelManager, so it is swapped in place.
    assert body["applies"] == "now" and body["restart_required"] is False
    assert body["endpoint"]["configured"] and body["endpoint"]["local"]
    assert panel_client.app.state.models.chat_backend.info.name == "openai_compatible_chat"
    # A loopback endpoint is still local inference: the claim does not change.
    assert body["remote"] == []


def test_the_translator_endpoint_applies_to_the_next_session(panel_client):
    r = panel_client.post("/v1/models/endpoint", json={
        "kind": "translator", "base_url": "https://api.example.com/v1", "active": True,
    })
    assert r.json()["applies"] == "next_session"
    assert r.json()["remote"] == ["translator"]


def test_the_key_is_saved_but_never_handed_back(panel_client):
    panel_client.post("/v1/models/endpoint", json={
        "kind": "chat", "base_url": "https://api.example.com/v1",
        "api_key": "sk-secret", "active": True,
    })
    endpoint = panel_client.get("/v1/models/status").json()["endpoints"]["chat"]
    assert endpoint["has_api_key"] is True
    assert "sk-secret" not in json.dumps(endpoint)
    assert "sk-secret" not in json.dumps(panel_client.get("/v1/info").json())


def test_saving_the_form_again_does_not_wipe_the_key(panel_client):
    """The client cannot echo a key it was never given, so an omitted field has
    to mean "leave it" — otherwise every edit of the model name logs you out."""
    panel_client.post("/v1/models/endpoint", json={
        "kind": "chat", "base_url": "https://api.example.com/v1",
        "api_key": "sk-secret", "active": True,
    })
    panel_client.post("/v1/models/endpoint", json={"kind": "chat", "model": "gpt-4o-mini"})
    endpoint = panel_client.get("/v1/models/status").json()["endpoints"]["chat"]
    assert endpoint["has_api_key"] is True and endpoint["model"] == "gpt-4o-mini"


def test_an_empty_key_clears_it(panel_client):
    """Which is how "forget the key" works — distinct from omitting the field."""
    panel_client.post("/v1/models/endpoint", json={
        "kind": "chat", "base_url": "https://api.example.com/v1",
        "api_key": "sk-secret", "active": True,
    })
    panel_client.post("/v1/models/endpoint", json={"kind": "chat", "api_key": ""})
    assert panel_client.get("/v1/models/status").json()["endpoints"]["chat"]["has_api_key"] is False


def test_switching_back_to_a_local_model_keeps_the_endpoint_on_record(panel_client):
    """And must not leave `base_url` in the local backend's constructor
    arguments — llama_cpp takes no such keyword and would fail to build."""
    panel_client.post("/v1/models/endpoint", json={
        "kind": "chat", "base_url": "https://api.example.com/v1", "active": True,
    })
    r = panel_client.post("/v1/models/endpoint", json={"kind": "chat", "active": False})
    assert r.status_code == 200
    body = r.json()
    assert body["endpoint"]["active"] is False
    # Remembered, so turning it back on is one click and not a re-typing.
    assert body["endpoint"]["base_url"] == "https://api.example.com/v1"
    assert body["remote"] == []
    status = panel_client.get("/v1/models/status").json()
    assert status["selected"]["chat"] is not None  # a real catalogue model again


def test_turning_it_on_without_a_url_is_refused(panel_client):
    r = panel_client.post("/v1/models/endpoint", json={"kind": "chat", "active": True})
    assert r.status_code == 400 and "base_url" in r.json()["detail"]


def test_speech_recognition_cannot_be_pointed_at_a_url(panel_client):
    r = panel_client.post("/v1/models/endpoint", json={
        "kind": "stt", "base_url": "https://api.example.com/v1", "active": True,
    })
    assert r.status_code == 400 and "stt" in r.json()["detail"]


def test_the_choice_survives_a_restart(panel_client, tmp_path):
    panel_client.post("/v1/models/endpoint", json={
        "kind": "chat", "base_url": "https://api.example.com/v1",
        "api_key_env": "OPENAI_API_KEY", "active": True,
    })
    written = (tmp_path / "config.yaml").read_text(encoding="utf-8")
    assert "openai_compatible" in written and "api.example.com" in written


def test_a_test_call_reports_the_endpoint_being_down_as_an_answer(panel_client):
    """Not a 500: a failed connection test is the result the user asked for,
    and it belongs next to the field that caused it."""
    panel_client.post("/v1/models/endpoint", json={
        # Nothing is listening here.
        "kind": "chat", "base_url": "http://127.0.0.1:9/v1", "active": True,
    })
    r = panel_client.post("/v1/models/endpoint/test", json={"kind": "chat"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False and body["url"].endswith("/chat/completions")
    assert body["error"]


def test_a_test_call_against_a_working_endpoint_reports_the_reply(panel_client, monkeypatch):
    import wrenote.api.models as models_api

    real = models_api._client_for

    def patched(cfg, slot, catalogue):
        client = real(cfg, slot, catalogue)
        client.open()
        client._client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _r: httpx.Response(200, json=completion("ok")))
        )
        return client

    monkeypatch.setattr(models_api, "_client_for", patched)
    panel_client.post("/v1/models/endpoint", json={
        "kind": "chat", "base_url": "http://127.0.0.1:8080/v1", "active": True,
    })
    body = panel_client.post("/v1/models/endpoint/test", json={"kind": "chat"}).json()
    assert body["ok"] is True and body["reply"] == "ok"


def test_the_two_lists_of_http_backends_agree():
    """`core/catalogue` keeps its own copy so it never imports an HTTP client."""
    from wrenote.core.catalogue import HTTP_BACKENDS

    assert HTTP_BACKENDS == oc.HTTP_BACKENDS
