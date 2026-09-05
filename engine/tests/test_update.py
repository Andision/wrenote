"""The update check (core/update.py, api/update.py).

What has to hold: a newer release is recognised and an older or equal one is
not (a typo'd index must never nag); the installer offered is this machine's;
the network is asked once and then remembered; a failure is a code, not a
crash; the automatic check respects the setting, and "check now" ignores it.
"""
from __future__ import annotations

import json

import pytest

import wrenote.core.config as config_mod
from wrenote.core.config import UpdateConfig
from wrenote.core.update import (
    FAIL_TTL_S,
    TTL_S,
    UpdateChecker,
    is_newer,
    parse_version,
    tauri_target,
)

INDEX_URL = "https://github.com/Andision/wrenote/releases/latest/download/latest.json"


def _index(version="0.2.0", **over):
    data = {
        "version": version,
        "notes": "Faster diarization.",
        "pub_date": "2026-09-05T00:00:00Z",
        "platforms": {
            "darwin-aarch64": {"url": f"https://x/Wrenote_{version}_aarch64.dmg", "signature": ""},
            "windows-x86_64": {"url": f"https://x/Wrenote_{version}_x64-setup.exe", "signature": ""},
        },
    }
    data.update(over)
    return json.dumps(data).encode("utf-8")


class Fetcher:
    """A fake network: serves one body, counts calls, can be told to fail."""

    def __init__(self, body: bytes | Exception):
        self.body = body
        self.calls = 0

    def __call__(self, url: str) -> bytes:
        self.calls += 1
        assert url == INDEX_URL
        if isinstance(self.body, Exception):
            raise self.body
        return self.body


def _checker(body, *, current="0.1.0", check=True, platform="darwin-aarch64", index_url=INDEX_URL):
    fetch = Fetcher(body)
    return UpdateChecker(
        UpdateConfig(check=check, index_url=index_url),
        current=current, platform_key=platform, fetch=fetch,
    ), fetch


# ---------- versions ----------

@pytest.mark.parametrize("newer,older", [
    ("0.2.0", "0.1.0"),
    ("0.10.0", "0.9.9"),
    ("1.0.0", "0.99.0"),
    ("v0.2.0", "0.1.9"),
    ("0.2.0", "0.2.0-beta.1"),        # a release beats its pre-release
    ("0.2.0-beta.2", "0.2.0-beta.1"),
    ("0.2.0-rc.1", "0.2.0-beta.9"),   # words compare lexically
    ("0.2.0-beta.a", "0.2.0-beta.9"), # numbers sort below words
    ("0.2.0+build.9", "0.1.0+build.1"),
])
def test_version_ordering(newer, older):
    assert is_newer(newer, older)
    assert not is_newer(older, newer)


def test_equal_versions_are_not_newer():
    assert not is_newer("0.1.0", "0.1.0")
    assert not is_newer("v0.1.0", "0.1.0+local")


def test_garbage_is_never_newer():
    for bad in ("", "latest", "0.2.0.beta", "1.2.3.4.5x", "v", "nope"):
        assert parse_version(bad) is None, bad
        assert not is_newer(bad, "0.1.0")
        assert not is_newer("0.2.0", bad)


def test_short_versions_pad_to_three_parts():
    assert not is_newer("0.2", "0.2.0")
    assert is_newer("0.3", "0.2.9")


def test_tauri_target_uses_the_updaters_spelling():
    assert tauri_target("darwin", "arm64") == "darwin-aarch64"
    assert tauri_target("win32", "x86_64") == "windows-x86_64"
    assert tauri_target("linux", "x86_64") == "linux-x86_64"
    assert tauri_target("plan9", "mips") == "plan9-mips"


# ---------- the checker ----------

def test_a_newer_release_is_reported_with_this_platforms_installer():
    checker, _ = _checker(_index("0.2.0"))
    s = checker.status()
    assert s["available"] is True
    assert s["current"] == "0.1.0" and s["latest"] == "0.2.0"
    assert s["download_url"].endswith("_aarch64.dmg")
    # No release_url in the file: derived from the GitHub index location.
    assert s["release_url"] == "https://github.com/Andision/wrenote/releases/tag/v0.2.0"
    assert s["notes"] == "Faster diarization."
    assert s["published_at"] == "2026-09-05T00:00:00Z"
    assert s["error"] is None and s["checked_at"]


def test_the_running_version_or_an_older_one_is_not_an_update():
    for published in ("0.1.0", "0.0.9", "0.1.0-rc.1"):
        checker, _ = _checker(_index(published))
        s = checker.status()
        assert s["available"] is False and s["latest"] == published, published


