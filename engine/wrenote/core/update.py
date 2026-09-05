"""Is there a newer Wrenote?

Models and runtime packs each have an index the engine reads, verifies and
caches; the app itself had no way to learn that a new version exists. This
module reads one more index, ``latest.json``, published alongside every
release by ``.github/workflows/build-tauri.yml`` (written by
``packaging/release/make_latest.py``). Its shape is the one Tauri's updater
plugin reads, so the same file can drive an in-place install later without a
second channel::

    {"version": "0.2.0", "notes": "…", "pub_date": "2026-09-05T00:00:00Z",
     "release_url": "https://github.com/Andision/wrenote/releases/tag/v0.2.0",
     "platforms": {"darwin-aarch64": {"url": "…/Wrenote_0.2.0_aarch64.dmg",
                                      "signature": ""}, …}}

Rules:

* The engine only *reports* — current version, latest, whether that is newer,
  where to get it. Installing is the shell's job, or the user's via the
  release page. A client that compared versions itself would be a bug.
* One request, when a client asks (``GET /v1/update``), at most once per
  ``TTL_S``; never on a timer and never at import. Nothing about the machine
  is sent: it is the same GET the model and runtime downloads make.
* ``update.check: false`` turns the automatic check off. An explicit "check
  now" (``POST /v1/update/check``) still works, because the user asked.
* Codes, not sentences: ``error`` is ``unreachable`` | ``bad_index`` |
  ``no_index`` and the client renders it in the user's language.
"""
from __future__ import annotations

import json
import logging
import re
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .config import UpdateConfig
from .models import _ssl_context

log = logging.getLogger(__name__)

# A successful answer is good for this long; a failed one is retried sooner
# so a flaky network doesn't pin "unreachable" on the About panel for hours.
TTL_S = 6 * 3600
FAIL_TTL_S = 10 * 60
FETCH_TIMEOUT_S = 15

Fetch = Callable[[str], bytes]


# ---------- versions ----------

_VERSION_RE = re.compile(
    r"^v?(?P<core>\d+(?:\.\d+)*)(?:-(?P<pre>[0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$"
)


def parse_version(text: str) -> tuple[Any, ...] | None:
    """A sort key for a semver-ish string, or ``None`` when it isn't one.

    ``v0.2.0``, ``0.2.0-beta.1`` and ``0.2.0+build.7`` all parse; build
    metadata is ignored, a pre-release sorts before its release, and
    pre-release identifiers compare the semver way (numbers numerically and
    below words).
    """
    m = _VERSION_RE.match(text.strip())
    if not m:
        return None
    core = tuple(int(p) for p in m.group("core").split("."))
    core = core + (0,) * (3 - len(core))
    pre = m.group("pre")
    if pre is None:
        return (core, (1,))
    ids: list[tuple[int, Any]] = [
        (0, int(p)) if p.isdigit() else (1, p) for p in pre.split(".")
    ]
    return (core, (0, *ids))


def is_newer(candidate: str, current: str) -> bool:
    """True when ``candidate`` is a version and sorts after ``current``.

    Unparsable input is never "newer": an index with a typo must not nag."""
    a, b = parse_version(candidate), parse_version(current)
    return a is not None and b is not None and a > b


# ---------- platform ----------

_OS = {"darwin": "darwin", "win32": "windows", "linux": "linux"}
_ARCH = {
    "arm64": "aarch64", "aarch64": "aarch64",
    "x86_64": "x86_64", "amd64": "x86_64",
    "i686": "i686", "i386": "i686", "x86": "i686",
}


def tauri_target(os_name: str, arch: str) -> str:
    """The ``platforms`` key for this machine, in the updater's spelling
    (``darwin-aarch64``, ``windows-x86_64``). Unknown parts pass through."""
    return f"{_OS.get(os_name, os_name)}-{_ARCH.get(arch.lower(), arch.lower())}"


# ---------- the index ----------

