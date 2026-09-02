"""Shared test fixtures for the wrenote backend.

These exist to give the architecture refactor a *behavior-unchanged* safety net
(see REFACTOR_PLAN.md, Phase 0). They run against the current module-global
``wrenote.server.app`` — we deliberately do NOT depend on a not-yet-existing
``create_app()`` factory, so the same tests stay valid before and after the
refactor introduces one.

Isolation strategy (the app hard-codes ``~/.wrenote`` paths and loads the repo
config at startup, so we patch those *before* the TestClient triggers lifespan):

* ``load_config`` → a pure-mock config (stt/translator/chat = mock,
  vad/speaker = disabled) so no native model (whisper/llama/onnx) is ever
  instantiated.
* ``store.DEFAULT_DB_PATH`` / ``recording.DEFAULT_DIR`` → a per-test tmp dir,
  so tests never touch the real ``~/.wrenote/data.db`` or recordings.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import wrenote.core.recording as recording_mod
import wrenote.core.store as store_mod
import wrenote.server as server
from wrenote.core.config import Config


def _mock_config(runtimes_dir=None) -> Config:
    """All-mock, native-dep-free config. `model_validate` bypasses the env-var
    source (same path `load_config(use_env=False)` uses)."""
    return Config.model_validate(
        {
            "stt": {"backend": "mock"},
            "vad": {"backend": "disabled"},
            "translator": {"backend": "mock"},
            "speaker": {"backend": "disabled"},
            "chat": {"backend": "mock"},
            # Never read/write the real ~/.wrenote/runtimes/state.json.
            "compute": {"runtimes_dir": str(runtimes_dir) if runtimes_dir else "~/.wrenote/runtimes"},
        }
    )


def _isolate_paths(monkeypatch, tmp_path) -> None:
    """Point the SQLite DB + recordings at a per-test tmp dir (the app otherwise
    hard-codes ``~/.wrenote``)."""
    monkeypatch.setattr(store_mod, "DEFAULT_DB_PATH", tmp_path / "data.db")
    monkeypatch.setattr(recording_mod, "DEFAULT_DIR", tmp_path / "recordings")


@pytest.fixture
def client(monkeypatch, tmp_path):
    """A TestClient over a fresh ``create_app(mock_config)`` with isolated state.

    Function-scoped: each test gets a fresh empty SQLite DB and recordings dir.
    The ``with TestClient(...)`` block runs the app's lifespan (startup +
    shutdown), so ``app.state.store`` etc. are wired exactly as in production.
    """
    _isolate_paths(monkeypatch, tmp_path)
    with TestClient(server.create_app(_mock_config(tmp_path / "runtimes"))) as c:
        yield c


@pytest.fixture
def auth_client(monkeypatch, tmp_path):
    """Like ``client`` but with loopback auth enabled (token ``test-secret``).

    Only possible because ``create_app(auth_token=...)`` makes auth per-app —
    the previous import-time ``WRENOTE_AUTH_TOKEN`` gating couldn't be toggled
    per test. Exercises the 401 / cookie / bearer / query-token paths.
    """
    _isolate_paths(monkeypatch, tmp_path)
    app = server.create_app(_mock_config(tmp_path / "runtimes"), auth_token="test-secret")
    with TestClient(app) as c:
        yield c