def test_a_platform_without_an_installer_still_gets_the_release_page():
    checker, _ = _checker(_index("0.2.0"), platform="linux-x86_64")
    s = checker.status()
    assert s["available"] is True
    assert s["download_url"] is None
    assert s["release_url"].endswith("/releases/tag/v0.2.0")


def test_an_explicit_release_url_wins_over_the_derived_one():
    checker, _ = _checker(_index("0.2.0", release_url="https://mirror.example/wrenote/0.2.0"))
    assert checker.status()["release_url"] == "https://mirror.example/wrenote/0.2.0"


def test_the_answer_is_remembered(monkeypatch):
    checker, fetch = _checker(_index("0.2.0"))
    checker.status()
    checker.status()
    assert fetch.calls == 1
    # Until the answer is stale …
    now = [1000.0]
    monkeypatch.setattr("wrenote.core.update.time.monotonic", lambda: now[0])
    checker.status(force=True)  # resets the clock at t=1000
    now[0] += TTL_S + 1
    checker.status()
    assert fetch.calls == 3
    # … or the user presses "check now".
    checker.status(force=True)
    assert fetch.calls == 4


def test_unreachable_is_a_code_and_is_retried_sooner(monkeypatch):
    checker, fetch = _checker(OSError("no route to host"))
    s = checker.status()
    assert s["error"] == "unreachable" and s["available"] is False and s["latest"] is None
    now = [0.0]
    monkeypatch.setattr("wrenote.core.update.time.monotonic", lambda: now[0])
    checker.status(force=True)
    now[0] += FAIL_TTL_S + 1
    checker.status()
    assert fetch.calls == 3


@pytest.mark.parametrize("body", [
    b"<html>Not Found</html>",
    b"[]",
    json.dumps({"notes": "no version"}).encode(),
    json.dumps({"version": "latest"}).encode(),
    b"\xff\xfe",
])
def test_a_broken_index_is_a_code_not_a_crash(body):
    checker, _ = _checker(body)
    s = checker.status()
    assert s["error"] == "bad_index" and s["available"] is False


def test_no_index_configured():
    checker, fetch = _checker(_index(), index_url="")
    s = checker.status()
    assert s["error"] == "no_index" and fetch.calls == 0


def test_automatic_check_off_means_no_request_unless_asked():
    checker, fetch = _checker(_index("0.2.0"), check=False)
    s = checker.status()
    assert s["enabled"] is False and s["available"] is False and s["checked_at"] is None
    assert fetch.calls == 0
    # "Check now" is the user asking; the switch is about asking on their behalf.
    s = checker.status(force=True)
    assert s["available"] is True and fetch.calls == 1


def test_the_installer_signature_is_carried_for_the_shell():
    body = _index("0.2.0")
    data = json.loads(body)
    data["platforms"]["darwin-aarch64"]["signature"] = "minisign…"
    checker, _ = _checker(json.dumps(data).encode())
    # Not surfaced yet (the engine doesn't install), but the index shape is
    # the updater's, so a signed file parses as-is.
    assert checker.status()["available"] is True


# ---------- the routes ----------

def _swap_checker(client, body, **kw):
    checker, fetch = _checker(body, **kw)
    client.app.state.updates = checker
    return fetch


def test_get_update_reports_and_caches(client):
    fetch = _swap_checker(client, _index("0.2.0"))
    r = client.get("/v1/update")
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is True and body["latest"] == "0.2.0"
    assert body["download_url"].endswith(".dmg")
    client.get("/v1/update")
    assert fetch.calls == 1


def test_check_now_ignores_the_cache_and_the_switch(client):
    fetch = _swap_checker(client, _index("0.2.0"), check=False)
    assert client.get("/v1/update").json()["enabled"] is False
    r = client.post("/v1/update/check")
    assert r.status_code == 200 and r.json()["available"] is True
    assert fetch.calls == 1


def test_settings_persist_to_the_user_config_and_apply_live(client):
    fetch = _swap_checker(client, _index("0.2.0"))
    r = client.post("/v1/update/settings", json={"check": False})
    assert r.status_code == 200 and r.json() == {"check": False}
    # Survives a restart …
    written = config_mod.user_config_path().read_text(encoding="utf-8")
    assert "check: false" in written
    # … and holds now, without one.
    assert client.get("/v1/update").json()["enabled"] is False
    assert fetch.calls == 0
    client.post("/v1/update/settings", json={"check": True})
    assert client.get("/v1/update").json()["available"] is True


def test_the_default_test_app_never_reaches_out(client):
    """The suite's config disables the check and clears the index; a test that
    forgets to swap the checker gets an honest 'off', not a GitHub request."""
    s = client.get("/v1/update").json()
    assert s["enabled"] is False and s["error"] is None