@dataclass(frozen=True)
class Release:
    version: str
    notes: str
    published_at: str | None
    release_url: str | None
    platforms: dict[str, dict[str, str]]

    @classmethod
    def from_index(cls, data: Any, *, index_url: str) -> Release:
        if not isinstance(data, dict):
            raise ValueError("index is not an object")
        version = str(data.get("version") or "").strip()
        if parse_version(version) is None:
            raise ValueError(f"index has no usable version: {version!r}")
        platforms: dict[str, dict[str, str]] = {}
        for key, entry in (data.get("platforms") or {}).items():
            if isinstance(entry, dict) and entry.get("url"):
                platforms[str(key)] = {
                    "url": str(entry["url"]),
                    "signature": str(entry.get("signature") or ""),
                }
        return cls(
            version=version,
            notes=str(data.get("notes") or ""),
            published_at=(str(data["pub_date"]) if data.get("pub_date") else None),
            release_url=(
                str(data["release_url"]) if data.get("release_url")
                else _release_page(index_url, version)
            ),
            platforms=platforms,
        )


_GITHUB_RE = re.compile(r"^(https://github\.com/[^/]+/[^/]+)/releases/")


def _release_page(index_url: str, version: str) -> str | None:
    """The human page for a release, when the index lives in GitHub releases
    and doesn't say. A mirror publishes ``release_url`` explicitly."""
    m = _GITHUB_RE.match(index_url)
    return f"{m.group(1)}/releases/tag/v{version}" if m else None


def _fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "wrenote-update-check"})
    with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT_S, context=_ssl_context()) as resp:
        return resp.read()


class UpdateChecker:
    """Reads the release index on request and remembers the answer."""

    def __init__(
        self,
        config: UpdateConfig,
        *,
        current: str,
        platform_key: str,
        fetch: Fetch | None = None,
    ) -> None:
        self._enabled = bool(config.check)
        self._index_url = config.index_url.strip()
        self._current = current
        self._platform = platform_key
        self._fetch = fetch or _fetch
        self._cache: tuple[float, dict[str, Any]] | None = None

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)

    def status(self, *, force: bool = False) -> dict[str, Any]:
        """What the client shows. Blocking (one HTTP GET at most); call it in
        a thread. ``force`` is the user's "check now": it ignores both the
        cache and the ``update.check`` setting."""
        base = {
            "current": self._current,
            "enabled": self._enabled,
            "platform": self._platform,
            "index_url": self._index_url,
        }
        if not force and not self._enabled:
            return {**base, **_no_answer(), "checked_at": None}
        now = time.monotonic()
        if not force and self._cache is not None:
            at, answer = self._cache
            ttl = TTL_S if answer["error"] is None else FAIL_TTL_S
            if now - at < ttl:
                return {**base, **answer}
        answer = self._check()
        self._cache = (now, answer)
        return {**base, **answer}

    def _check(self) -> dict[str, Any]:
        checked_at = datetime.now(UTC).isoformat(timespec="seconds")
        if not self._index_url:
            return {**_no_answer(), "checked_at": checked_at, "error": "no_index"}
        try:
            raw = self._fetch(self._index_url)
        except Exception as e:  # DNS, TLS, 404, timeout — all "couldn't ask"
            log.info("update check: %s unreachable (%s: %s)", self._index_url, type(e).__name__, e)
            return {**_no_answer(), "checked_at": checked_at, "error": "unreachable"}
        try:
            release = Release.from_index(json.loads(raw.decode("utf-8")), index_url=self._index_url)
        except (ValueError, UnicodeDecodeError) as e:
            log.warning("update check: %s is not a release index (%s)", self._index_url, e)
            return {**_no_answer(), "checked_at": checked_at, "error": "bad_index"}
        available = is_newer(release.version, self._current)
        if available:
            log.info("update check: %s available (running %s)", release.version, self._current)
        installer = release.platforms.get(self._platform)
        return {
            "checked_at": checked_at,
            "latest": release.version,
            "available": available,
            "download_url": installer["url"] if installer else None,
            "release_url": release.release_url,
            "notes": release.notes,
            "published_at": release.published_at,
            "error": None,
        }


def _no_answer() -> dict[str, Any]:
    return {
        "latest": None,
        "available": False,
        "download_url": None,
        "release_url": None,
        "notes": "",
        "published_at": None,
        "error": None,
    }
