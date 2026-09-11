"""A `llama-server` this engine started, and is responsible for killing.

Step 2 of ``docs/plans/LLM_OUT_OF_PROCESS.md``: the local case stops being a
different thing from the remote one. The engine spawns `llama-server`, waits
for it to load the weights, and hands the same
:class:`~wrenote.core.openai_compat.ChatCompletionsClient` its address — so
from the backend's point of view a model on this machine and a hosted API are
one code path, which is the entire point.

Three things this has to get right, in order of how badly they go wrong:

**Not leaking a 2.5 GB process.** Normal shutdown is easy (the lifespan stops
it). The hard case is the engine being killed outright, which leaves the
server running with nobody to talk to it. So every server writes a small file
naming its pid, port and token; the next start reads those files and reclaims
what it finds. Reclaiming *verifies* before killing — it asks the recorded
port for `/health` with the recorded token, and only a process that answers is
ours. A pid on its own is not evidence: pids are reused, and killing a
stranger's process because we crashed is worse than leaking one.

**Not being talked to by anything else.** The port is loopback and the server
gets a random `--api-key`, the same shape the engine already uses for its own
SPA. Otherwise any page in the user's browser could use their GPU.

**Saying what happened when it won't start.** A missing binary, a model file
llama.cpp rejects, a port taken in the moment between choosing it and binding
it — each is a different sentence, and the one thing they must not be is a
chat that silently never answers.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import secrets
import shutil
import socket
import sys
from collections.abc import Sequence
from pathlib import Path

import httpx2 as httpx

from .openai_compat import ChatCompletionsClient

log = logging.getLogger(__name__)

#: The executable, by the names llama.cpp has shipped it under.
BINARY_NAMES = ("llama-server", "llama-server.exe", "server", "server.exe")

#: How long to wait for the weights to load before giving up. A 4B model off a
#: cold disk is tens of seconds; the ceiling is for a large model on a slow
#: one, and it is a ceiling rather than a guess because the failure it guards
#: against (a server that will never become ready) is rare and the cost of
#: cutting a slow-but-fine start short is a feature that looks broken.
READY_TIMEOUT_S = 300.0

#: Grace between asking a server to stop and insisting.
STOP_GRACE_S = 5.0

#: How many times to try spawning before giving up. More than one because the
#: port is a hint rather than a reservation and losing that race is a fast,
#: silent exit; a small number because every other reason to exit early — a
#: model file llama.cpp won't read, a binary for the wrong architecture — will
#: fail identically each time, and the user is waiting.
START_ATTEMPTS = 3


class LlamaServerError(RuntimeError):
    """The server could not be started, or died while we were using it."""


class LlamaServerExited(LlamaServerError):
    """It exited before it was ready — which is sometimes only bad luck.

    The port is chosen by binding one and letting go (see :func:`_free_port`),
    so something else can take it in between; llama.cpp then fails to bind and
    exits. Indistinguishable from a model file it refuses, so
    :meth:`LlamaServerProcess.start` simply tries again a couple of times and
    reports the last failure if none of them worked.
    """


def find_binary(explicit: str = "", *, search: Sequence[Path] = ()) -> Path | None:
    """Locate `llama-server`: the configured path, then ours, then the PATH.

    An explicit path that does not exist returns ``None`` rather than falling
    through to something else — a user who named a binary wants *that* binary,
    and silently running a different one is how you debug the wrong program.
    """
    if explicit:
        path = Path(explicit).expanduser()
        return path if path.is_file() else None
    for directory in search:
        for name in BINARY_NAMES:
            candidate = directory / name
            if candidate.is_file():
                return candidate
    for name in BINARY_NAMES:
        found = shutil.which(name)
        if found:
            return Path(found)
    return None


def _free_port(host: str) -> int:
    """A port nothing is listening on, right now.

    Unavoidably a hint rather than a reservation: `llama-server` binds it
    itself, so it can be taken in between. The window is small, the failure is
    loud (the server exits and start() says so), and the alternative — passing
    a bound socket to a process that expects to bind its own — does not exist.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


