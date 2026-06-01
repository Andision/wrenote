"""Desktop launcher: run the wrenote server in-process and show it in a
native webview window (pywebview).

``python -m wrenote.desktop`` — used both for local preview and, later, as the
PyInstaller entry point. The frontend SPA is served by the FastAPI app at ``/``
(see :mod:`wrenote.server`); the webview points at the loopback URL, so the same
backend works unchanged in a browser, here, or under any other shell.

Hardening (P1):
* **Dynamic port** — bind to an OS-assigned free port, so two installs / a
  stray dev server never collide on a hard-coded 8000.
* **Loopback token** — a random per-launch secret handed to the server via
  ``WRENOTE_AUTH_TOKEN``. The server cookies it onto our webview when the SPA
  loads; other local pages (which share the loopback interface) can't reach the
  API/WebSocket without it.
* **Single instance** — an exclusive lock file under ``~/.wrenote`` keeps a
  second launch from sharing the SQLite DB / recordings / microphone.
"""
from __future__ import annotations

import logging
import os
import secrets
import socket
import sys
import threading
import time
import urllib.request
from pathlib import Path
from typing import IO

import uvicorn
import webview

from .core.config import load_config

log = logging.getLogger(__name__)

WINDOW_TITLE = "Wrenote"
READY_TIMEOUT_S = 30.0
LOCK_PATH = Path.home() / ".wrenote" / "wrenote.lock"

# Keep the lock handle alive for the whole process; the OS lock is released
# when this file object is closed (or the process exits).
_lock_handle: IO[str] | None = None


def _acquire_single_instance_lock(path: Path) -> bool:
    """Take an exclusive, non-blocking lock. Returns False if already held."""
    global _lock_handle
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "w")  # noqa: SIM115 — held open for the process lifetime to hold the lock
    try:
        if sys.platform == "win32":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return False
    _lock_handle = handle
    return True


def _bind_free_port(host: str) -> tuple[socket.socket, int]:
    """Bind a loopback socket to an OS-assigned port; hand it to uvicorn later."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, 0))
    return sock, sock.getsockname()[1]


def _ensure_bundled_binaries_on_path() -> None:
    """Put co-located binaries (ffmpeg) on PATH.

    Upload decoding shells out to ``ffmpeg``. When we launch the env's Python
    directly (or run as a frozen app), the conda env / bundle ``bin`` is not on
    PATH, so ``ffmpeg`` isn't found and uploads fail with "No such file". In dev
    this dir is the conda env's bin (where ffmpeg lives); in a PyInstaller
    bundle it's the dir holding the frozen executable, where we ship ffmpeg.
    """
    candidates = [Path(sys.executable).resolve().parent]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        # PyInstaller .app: bundled binaries (ffmpeg) land in _MEIPASS
        # (Contents/Frameworks), not next to the executable (Contents/MacOS).
        candidates.append(Path(meipass))
    current = os.environ.get("PATH", "")
    parts = current.split(os.pathsep)
    add = [str(d) for d in candidates if str(d) not in parts]
    if add:
        os.environ["PATH"] = os.pathsep.join([*add, current]) if current else os.pathsep.join(add)


def _grant_webview_media() -> None:
    """Auto-grant microphone capture in the macOS WKWebView.

    pywebview's WKUIDelegate doesn't implement the media-capture permission
    callback, so WKWebView denies ``getUserMedia`` by default (the page sees
    ``navigator.mediaDevices`` but capture is refused). Subclass its delegate to
    grant it. The OS still gates real mic access via NSMicrophoneUsageDescription
    + the audio-input entitlement, and only our own loopback page is ever loaded,
    so this just unblocks our own recording UI.
    """
    if sys.platform != "darwin":
        return
    try:
        from webview.platforms import cocoa

        base = cocoa.BrowserView.BrowserDelegate

        class _MediaGrantDelegate(base):  # type: ignore[misc, valid-type]
            def webView_requestMediaCapturePermissionForOrigin_initiatedByFrame_type_decisionHandler_(
                self, web_view, origin, frame, capture_type, decision_handler
            ):
                decision_handler(1)  # WKPermissionDecisionGrant

        cocoa.BrowserView.BrowserDelegate = _MediaGrantDelegate
        log.info("WKWebView media-capture permission delegate installed")
    except Exception:
        log.exception("could not install webview media delegate; mic may be blocked")


def _wait_until_ready(url: str, timeout: float = READY_TIMEOUT_S) -> bool:
    """Poll the server's /health until it answers 200 or we time out."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.0) as resp:  # loopback health probe
                if resp.status == 200:
                    return True
        except Exception:
            time.sleep(0.1)
    return False


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    _ensure_bundled_binaries_on_path()

    if not _acquire_single_instance_lock(LOCK_PATH):
        log.warning("Another Wrenote instance is already running; exiting.")
        webview.create_window(
            WINDOW_TITLE,
            html="<body style='font:15px -apple-system,system-ui,sans-serif;"
            "display:flex;align-items:center;justify-content:center;height:100vh;margin:0;"
            "color:#444'>Wrenote is already running.</body>",
            width=420,
            height=200,
        )
        webview.start()
        return

    cfg = load_config()
    host = cfg.server.host

    # A random per-launch token; the server (imported lazily by uvicorn below)
    # reads it from the environment to gate the API/WebSocket.
    os.environ["WRENOTE_AUTH_TOKEN"] = secrets.token_urlsafe(32)

    sock, port = _bind_free_port(host)
    server = uvicorn.Server(
        uvicorn.Config(
            "wrenote.server:app",
            host=host,
            port=port,
            log_level=cfg.server.log_level,
            reload=False,
        )
    )

    # uvicorn skips signal-handler installation off the main thread, so running
    # Server.run() in a daemon thread is safe; the GUI loop owns the main thread.
    threading.Thread(target=lambda: server.run(sockets=[sock]), name="uvicorn", daemon=True).start()

    base = f"http://{host}:{port}"
    if not _wait_until_ready(f"{base}/health"):
        log.error("Server did not become ready within %.0fs; opening window anyway", READY_TIMEOUT_S)

    log.info("Opening window at %s", base)
    _grant_webview_media()
    window = webview.create_window(WINDOW_TITLE, base, width=1280, height=860)
    window.events.closed += lambda: setattr(server, "should_exit", True)
    webview.start()


if __name__ == "__main__":
    main()
