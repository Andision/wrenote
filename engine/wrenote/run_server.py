"""Pure server entry for the Electron shell — no window (Electron owns that).

The Electron main process spawns this, reads the single ``WRENOTE_PORT=<n>``
line we print on stdout to learn the OS-assigned port, then points its
``BrowserWindow`` at ``http://127.0.0.1:<n>``. The per-launch loopback auth token
arrives via the ``WRENOTE_AUTH_TOKEN`` env var (read by :mod:`wrenote.auth` at
import); the server cookies it onto the SPA when it loads ``/``, so the frontend
needs no changes.

Binding the socket here (rather than letting the shell pick a port and race to
spawn) mirrors what ``desktop.py`` did: the OS assigns a free port, we hand the
already-bound socket to uvicorn, and report the number — no TOCTOU window.

``desktop.py`` (the pywebview launcher) stays runnable until pywebview is dropped
in P2; this module is the Electron path's replacement for it.
"""
from __future__ import annotations

import argparse
import os
import socket
import sys

import uvicorn

from .core.config import load_config


def _bind_free_port(host: str, port: int) -> tuple[socket.socket, int]:
    """Bind a loopback socket (``port=0`` → OS-assigned) to hand to uvicorn."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, port))
    return sock, sock.getsockname()[1]


def _ensure_frozen_binaries_on_path() -> None:
    """In a frozen app, prepend the bundle dir (``_MEIPASS``, where ffmpeg and the
    macOS capture helpers live) to PATH so upload decoding can shell out to
    ``ffmpeg``. No-op in dev — mirrors desktop.py's launcher hardening."""
    meipass = getattr(sys, "_MEIPASS", None)
    if not meipass:
        return
    parts = [p for p in os.environ.get("PATH", "").split(os.pathsep) if p]
    if meipass not in parts:
        os.environ["PATH"] = os.pathsep.join([meipass, *parts])


def main() -> None:
    _ensure_frozen_binaries_on_path()
    parser = argparse.ArgumentParser(prog="wrenote.run_server")
    parser.add_argument("--host", default=None, help="override config host")
    parser.add_argument("--port", type=int, default=0, help="0 = OS-assigned free port")
    args = parser.parse_args()

    cfg = load_config()
    host = args.host or cfg.server.host

    sock, port = _bind_free_port(host, args.port)
    # The shell parses this exact line to learn the port; flush before the
    # server loop blocks so it's never buffered behind us.
    print(f"WRENOTE_PORT={port}", flush=True)

    server = uvicorn.Server(
        uvicorn.Config(
            "wrenote.server:app",
            host=host,
            port=port,
            log_level=cfg.server.log_level,
            reload=False,
        )
    )
    server.run(sockets=[sock])


if __name__ == "__main__":
    main()