class LlamaServerProcess:
    """One `llama-server`, hosting one model, owned by this process."""

    def __init__(
        self,
        *,
        binary: Path,
        model_path: Path,
        state_dir: Path,
        label: str,
        n_ctx: int = 8192,
        n_gpu_layers: int = -1,
        host: str = "127.0.0.1",
        extra_args: Sequence[str] = (),
    ) -> None:
        self._binary = binary
        self._model_path = model_path
        self._label = label
        self._state_dir = state_dir
        self._n_ctx = n_ctx
        self._n_gpu_layers = n_gpu_layers
        self._host = host
        self._extra_args = list(extra_args)
        self._proc: asyncio.subprocess.Process | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._port = 0
        self._token = ""
        #: The last few stderr lines, so a failure to start can quote llama.cpp
        #: rather than only reporting that it exited.
        self._tail: list[str] = []

    # --- what the backend needs -------------------------------------------

    @property
    def base_url(self) -> str:
        return f"http://{self._host}:{self._port}/v1"

    @property
    def token(self) -> str:
        return self._token

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    # --- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        """Spawn it and wait until it answers. Raises on anything else."""
        if self.alive:
            return
        if not self._model_path.exists():
            raise LlamaServerError(f"model not found at {self._model_path}")

        # Off the loop: `_is_ours` waits on a socket per state file, and the
        # loop it would block is the one serving the request that triggered
        # this load.
        await asyncio.to_thread(reclaim_orphans, self._state_dir)

        for attempt in range(1, START_ATTEMPTS + 1):
            try:
                await self._spawn()
                return
            except LlamaServerExited:
                await self.stop()
                if attempt == START_ATTEMPTS:
                    raise
                log.warning(
                    "llama-server for %s exited before it was ready (attempt %d/%d); "
                    "retrying on a new port",
                    self._label, attempt, START_ATTEMPTS,
                )

    async def _spawn(self) -> None:
        # Fresh, so a retry's error message quotes the attempt that failed
        # rather than the one before it.
        self._tail = []
        self._port = _free_port(self._host)
        # Hex, not urlsafe base64: the latter can begin with "-", and a
        # token that starts with a dash is read as the *next flag* by
        # argparse and by llama.cpp's own parser — `--api-key` then has no
        # value and the server exits 2 before it ever listens. About 1.6% of
        # tokens, so it fails one start in sixty and looks like a flake. Hex
        # cannot collide with an option at all, and 24 bytes is still 192
        # bits.
        self._token = secrets.token_hex(24)
        argv = [
            str(self._binary),
            "--model", str(self._model_path),
            "--host", self._host,
            "--port", str(self._port),
            "--ctx-size", str(self._n_ctx),
            "--n-gpu-layers", str(self._n_gpu_layers),
            # Loopback is not on its own enough: any page in the user's
            # browser can reach 127.0.0.1 too.
            "--api-key", self._token,
            *self._extra_args,
        ]
        log.info(
            "starting llama-server for %s: %s (port=%d, n_ctx=%d, n_gpu_layers=%d)",
            self._label, self._model_path.name, self._port, self._n_ctx, self._n_gpu_layers,
        )
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
                # Its own process group, so stopping it cannot depend on
                # whatever signal the engine happens to be receiving.
                **_detach_kwargs(),
            )
        except OSError as e:
            raise LlamaServerError(f"cannot run {self._binary}: {e}") from e

        self._stderr_task = asyncio.create_task(
            self._drain_stderr(), name=f"llama-server.stderr.{self._label}"
        )
        self._write_state()
        try:
            await self._wait_ready()
        except BaseException:
            # Including CancelledError: a shutdown during start-up must not
            # leave the process we just spawned behind.
            await self.stop()
            raise
        log.info("llama-server for %s is ready at %s", self._label, self.base_url)

    async def stop(self) -> None:
        proc, self._proc = self._proc, None
        if self._stderr_task is not None:
            self._stderr_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._stderr_task
            self._stderr_task = None
        if proc is not None and proc.returncode is None:
            with contextlib.suppress(ProcessLookupError, OSError):
                proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=STOP_GRACE_S)
            except TimeoutError:
                log.warning("llama-server for %s ignored terminate; killing", self._label)
                with contextlib.suppress(ProcessLookupError, OSError):
                    proc.kill()
                with contextlib.suppress(Exception):
                    await proc.wait()
        self._clear_state()

    # --- internals ---------------------------------------------------------

    async def _wait_ready(self) -> None:
        """Poll `/health` until it is serving, the process dies, or we give up."""
        deadline = asyncio.get_running_loop().time() + READY_TIMEOUT_S
        url = f"http://{self._host}:{self._port}/health"
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
            while True:
                if self._proc is None or self._proc.returncode is not None:
                    code = self._proc.returncode if self._proc else None
                    raise LlamaServerExited(
                        f"llama-server exited with code {code} before it was ready"
                        + (f": {self.stderr_tail}" if self._tail else "")
                    )
                if asyncio.get_running_loop().time() > deadline:
                    raise LlamaServerError(
                        f"llama-server did not become ready within "
                        f"{READY_TIMEOUT_S:.0f}s (model {self._model_path.name})"
                    )
                try:
                    resp = await client.get(
                        url, headers={"authorization": f"Bearer {self._token}"}
                    )
                    # 503 is llama.cpp still reading the weights, which on a
                    # large model is most of this loop.
                    if resp.status_code == 200:
                        return
                except httpx.RequestError:
                    pass  # not listening yet
                await asyncio.sleep(0.25)

    async def _drain_stderr(self) -> None:
        assert self._proc is not None and self._proc.stderr is not None
        try:
            async for raw in self._proc.stderr:
                line = raw.decode("utf-8", "replace").rstrip()
                if not line:
                    continue
                self._tail = [*self._tail[-9:], line]
                log.debug("llama-server[%s]: %s", self._label, line)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.debug("llama-server stderr reader stopped", exc_info=True)

    @property
    def stderr_tail(self) -> str:
        return " / ".join(self._tail[-3:])

    def _state_path(self) -> Path:
        return self._state_dir / f"llama-server-{self._label}.json"

    def _write_state(self) -> None:
        """Record what a later run needs to identify this process as ours."""
        if self._proc is None:
            return
        try:
            self._state_dir.mkdir(parents=True, exist_ok=True)
            self._state_path().write_text(
                json.dumps({
                    "pid": self._proc.pid,
                    "port": self._port,
                    "token": self._token,
                    "host": self._host,
                }),
                encoding="utf-8",
            )
        except OSError:
            # Not fatal: it costs orphan recovery, not this run.
            log.warning("could not record the llama-server state file", exc_info=True)

    def _clear_state(self) -> None:
        with contextlib.suppress(OSError):
            self._state_path().unlink()


