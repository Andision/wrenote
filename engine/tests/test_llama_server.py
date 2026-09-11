"""Supervising a `llama-server`: starting it, using it, and never leaking it.

Step 2 of docs/plans/LLM_OUT_OF_PROCESS.md. These run a real subprocess — a
stand-in that speaks the two endpoints the supervisor depends on (see
``_fake_llama_server.py``) — because what is worth testing here is precisely
the part a mock would replace: that the process is spawned, waited for, talked
to, and gone afterwards.

POSIX only: the fake is reached through a shell wrapper named `llama-server`,
which is how `find_binary` is meant to find one. CI runs the suite on Linux.
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
from pathlib import Path

import pytest

from wrenote.core import llama_server as ls
from wrenote.core.registry import make_chat, make_translator

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="the fake binary is a POSIX shell wrapper"
)

FAKE = Path(__file__).with_name("_fake_llama_server.py")


@pytest.fixture
def binary(tmp_path):
    """An executable named `llama-server` that runs the stand-in."""
    path = tmp_path / "bin" / "llama-server"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE}" "$@"\n', encoding="utf-8")
    path.chmod(0o755)
    return path


@pytest.fixture
def model(tmp_path):
    """`llama-server` never sees a real GGUF here — the supervisor only checks
    that the path exists before spawning, which is the check worth having."""
    path = tmp_path / "models" / "model.gguf"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"GGUF")
    return path


def server(binary, model, tmp_path, **over):
    return ls.LlamaServerProcess(
        binary=binary, model_path=model, state_dir=tmp_path / "state",
        label="chat", **over,
    )


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


# ---------- finding the binary ----------


def test_an_explicit_path_that_is_wrong_is_not_silently_replaced(tmp_path, binary):
    """Naming a binary and getting a different one is how you debug the wrong
    program for an afternoon."""
    assert ls.find_binary(str(tmp_path / "nope" / "llama-server")) is None
    assert ls.find_binary(str(binary)) == binary


def test_it_looks_where_we_put_things_before_the_path(binary):
    assert ls.find_binary("", search=[binary.parent]) == binary


def test_a_missing_binary_says_where_it_looked(tmp_path):
    with pytest.raises(ls.LlamaServerError) as e:
        ls.resolve_binary("", runtimes_dir=tmp_path / "runtimes")
    assert "runtimes" in str(e.value) and "PATH" in str(e.value)


# ---------- the process ----------


async def test_it_starts_answers_and_stops(binary, model, tmp_path):
    proc = server(binary, model, tmp_path)
    await proc.start()
    try:
        assert proc.alive and proc.base_url.startswith("http://127.0.0.1:")
        assert proc.token  # a random key, so nothing else on loopback can use it
    finally:
        await proc.stop()
    assert not proc.alive


async def test_it_waits_out_a_model_that_is_still_loading(binary, model, tmp_path, monkeypatch):
    """503 from /health is llama.cpp reading the weights, which on a large
    model is most of the wait — not a failure."""
    monkeypatch.setenv("FAKE_LLAMA_LOADING", "5")
    proc = server(binary, model, tmp_path)
    await proc.start()
    try:
        assert proc.alive
    finally:
        await proc.stop()


async def test_a_server_that_dies_on_start_reports_why(binary, model, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_LLAMA_EXIT", "1")
    proc = server(binary, model, tmp_path)
    with pytest.raises(ls.LlamaServerError) as e:
        await proc.start()
    assert "exited with code 1" in str(e.value)
    # The stderr tail is quoted, so the message names the model llama.cpp
    # refused rather than only that something exited.
    assert "refusing to load" in str(e.value)


async def test_a_server_that_never_becomes_ready_gives_up(binary, model, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_LLAMA_NEVER_READY", "1")
    monkeypatch.setattr(ls, "READY_TIMEOUT_S", 0.6)
    proc = server(binary, model, tmp_path)
    with pytest.raises(ls.LlamaServerError, match="did not become ready"):
        await proc.start()
    # And it is not left running behind the failure.
    assert not proc.alive


async def test_a_missing_model_never_spawns_anything(binary, tmp_path):
    proc = server(binary, tmp_path / "gone.gguf", tmp_path)
    with pytest.raises(ls.LlamaServerError, match="model not found"):
        await proc.start()


async def test_a_server_that_ignores_terminate_is_killed(binary, model, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_LLAMA_IGNORE_TERM", "1")
    monkeypatch.setattr(ls, "STOP_GRACE_S", 0.5)
    proc = server(binary, model, tmp_path)
    await proc.start()
    pid = proc._proc.pid
    await proc.stop()
    assert not alive(pid)


async def test_cancelling_the_start_leaves_nothing_running(binary, model, tmp_path, monkeypatch):
    """A shutdown that lands while the weights are loading is the case that
    leaks: the task is cancelled between spawn and ready."""
    monkeypatch.setenv("FAKE_LLAMA_NEVER_READY", "1")
    proc = server(binary, model, tmp_path)
    task = asyncio.create_task(proc.start())
    await asyncio.sleep(0.4)  # spawned, still polling /health
    pid = proc._proc.pid
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not alive(pid)


# ---------- not leaking one ----------


async def test_a_leaked_server_is_reclaimed_by_the_next_start(binary, model, tmp_path):
    """The engine being killed outright is the case that leaves 2.5 GB behind,
    and the run that leaked it is by definition the run that couldn't tidy up."""
    first = server(binary, model, tmp_path)
    await first.start()
    pid = first._proc.pid
    # Simulate the engine dying: drop our handle without stopping it, leaving
    # the state file and a live process, exactly as a SIGKILL would.
    first._proc = None
    first._stderr_task.cancel()
    assert alive(pid)

    reclaimed = ls.reclaim_orphans(tmp_path / "state")
    assert reclaimed == 1
    for _ in range(50):
        if not alive(pid):
            break
        await asyncio.sleep(0.05)
    assert not alive(pid)


