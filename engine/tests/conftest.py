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

import wrenote.core.config as config_mod
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
            "compute": {
                "runtimes_dir": str(runtimes_dir) if runtimes_dir else "~/.wrenote/runtimes",
                "runtimes_index_url": "",
            },
        }
    )


def _isolate_paths(monkeypatch, tmp_path) -> None:
    """Point the SQLite DB, recordings, the user config file and the SPA dir at
    a per-test tmp dir (the app otherwise hard-codes ``~/.wrenote`` and the
    vite build output)."""
    monkeypatch.setattr(store_mod, "DEFAULT_DB_PATH", tmp_path / "data.db")
    monkeypatch.setattr(recording_mod, "DEFAULT_DIR", tmp_path / "recordings")
    monkeypatch.setattr(config_mod, "USER_CONFIG", tmp_path / "config.yaml")
    # A stub SPA, so the mount at "/" exists whether or not anyone ran
    # `npm run build`. Skipping those tests when the real build is absent was
    # the alternative, and a test that quietly stops running is worse than one
    # that never existed: CI has no node in this job, so they'd never run
    # there, which is precisely where they'd catch a regression.
    app_dir = tmp_path / "spa"
    app_dir.mkdir()
    (app_dir / "index.html").write_text(
        '<!doctype html><html><body><div id="root"></div></body></html>',
        encoding="utf-8",
    )
    monkeypatch.setattr(server, "APP_DIR", app_dir)


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