def _detach_kwargs() -> dict[str, object]:
    """Put the child in its own process group, per platform."""
    if sys.platform == "win32":  # pragma: no cover - exercised on Windows only
        return {"creationflags": getattr(__import__("subprocess"), "CREATE_NEW_PROCESS_GROUP", 0)}
    return {"start_new_session": True}


def reclaim_orphans(state_dir: Path) -> int:
    """Kill `llama-server`s a previous run left behind. Returns how many.

    Called on every start rather than only at boot, because the run that
    leaked one is by definition the run that did not get to clean up.

    A state file is only evidence of an orphan if something is still answering
    on its port *with the token we wrote there*. That test is what makes this
    safe: pids get reused, and this function's whole job is to kill something
    it did not start in this process.
    """
    if not state_dir.is_dir():
        return 0
    killed = 0
    for path in sorted(state_dir.glob("llama-server-*.json")):
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
            pid, port = int(state["pid"]), int(state["port"])
            token, host = str(state["token"]), str(state.get("host") or "127.0.0.1")
        except (OSError, ValueError, KeyError, TypeError):
            with contextlib.suppress(OSError):
                path.unlink()
            continue
        if _is_ours(host, port, token):
            log.warning("killing a llama-server left behind by a previous run (pid %d)", pid)
            with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
                os.kill(pid, 9)
            killed += 1
        with contextlib.suppress(OSError):
            path.unlink()
    return killed