def test_reclaim_never_kills_a_process_that_is_not_ours(tmp_path):
    """Pids are reused. A state file pointing at a live pid that does not
    answer with our token must be left completely alone — killing a stranger's
    process because we crashed is worse than leaking one."""
    state = tmp_path / "state"
    state.mkdir()
    # A port with nothing on it, and this test process's own pid.
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        free = s.getsockname()[1]
    (state / "llama-server-chat.json").write_text(
        json.dumps({"pid": os.getpid(), "port": free, "token": "t", "host": "127.0.0.1"})
    )
    assert ls.reclaim_orphans(state) == 0
    assert alive(os.getpid())  # i.e. we did not kill the test runner


async def test_reclaim_leaves_a_server_holding_a_different_token(binary, model, tmp_path):
    """Something else on that port — another app's llama-server, or ours from
    a different install — answers 401 and is not ours to kill."""
    proc = server(binary, model, tmp_path)
    await proc.start()
    pid = proc._proc.pid
    try:
        state_file = tmp_path / "state" / "llama-server-chat.json"
        state = json.loads(state_file.read_text())
        state["token"] = "not-the-token-we-issued"
        state_file.write_text(json.dumps(state))
        assert ls.reclaim_orphans(tmp_path / "state") == 0
        assert alive(pid)
    finally:
        await proc.stop()


