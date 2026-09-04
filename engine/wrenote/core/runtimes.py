"""Compute runtime packs: probe → select → ensure → activate.

The engine's native inference bindings (llama-cpp-python, pywhispercpp) are
compiled per accelerator: CUDA, Vulkan, Metal, plain CPU. Shipping one
installer per combination doesn't scale, and shipping *all* of them makes every
user download hundreds of MB of DLLs they can't use. So the app bundle carries
one **built-in** runtime for its platform (Metal on macOS arm64, CPU on
Windows) and fetches an accelerated **runtime pack** on demand into
``~/.wrenote/runtimes/<variant>/`` — the same first-run mechanism as the model
downloads in :mod:`wrenote.core.models`.

Lifecycle, all owned by :class:`RuntimeManager`:

1. **probe** — the platform adapter describes the hardware and lists candidate
   variants, best first (``HardwareInfo.accelerators``).
2. **select** — intersect that with the ``compute.accelerator`` config (``auto``
   or a pin), drop variants previously marked bad, and pick the first one that
   is installed. Falls through to the built-in runtime, which always exists.
3. **ensure** — download + verify + unpack a pack that isn't installed yet.
   Packs are listed in an index (``compute.runtimes_index_url``, published by
   ``.github/workflows/build-runtimes.yml``) and built by
   ``packaging/runtimes/build_pack.py``.
4. **activate** — make the pack's modules win over the built-in ones **before**
   any native module is imported. A PyInstaller-frozen app resolves bundled
   modules through its own importer at the front of ``sys.meta_path``, so a
   plain ``sys.path`` entry would lose; instead a finder for exactly the
   pack's top-level modules (from its manifest) is put in front of it. The
   backends import ``llama_cpp`` / ``pywhispercpp`` lazily inside ``load()``
   for exactly this reason.

If a native backend fails to load or crashes, the caller marks the variant bad
(:meth:`RuntimeManager.mark_bad`); the choice is persisted so the next launch
skips it and degrades one step down the chain instead of crash-looping.

Pack layout (a zip, see ``packaging/runtimes/build_pack.py``)::

    MANIFEST.json      {"schema": 1, "variant", "platform_tag", "python": "3.11",
                        "version", "modules": [top-level import names], "packages": {...}}
    site-packages/     the pip-installed bindings (no deps: the app ships those)
    bin/               optional extra shared libraries (e.g. CUDA runtime DLLs)
"""
from __future__ import annotations

import hashlib
import importlib.abc
import importlib.machinery
import json
import logging
import os
import shutil
import sys
import tempfile
import time
import urllib.request
import zipfile
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from importlib.machinery import ModuleSpec
from pathlib import Path
from typing import Any

from ..platform import AcceleratorNote, HardwareInfo, PlatformAdapter
from .config import ComputeConfig
from .models import _ssl_context

log = logging.getLogger(__name__)

#: Every runtime variant the engine knows how to talk about. Order is not
#: preference — preference comes from the platform adapter.
VARIANTS: tuple[str, ...] = ("cpu", "metal", "cuda", "vulkan")

#: The runtime compiled into the app bundle per platform tag. Anything not
#: listed ships the portable CPU build. CI must keep this in sync with the
#: wheels it installs before freezing (see .github/actions/build-engine).
BUILTIN_VARIANT: dict[str, str] = {
    "darwin-arm64": "metal",
}

STATE_FILE = "state.json"
PACK_MANIFEST = "MANIFEST.json"
PACK_SCHEMA = 1
INDEX_SCHEMA = 1
#: Top-level modules a pack provides when its manifest doesn't say.
#: Top-level modules a pack provides. Also the modules whose import commits the
#: process to one runtime — see :meth:`RuntimeManager.can_reactivate`.
DEFAULT_PACK_MODULES: tuple[str, ...] = ("llama_cpp", "pywhispercpp", "_pywhispercpp")
_CHUNK = 1 << 20
_INDEX_TTL_S = 300.0