def _is_ours(host: str, port: int, token: str) -> bool:
    """Whether the thing on that port is the server we recorded.

    Synchronous and short: this runs during start-up, and the answer for a
    port with nothing on it arrives as a refused connection immediately.
    """
    try:
        resp = httpx.get(
            f"http://{host}:{port}/health",
            headers={"authorization": f"Bearer {token}"},
            timeout=httpx.Timeout(2.0),
        )
    except httpx.RequestError:
        return False
    # 401 means something is there but it is not ours — a different server, or
    # ours with a different token, and neither is safe to kill.
    return resp.status_code == 200


class ManagedEndpoint:
    """A `llama-server` and the client pointed at it, as one lifecycle.

    The backends hold this rather than the two halves: starting a process and
    then failing to open a client to it is a state nobody should have to
    handle, and "the model is loaded" and "we can ask it things" become the
    same question again — which is what they were when llama.cpp was in
    process.
    """

    def __init__(
        self,
        *,
        model_path: Path,
        state_dir: Path,
        label: str,
        binary: Path,
        n_ctx: int = 8192,
        n_gpu_layers: int = -1,
        extra_args: Sequence[str] = (),
        max_concurrency: int = 1,
    ) -> None:
        self._proc = LlamaServerProcess(
            binary=binary,
            model_path=model_path,
            state_dir=state_dir,
            label=label,
            n_ctx=n_ctx,
            n_gpu_layers=n_gpu_layers,
            extra_args=extra_args,
        )
        self._max_concurrency = max_concurrency
        self._client: ChatCompletionsClient | None = None
        self._model_name = model_path.stem

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def client(self) -> ChatCompletionsClient:
        if self._client is None:
            raise RuntimeError("llama-server not started; call load() first")
        return self._client

    async def open(self) -> None:
        if self._client is not None:
            return
        await self._proc.start()
        client = ChatCompletionsClient(
            base_url=self._proc.base_url,
            api_key=self._proc.token,
            max_concurrency=self._max_concurrency,
            # The server hosts exactly one model and has no name for it.
            model="",
        )
        client.open()
        self._client = client

    async def aclose(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            await client.aclose()
        await self._proc.stop()


def resolve_binary(configured: str, *, runtimes_dir: Path | None = None) -> Path:
    """The `llama-server` to run, or an error saying where we looked.

    The normal answer arrives via the PATH: a compute runtime pack ships the
    binary for its accelerator in `bin/`, and ``RuntimeManager.activate`` puts
    that directory on the PATH already — it has to, for CUDA's DLLs. So the
    per-accelerator build needs no plumbing here, and a user who installed
    their own is found the same way.

    Deliberately a hard failure rather than a fallback to the in-process
    backend: a slot configured to run a supervised server and quietly running
    something else instead is the kind of "helpful" that makes a bug report
    impossible to read.
    """
    search: list[Path] = []
    if runtimes_dir is not None:
        # The dir itself, for a binary dropped in by hand. Installed packs are
        # reached through the PATH entry `activate` makes, not from here — this
        # function has no idea which variant is active, and should not.
        search += [runtimes_dir, runtimes_dir / "bin"]
    if getattr(sys, "frozen", False):  # pragma: no cover - frozen builds only
        # The platform whose accelerator is *built in* (Metal on macOS arm64)
        # has no pack to carry the binary, so it rides in the app bundle
        # beside ffmpeg and the capture helpers. `_MEIPASS` is where those
        # land; run_server already puts it on the PATH, so this is belt and
        # braces rather than the only route.
        search.append(Path(sys.executable).parent)
        meipass = getattr(sys, "_MEIPASS", "")
        if meipass:
            search.append(Path(meipass))
    found = find_binary(configured, search=search)
    if found is not None:
        return found
    if configured:
        raise LlamaServerError(f"no llama-server at {configured}")
    where = ", ".join(str(p) for p in search) or "the PATH"
    raise LlamaServerError(
        f"llama-server not found (looked in {where}, and on the PATH). "
        "Install a compute runtime pack that ships one (Settings → Compute), "
        "put one on the PATH, or name it in the config."
    )