def test_a_stale_state_file_is_just_removed(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    (state / "llama-server-chat.json").write_text("not json at all")
    assert ls.reclaim_orphans(state) == 0
    assert not list(state.glob("*.json"))


def test_reclaim_on_a_first_run_is_a_no_op(tmp_path):
    assert ls.reclaim_orphans(tmp_path / "never-existed") == 0


# ---------- the backends ----------


async def test_chat_streams_through_a_server_it_started(binary, model, tmp_path):
    backend = make_chat("llama_server", {
        "model_path": str(model), "binary": str(binary),
        "state_dir": str(tmp_path / "state"),
    })
    await backend.load()
    try:
        from wrenote.chat.base import ChatMessage

        out = [p async for p in await backend.chat([ChatMessage(role="user", content="hi")])]
        assert "".join(out).strip() == "served by a llama-server"
        assert backend.info.device == "managed-http"
    finally:
        await backend.unload()


async def test_the_translator_uses_the_shared_prompt_and_stops_its_server(
    binary, model, tmp_path
):
    backend = make_translator("llama_server", {
        "model_path": str(model), "binary": str(binary),
        "state_dir": str(tmp_path / "state"),
    })
    await backend.load()
    pid = backend._endpoint._proc._proc.pid
    try:
        assert await backend.translate("hello", src="en", tgt="zh") == "served by a llama-server"
    finally:
        await backend.unload()
    assert not alive(pid)
    # The state file goes with it, so the next run has nothing to reclaim.
    assert not list((tmp_path / "state").glob("*.json"))


async def test_a_backend_whose_binary_is_missing_says_so_at_load(model, tmp_path):
    backend = make_chat("llama_server", {
        "model_path": str(model), "binary": str(tmp_path / "nope"),
        "state_dir": str(tmp_path / "state"),
    })
    with pytest.raises(ls.LlamaServerError, match="no llama-server at"):
        await backend.load()


async def test_unload_without_load_is_harmless(model, tmp_path):
    backend = make_chat("llama_server", {"model_path": str(model), "state_dir": str(tmp_path)})
    await backend.unload()


def test_the_engine_reclaims_on_start_whatever_backend_is_configured(monkeypatch, tmp_path):
    """The config may have moved off the managed backend since the run that
    leaked one, so this cannot be left to the backend's own start-up."""
    from fastapi.testclient import TestClient

    import wrenote.core.config as config_mod
    import wrenote.server as server_mod
    from wrenote.core.config import Config

    monkeypatch.setattr(config_mod, "USER_CONFIG", tmp_path / "config.yaml")
    seen: list[Path] = []
    monkeypatch.setattr(
        server_mod, "reclaim_orphans", lambda d: (seen.append(d), 0)[1]
    )
    cfg = Config.model_validate({
        "stt": {"backend": "mock"}, "stt_offline": {"backend": "mock"},
        "vad": {"backend": "disabled"}, "translator": {"backend": "mock"},
        "speaker": {"backend": "disabled"}, "chat": {"backend": "mock"},
        "data": {"dir": str(tmp_path), "exports_dir": str(tmp_path / "e")},
        "compute": {"runtimes_index_url": ""},
        "update": {"check": False, "index_url": ""},
    })
    with TestClient(server_mod.create_app(cfg)):
        pass
    assert seen == [Path(cfg.data.dir)]


async def test_losing_the_port_race_is_retried_rather_than_reported(
    binary, model, tmp_path, monkeypatch
):
    """The port is a hint, not a reservation: `_free_port` lets go of it and
    llama.cpp binds it a moment later, so something else can take it in
    between.

    Actually taken here, rather than simulated — the point is that a real
    failed bind is recovered from, and this suite's own flakiness under a full
    run was exactly this race.
    """
    ports: list[int] = []
    held: list[socket.socket] = []
    real_free_port = ls._free_port

    def steal_the_first(host: str) -> int:
        port = real_free_port(host)
        ports.append(port)
        if not held:
            taken = socket.socket()
            taken.bind((host, port))
            taken.listen(1)
            held.append(taken)
        return port

    monkeypatch.setattr(ls, "_free_port", steal_the_first)
    proc = server(binary, model, tmp_path)
    try:
        await proc.start()
        assert proc.alive
        assert len(ports) == 2 and ports[0] != ports[1]  # retried, elsewhere
        assert str(ports[1]) in proc.base_url
    finally:
        await proc.stop()
        for sock in held:
            sock.close()


async def test_a_server_that_always_dies_still_fails_with_its_reason(
    binary, model, tmp_path, monkeypatch
):
    """Retrying must not swallow the real answer: a model llama.cpp will not
    read fails identically every time, and the user needs to be told that."""
    monkeypatch.setenv("FAKE_LLAMA_EXIT", "1")
    proc = server(binary, model, tmp_path)
    with pytest.raises(ls.LlamaServerExited, match="refusing to load"):
        await proc.start()


async def test_swapping_the_chat_model_stops_the_server_it_was_using(
    binary, model, tmp_path, monkeypatch
):
    """"Memory actually comes back" is the plan's second reason for all this,
    and it is only true if the old process actually goes away. ModelManager
    swaps the backend; what has to follow is a dead subprocess."""
    from wrenote.core.registry import make_chat
    from wrenote.model_manager import ModelManager

    params = {
        "model_path": str(model), "binary": str(binary),
        "state_dir": str(tmp_path / "state"),
    }
    manager = ModelManager(chat_backend=make_chat("llama_server", params), diarize_speaker=None)
    backend = await manager.ensure_chat_loaded()
    pid = backend._endpoint._proc._proc.pid
    assert alive(pid)

    # A different model on the same backend — the swap Settings → Models does.
    await manager.replace_chat(make_chat("llama_server", params))
    for _ in range(50):
        if not alive(pid):
            break
        await asyncio.sleep(0.05)
    assert not alive(pid), "the previous llama-server was left running"

    # And the new one is not started until something asks for it.
    assert manager._chat_loaded is False
    await manager.aclose()