ProgressCallback = Callable[[float, str], None]


class RuntimeUnavailable(RuntimeError):
    """A runtime pack is not installed and cannot be fetched (no index, no pack
    for this platform, wrong Python, checksum mismatch, …)."""


def python_tag() -> str:
    return f"{sys.version_info[0]}.{sys.version_info[1]}"


@dataclass(frozen=True)
class RuntimePack:
    variant: str
    platform_tag: str  # "<os>-<arch>", e.g. "win32-x86_64"
    builtin: bool  # compiled into the app bundle → always installed
    path: Path | None  # install location for non-builtin packs

    @property
    def manifest_path(self) -> Path | None:
        return None if self.path is None else self.path / PACK_MANIFEST

    @property
    def installed(self) -> bool:
        if self.builtin:
            return True
        return self.manifest_path is not None and self.manifest_path.exists()

    def manifest(self) -> dict[str, Any]:
        """The installed pack's manifest (``{}`` for the built-in / missing)."""
        if self.manifest_path is None or not self.manifest_path.exists():
            return {}
        try:
            with open(self.manifest_path, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            log.warning("unreadable pack manifest %s", self.manifest_path)
            return {}

    def to_dict(self) -> dict[str, Any]:
        m = self.manifest()
        return {
            "variant": self.variant,
            "platform_tag": self.platform_tag,
            "builtin": self.builtin,
            "installed": self.installed,
            "path": str(self.path) if self.path else None,
            "version": m.get("version"),
            "packages": m.get("packages"),
        }


@dataclass(frozen=True)
class RuntimeSelection:
    variant: str
    reason: str  # "pinned" | "hardware" | "builtin" | "fallback"
    chain: tuple[str, ...]  # candidates considered, in order
    skipped: tuple[str, ...]  # candidates passed over (bad or not installed)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["chain"] = list(self.chain)
        d["skipped"] = list(self.skipped)
        return d


@dataclass(frozen=True)
class PackRelease:
    """One downloadable pack, as listed in the index."""

    variant: str
    platform_tag: str
    python: str
    version: str
    url: str
    sha256: str
    size: int
    modules: tuple[str, ...]

    @classmethod
    def from_index(cls, row: dict[str, Any]) -> PackRelease:
        return cls(
            variant=str(row["variant"]),
            platform_tag=str(row["platform_tag"]),
            python=str(row.get("python", "")),
            version=str(row.get("version", "")),
            url=str(row["url"]),
            sha256=str(row.get("sha256", "")).lower(),
            size=int(row.get("size") or 0),
            modules=tuple(row.get("modules") or DEFAULT_PACK_MODULES),
        )

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["modules"] = list(self.modules)
        return d


#: Variants that use the GPU. Ordered lightest-download first: on Windows the
#: CUDA pack is ~20x the size of the Vulkan one (NVIDIA's cuBLAS alone is
#: ~550 MB), and for this workload — streaming whisper plus a small translation
#: model — Vulkan already captures most of the GPU win. So Vulkan is what a
#: first run recommends to an NVIDIA user, with CUDA offered as an upgrade.
ACCELERATED = ("vulkan", "metal", "cuda")

#: English rendering of a :class:`RuntimeOption` note code. As with
#: :data:`wrenote.platform.base.NOTE_TEXT`, the client owns what a user reads —
#: including *why* anyone would take the bigger CUDA build over Vulkan, which is
#: a sentence, not a fact this module knows.
OPTION_NOTE_TEXT: dict[str, str] = {
    "builtin": "Built into the app · nothing to download",
    "installed": "Installed",
    "download": "{mb} MB download",
    "unavailable": "Not available on this machine",
    "unpublished": "Not published for this machine yet",
    "size_unknown": "Download size unknown until the pack index is checked",
}


@dataclass(frozen=True)
class RuntimeOption:
    """One runtime a person could choose, with the reasoning shown to them.

    This is what the first-run wizard renders as a card and what Settings →
    Compute annotates its rows with. ``recommended`` marks exactly one option,
    ``blocked_reason`` explains a variant the hardware rules out (an
    accelerator that silently disappears reads as a bug).
    """

    variant: str
    usable: bool
    installed: bool
    builtin: bool
    recommended: bool
    accelerated: bool
    note_code: str  # key into OPTION_NOTE_TEXT — the client renders it
    download_mb: int | None  # None = nothing to fetch (built-in or already installed)
    #: The hardware verdict for this variant, or ``None`` when the platform had
    #: nothing to say. Same code+params shape as the note.
    hardware: AcceleratorNote | None = None

    @property
    def note(self) -> str:
        """English rendering, for logs and clients that don't localize."""
        mb = "" if self.download_mb is None else str(self.download_mb)
        return OPTION_NOTE_TEXT.get(self.note_code, self.note_code).format(mb=mb)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["note"] = self.note
        d["hardware"] = self.hardware.to_dict() if self.hardware else None
        return d


def platform_tag(hw: HardwareInfo) -> str:
    return f"{hw.os}-{hw.arch}"


def builtin_variant_for(tag: str) -> str:
    """What the bundle for ``tag`` was compiled with. Overridable via
    ``WRENOTE_BUILTIN_RUNTIME`` so a CI matrix entry that installs e.g. the
    Vulkan wheel can declare it without a code change."""
    env = os.environ.get("WRENOTE_BUILTIN_RUNTIME", "").strip().lower()
    if env in VARIANTS:
        return env
    return BUILTIN_VARIANT.get(tag, "cpu")


# ---------- import routing ----------


class PackFinder(importlib.abc.MetaPathFinder):
    """Resolve a pack's top-level modules from its ``site-packages`` first.

    Installed at ``sys.meta_path[0]`` so it runs before PyInstaller's frozen
    importer (and before the normal path finder in dev). Only the names in
    ``modules`` are routed; everything else falls through untouched, so the
    pack cannot shadow the app's numpy, pydantic, …
    """

    def __init__(self, site: Path, modules: Sequence[str]) -> None:
        self.site = site
        self.modules = frozenset(modules)

    def find_spec(
        self, fullname: str, path: Sequence[str] | None = None, target: Any = None
    ) -> ModuleSpec | None:
        top = fullname.partition(".")[0]
        if top not in self.modules:
            return None
        # Top-level: look only in the pack. Submodules: ``path`` is the parent
        # package's __path__, which already points into the pack.
        search = [str(self.site)] if path is None else list(path)
        return importlib.machinery.PathFinder.find_spec(fullname, search, target)

    def __repr__(self) -> str:
        return f"<PackFinder {sorted(self.modules)} @ {self.site}>"


# ---------- manager ----------


class RuntimeManager:
    def __init__(
        self,
        config: ComputeConfig,
        platform: PlatformAdapter,
        *,
        builtin: str | None = None,
    ) -> None:
        self._config = config
        self._platform = platform
        self._hardware = platform.probe_hardware()
        self._tag = platform_tag(self._hardware)
        self._builtin = builtin or builtin_variant_for(self._tag)
        self._dir = Path(config.runtimes_dir).expanduser()
        self._active: RuntimeSelection | None = None
        self._finder: PackFinder | None = None
        self._path_entry: str | None = None
        self._bad: dict[str, str] = self._load_state().get("bad", {})
        self._index_cache: tuple[float, list[PackRelease]] | None = None
        self._index_error: str | None = None

    # --- introspection ----------------------------------------------------

    @property
    def hardware(self) -> HardwareInfo:
        return self._hardware

    @property
    def platform_tag(self) -> str:
        return self._tag

    @property
    def builtin(self) -> str:
        return self._builtin

    @property
    def active(self) -> RuntimeSelection | None:
        return self._active

    @property
    def runtimes_dir(self) -> Path:
        return self._dir

    def pack(self, variant: str) -> RuntimePack:
        if variant not in VARIANTS:
            raise ValueError(f"unknown runtime variant {variant!r}; known: {VARIANTS}")
        builtin = variant == self._builtin
        return RuntimePack(
            variant=variant,
            platform_tag=self._tag,
            builtin=builtin,
            path=None if builtin else self._dir / variant,
        )

    # --- 1 + 2: probe & select ---------------------------------------------

    def candidates(self) -> tuple[str, ...]:
        """Variants worth trying on this machine, best first.

        ``compute.accelerator = auto`` → the platform's hardware ranking;
        a pinned value → that variant first. The built-in runtime is always
        appended so the chain can never be empty, and previously bad
        variants are dropped (except the built-in one — it has to work).
        """
        pin = self._config.accelerator.lower()
        if pin == "auto":
            chain = list(self._hardware.accelerators)
        else:
            if pin not in VARIANTS:
                raise ValueError(
                    f"compute.accelerator={pin!r} is not one of {VARIANTS} or 'auto'"
                )
            chain = [pin]
        if self._builtin not in chain:
            chain.append(self._builtin)
        seen: list[str] = []
        for v in chain:
            if v in seen:
                continue
            if v in self._bad and v != self._builtin:
                continue
            seen.append(v)
        return tuple(seen)

    def select(self) -> RuntimeSelection:
        """Pick the first candidate that is installed (never fetches)."""
        chain = self.candidates()
        skipped: list[str] = []
        pinned = self._config.accelerator.lower() != "auto"
        for v in chain:
            if self.pack(v).installed:
                if v == self._builtin and skipped:
                    reason = "fallback"
                elif v == self._builtin:
                    reason = "builtin"
                elif pinned:
                    reason = "pinned"
                else:
                    reason = "hardware"
                return RuntimeSelection(v, reason, chain, tuple(skipped))
            skipped.append(v)
        # Unreachable in practice: the built-in pack is always installed.
        return RuntimeSelection(self._builtin, "builtin", chain, tuple(skipped))

    def options(self, *, releases: dict[str, PackRelease] | None = None) -> list[RuntimeOption]:
        """Every runtime a person can choose here, best first, one recommended.

        Ordering is *what to offer*, not the fallback chain :meth:`candidates`
        computes: the recommendation is the lightest accelerated variant this
        machine can actually run (see :data:`ACCELERATED`), because a first run
        should not cost a 700 MB download to get most of the GPU win. Heavier
        accelerated variants follow as upgrades, then the CPU fallback, then
        anything the hardware rules out — carrying the reason, so the UI can
        say *why* CUDA is missing instead of just not showing it.

        ``releases`` (from :meth:`index`) adds download sizes; without it the
        sizes are ``None`` and no network call happens.
        """
        usable = list(self._hardware.accelerators)
        offered = [v for v in usable if v in ACCELERATED]
        offered.sort(key=lambda v: ACCELERATED.index(v))
        chosen = next(
            (v for v in offered if self._available(v, releases)),
            self._builtin,
        )

        rows: list[RuntimeOption] = []
        for variant in [*offered, *(v for v in usable if v not in offered)]:
            rows.append(self._option(variant, chosen, releases))
        # Variants the hardware ruled out: keep them visible with the reason.
        for note in self._hardware.notes:
            if not note.usable and not any(r.variant == note.variant for r in rows):
                rows.append(self._option(note.variant, chosen, releases, usable=False))
        return rows

    def _available(self, variant: str, releases: dict[str, PackRelease] | None) -> bool:
        """Can this variant actually be used — installed, built in, or (when the
        index was fetched) published for this machine?"""
        pack = self.pack(variant)
        if pack.installed:
            return True
        return releases is not None and variant in releases

    def _option(
        self,
        variant: str,
        chosen: str,
        releases: dict[str, PackRelease] | None,
        *,
        usable: bool = True,
    ) -> RuntimeOption:
        pack = self.pack(variant)
        note = self._hardware.note_for(variant)
        release = (releases or {}).get(variant)
        size_mb = None if (pack.installed or release is None) else max(1, release.size >> 20)
        if not usable:
            code = "unavailable"
        elif pack.builtin:
            code = "builtin"
        elif pack.installed:
            code = "installed"
        elif size_mb is not None:
            code = "download"
        elif releases is not None:
            code = "unpublished"
        else:
            code = "size_unknown"
        return RuntimeOption(
            variant=variant,
            usable=usable,
            installed=pack.installed,
            builtin=pack.builtin,
            recommended=usable and variant == chosen,
            accelerated=variant in ACCELERATED,
            note_code=code,
            download_mb=size_mb,
            hardware=note,
        )

    # --- 3: ensure ----------------------------------------------------------

    def index(self, *, refresh: bool = False) -> list[PackRelease]:
        """Packs published for this platform + Python, from the index URL.

        Cached for a few minutes. Raises :class:`RuntimeUnavailable` when the
        index can't be fetched or parsed; :meth:`status` reports that instead
        of failing.
        """
        now = time.monotonic()
        if not refresh and self._index_cache and now - self._index_cache[0] < _INDEX_TTL_S:
            return self._index_cache[1]
        url = self._config.runtimes_index_url
        if not url:
            raise RuntimeUnavailable("no runtime index configured (compute.runtimes_index_url)")
        try:
            with urllib.request.urlopen(
                urllib.request.Request(url), timeout=15, context=_ssl_context()
            ) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as e:  # network, JSON, file-not-found — all "unavailable"
            self._index_error = f"{e.__class__.__name__}: {e}"
            raise RuntimeUnavailable(f"could not fetch runtime index {url}: {e}") from e
        if not isinstance(data, dict) or int(data.get("schema", 0)) != INDEX_SCHEMA:
            self._index_error = "unsupported index schema"
            raise RuntimeUnavailable(f"runtime index {url}: unsupported schema")
        py = python_tag()
        packs: list[PackRelease] = []
        for row in data.get("packs", []):
            try:
                rel = PackRelease.from_index(row)
            except (KeyError, TypeError, ValueError):
                continue
            if rel.platform_tag == self._tag and (not rel.python or rel.python == py):
                packs.append(rel)
        self._index_cache = (now, packs)
        self._index_error = None
        return packs

    def release_for(self, variant: str) -> PackRelease | None:
        return next((p for p in self.index() if p.variant == variant), None)

    def ensure(self, variant: str, progress: ProgressCallback | None = None) -> RuntimePack:
        """Make ``variant`` installed, downloading it if needed. Blocking.

        ``progress(fraction, status)`` is called from the calling thread: the
        download phase moves 0→0.9, verification + unpacking 0.9→1.0.
        Raises :class:`RuntimeUnavailable` when the pack can't be obtained.
        """
        report = progress or (lambda _f, _s: None)
        pack = self.pack(variant)
        if pack.installed:
            report(1.0, f"{variant} runtime ready")
            return pack
        rel = self.release_for(variant)
        if rel is None:
            raise RuntimeUnavailable(
                f"no {variant!r} runtime pack is published for {self._tag} / Python "
                f"{python_tag()}; the built-in runtime ({self._builtin!r}) is used instead"
            )
        assert pack.path is not None
        self._dir.mkdir(parents=True, exist_ok=True)
        archive = self._dir / f"{variant}.zip.partial"
        try:
            self._download(rel, archive, report)
            report(0.9, "verifying")
            digest = _sha256(archive)
            if rel.sha256 and digest != rel.sha256:
                raise RuntimeUnavailable(
                    f"{variant} runtime pack checksum mismatch (got {digest[:12]}…, "
                    f"expected {rel.sha256[:12]}…)"
                )
            report(0.93, "unpacking")
            self._unpack(archive, pack.path, rel)
        finally:
            archive.unlink(missing_ok=True)
        # A freshly installed pack is a fresh chance for a variant marked bad.
        if variant in self._bad:
            self.clear_bad(variant)
        report(1.0, f"{variant} runtime installed")
        log.info("installed runtime pack %s %s → %s", variant, rel.version, pack.path)
        return pack

    def _download(self, rel: PackRelease, dest: Path, report: ProgressCallback) -> None:
        mb = 1 << 20
        req = urllib.request.Request(rel.url)
        try:
            resp = urllib.request.urlopen(req, timeout=30, context=_ssl_context())
        except Exception as e:
            raise RuntimeUnavailable(f"download failed for {rel.url}: {e}") from e
        total = int(resp.headers.get("Content-Length") or 0) or rel.size
        done = 0
        try:
            with open(dest, "wb") as f:
                while True:
                    chunk = resp.read(_CHUNK)
                    if not chunk:
                        break
                    f.write(chunk)
                    done += len(chunk)
                    frac = min(0.9, 0.9 * done / total) if total else 0.0
                    report(frac, f"downloading {rel.variant} runtime {done // mb}/{total // mb} MB")
        finally:
            resp.close()
        if total and done < total:
            raise RuntimeUnavailable(f"{rel.variant} runtime download truncated ({done}/{total} bytes)")

    def _unpack(self, archive: Path, dest: Path, rel: PackRelease) -> None:
        tmp = Path(tempfile.mkdtemp(prefix=f"{rel.variant}.", dir=self._dir))
        try:
            with zipfile.ZipFile(archive) as z:
                for member in z.namelist():
                    # Refuse anything that would escape the target dir.
                    if member.startswith(("/", "\\")) or ".." in Path(member).parts:
                        raise RuntimeUnavailable(f"runtime pack contains an unsafe path: {member}")
                z.extractall(tmp)
            manifest_path = tmp / PACK_MANIFEST
            if not manifest_path.exists():
                raise RuntimeUnavailable("runtime pack has no MANIFEST.json")
            with open(manifest_path, encoding="utf-8") as f:
                manifest = json.load(f)
            _validate_manifest(manifest, rel.variant, self._tag)
            if dest.exists():
                shutil.rmtree(dest)
            os.replace(tmp, dest)
        except zipfile.BadZipFile as e:
            raise RuntimeUnavailable(f"runtime pack is not a valid zip: {e}") from e
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def remove(self, variant: str) -> bool:
        """Delete an installed (non-builtin) pack. Returns whether anything was removed."""
        pack = self.pack(variant)
        if pack.builtin or pack.path is None or not pack.path.exists():
            return False
        if self._active and self._active.variant == variant:
            log.warning("removing the active runtime pack %s; restart to apply", variant)
        shutil.rmtree(pack.path)
        return True

    # --- 4: activate --------------------------------------------------------

    def activate(self, selection: RuntimeSelection | None = None) -> RuntimeSelection:
        """Route the selected pack's modules ahead of the built-in ones. Idempotent.
        Must run before any native backend module is imported (they import
        lazily in ``load()``)."""
        if self._active is not None:
            return self._active
        sel = selection or self.select()
        pack = self.pack(sel.variant)
        if not pack.builtin and pack.path is not None:
            manifest = pack.manifest()
            wanted_py = str(manifest.get("python") or "")
            if wanted_py and wanted_py != python_tag():
                log.error(
                    "runtime pack %s was built for Python %s, this engine runs %s; "
                    "marking it bad", sel.variant, wanted_py, python_tag(),
                )
                self.mark_bad(sel.variant, f"built for Python {wanted_py}")
                return self.activate(self.select())
            site = pack.path / "site-packages"
            modules = manifest.get("modules") or list(DEFAULT_PACK_MODULES)
            self._finder = PackFinder(site, modules)
            sys.meta_path.insert(0, self._finder)
            dll_dir = pack.path / "bin"
            if dll_dir.exists():
                add_dll = getattr(os, "add_dll_directory", None)
                if add_dll is not None:
                    add_dll(str(dll_dir))
                # add_dll_directory only affects loads that use the
                # LOAD_LIBRARY_SEARCH_* flags. Extension modules (.pyd) do;
                # llama-cpp-python does not — it loads llama.dll through
                # ctypes with winmode=0, i.e. the standard search order, which
                # reads PATH. A CUDA pack's cudart/cublas live here, so put the
                # directory on both paths or llama.dll won't resolve them.
                path = os.environ.get("PATH", "")
                os.environ["PATH"] = f"{dll_dir}{os.pathsep}{path}" if path else str(dll_dir)
                self._path_entry = str(dll_dir)
        self._active = sel
        log.info(
            "compute runtime: %s (%s; chain=%s; builtin=%s)",
            sel.variant, sel.reason, "→".join(sel.chain), self._builtin,
        )
        return sel

    def deactivate(self) -> None:
        """Undo :meth:`activate`'s import routing and its DLL path entry."""
        if self._finder is not None and self._finder in sys.meta_path:
            sys.meta_path.remove(self._finder)
        self._finder = None
        if self._path_entry:
            parts = os.environ.get("PATH", "").split(os.pathsep)
            kept = [p for p in parts if p != self._path_entry]
            if len(kept) != len(parts):
                os.environ["PATH"] = os.pathsep.join(kept)
        self._path_entry = None
        self._active = None

    def can_reactivate(self) -> bool:
        """Is switching runtimes still free in this process?

        Only until a native backend is imported. The bindings load lazily inside
        each backend's ``load()``, so during first-run setup — models not
        downloaded, nothing transcribed yet — nothing has touched them and the
        routing can simply be redone. Once they are in ``sys.modules`` their
        DLLs are mapped into the process for good and only a restart helps.
        """
        return not any(m in sys.modules for m in DEFAULT_PACK_MODULES)

    def reactivate(self) -> RuntimeSelection | None:
        """Re-run selection and routing in place; ``None`` if it's too late."""
        if not self.can_reactivate():
            return None
        self.deactivate()
        return self.activate()

    def set_accelerator(self, accelerator: str) -> None:
        """Point the in-memory config at a new choice (the caller persists it).
        Without this, :meth:`reactivate` would just re-pick the old variant."""
        self._config.accelerator = accelerator

    # --- failure feedback -----------------------------------------------------

    def mark_bad(self, variant: str, reason: str) -> None:
        """Remember that ``variant`` failed here so future launches skip it."""
        if variant == self._builtin:
            log.error("built-in runtime %s failed (%s); nothing to fall back to", variant, reason)
            return
        self._bad[variant] = reason
        self._save_state()
        log.warning("compute runtime %s marked bad: %s", variant, reason)

    def clear_bad(self, variant: str | None = None) -> None:
        """Forget bad marks (all, or one variant)."""
        if variant is None:
            self._bad = {}
        else:
            self._bad.pop(variant, None)
        self._save_state()

    @property
    def bad(self) -> dict[str, str]:
        return dict(self._bad)

    # --- VRAM budgeting -----------------------------------------------------

    def gpu_layers_for(self, model_size_mb: int, *, already_allocated_mb: int = 0) -> int:
        """``n_gpu_layers`` to offload a model of ``model_size_mb`` with.

        ``-1`` = everything, ``0`` = keep on CPU. Explicit ``compute.gpu_layers``
        wins. On the CPU runtime it's always 0; with unified memory (Apple
        Silicon) or unknown VRAM we offload everything and trust the driver;
        with a discrete GPU we offload only if the whole model (plus ~20% for
        KV cache / activations) fits in the remaining budget. Partial offload is
        a later refinement — its interface is this same function.
        """
        if self._config.gpu_layers is not None:
            return self._config.gpu_layers
        active = self._active.variant if self._active else self.select().variant
        if active == "cpu":
            return 0
        vram = self.vram_budget_mb()
        if vram is None:
            return -1
        need = int(model_size_mb * 1.2) + already_allocated_mb
        return -1 if need <= vram else 0

    def vram_budget_mb(self) -> int | None:
        """Usable VRAM: config override, else 90% of the largest discrete GPU;
        ``None`` for unified memory / unknown (= no budgeting)."""
        if self._config.vram_budget_mb is not None:
            return self._config.vram_budget_mb
        discrete = [g.vram_mb for g in self._hardware.gpus if g.vram_mb and not g.unified_memory]
        if not discrete:
            return None
        return int(max(discrete) * 0.9)

    # --- status -------------------------------------------------------------

    def status(self, *, include_index: bool = False) -> dict[str, Any]:
        """Everything a settings UI needs to render the compute panel.

        With ``include_index`` the published packs are looked up (network;
        call from a worker thread) and each pack row gains ``available`` +
        ``release``; an unreachable index is reported, never raised.
        """
        selection = self._active or self.select()
        packs = [self.pack(v).to_dict() for v in VARIANTS]
        index_info: dict[str, Any] = {"url": self._config.runtimes_index_url, "checked": False}
        releases: dict[str, PackRelease] | None = None
        if include_index:
            index_info["checked"] = True
            try:
                releases = {r.variant: r for r in self.index()}
                index_info["reachable"] = True
                index_info["error"] = None
            except RuntimeUnavailable as e:
                releases = {}
                index_info["reachable"] = False
                index_info["error"] = str(e)
            for row in packs:
                rel = releases.get(row["variant"])
                row["available"] = row["builtin"] or rel is not None
                row["release"] = rel.to_dict() if rel else None
        return {
            "platform_tag": self._tag,
            "python": python_tag(),
            "hardware": self._hardware.to_dict(),
            "capabilities": self._platform.capabilities.to_dict(),
            "config": self._config.model_dump(),
            "builtin": self._builtin,
            "candidates": list(self.candidates()),
            "selection": selection.to_dict(),
            "active": self._active.variant if self._active else None,
            "packs": packs,
            "options": [o.to_dict() for o in self.options(releases=releases)],
            "can_switch_without_restart": self.can_reactivate(),
            "bad": self.bad,
            "vram_budget_mb": self.vram_budget_mb(),
            "index": index_info,
        }

    # --- persistence --------------------------------------------------------

    def _state_path(self) -> Path:
        return self._dir / STATE_FILE

    def _load_state(self) -> dict[str, Any]:
        try:
            with open(self._state_path(), encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except FileNotFoundError:
            return {}
        except (OSError, json.JSONDecodeError):
            log.warning("unreadable runtime state at %s; ignoring", self._state_path())
            return {}

    def _save_state(self) -> None:
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            with open(self._state_path(), "w", encoding="utf-8") as f:
                json.dump({"bad": self._bad}, f, indent=2)
        except OSError:
            log.exception("could not persist runtime state to %s", self._state_path())


# ---------- helpers ----------


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(_CHUNK):
            h.update(chunk)
    return h.hexdigest()


def _validate_manifest(manifest: Any, variant: str, tag: str) -> None:
    if not isinstance(manifest, dict):
        raise RuntimeUnavailable("runtime pack manifest is not an object")
    if int(manifest.get("schema", 0)) != PACK_SCHEMA:
        raise RuntimeUnavailable(f"runtime pack manifest schema {manifest.get('schema')!r} unsupported")
    if manifest.get("variant") != variant:
        raise RuntimeUnavailable(
            f"runtime pack is for variant {manifest.get('variant')!r}, expected {variant!r}"
        )
    if manifest.get("platform_tag") != tag:
        raise RuntimeUnavailable(
            f"runtime pack is for {manifest.get('platform_tag')!r}, this machine is {tag!r}"
        )
    py = str(manifest.get("python") or "")
    if py and py != python_tag():
        raise RuntimeUnavailable(
            f"runtime pack was built for Python {py}, this engine runs {python_tag()}"
        )
