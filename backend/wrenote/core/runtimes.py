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
3. **ensure** — download + unpack a pack that isn't installed yet. *Not wired
   yet*: raises :class:`RuntimeUnavailable` until CI publishes packs. The
   interface is fixed so config, API and UI can code against it now.
4. **activate** — put the pack's ``site-packages`` at the front of ``sys.path``
   (and its DLL dir on the loader path on Windows) **before** any native
   module is imported. The backends import ``llama_cpp`` / ``pywhispercpp``
   lazily inside ``load()`` for exactly this reason.

If a native backend fails to load or crashes, the caller marks the variant bad
(:meth:`RuntimeManager.mark_bad`); the choice is persisted so the next launch
skips it and degrades one step down the chain instead of crash-looping.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ..platform import HardwareInfo, PlatformAdapter
from .config import ComputeConfig

log = logging.getLogger(__name__)

#: Every runtime variant the engine knows how to talk about. Order is not
#: preference — preference comes from the platform adapter.
VARIANTS: tuple[str, ...] = ("cpu", "metal", "cuda", "vulkan")

#: The runtime compiled into the app bundle per platform tag. Anything not
#: listed ships the portable CPU build. CI must keep this in sync with the
#: wheels it installs before freezing (see .github/workflows/build.yml).
BUILTIN_VARIANT: dict[str, str] = {
    "darwin-arm64": "metal",
}

STATE_FILE = "state.json"
PACK_MANIFEST = "MANIFEST.json"

ProgressCallback = Callable[[float, str], None]


class RuntimeUnavailable(RuntimeError):
    """A runtime pack is not installed and cannot (yet) be fetched."""


@dataclass(frozen=True)
class RuntimePack:
    variant: str
    platform_tag: str  # "<os>-<arch>", e.g. "win32-x86_64"
    builtin: bool  # compiled into the app bundle → always installed
    path: Path | None  # download location for non-builtin packs

    @property
    def installed(self) -> bool:
        if self.builtin:
            return True
        return self.path is not None and (self.path / PACK_MANIFEST).exists()

    def to_dict(self) -> dict[str, Any]:
        return {
            "variant": self.variant,
            "platform_tag": self.platform_tag,
            "builtin": self.builtin,
            "installed": self.installed,
            "path": str(self.path) if self.path else None,
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
        self._bad: dict[str, str] = self._load_state().get("bad", {})

    # --- introspection ----------------------------------------------------

    @property
    def hardware(self) -> HardwareInfo:
        return self._hardware

    @property
    def builtin(self) -> str:
        return self._builtin

    @property
    def active(self) -> RuntimeSelection | None:
        return self._active

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

    # --- 3: ensure ----------------------------------------------------------

    def ensure(self, variant: str, progress: ProgressCallback | None = None) -> RuntimePack:
        """Make ``variant`` installed, downloading it if needed.

        Downloading is not implemented until CI publishes packs; until then a
        non-builtin, not-yet-installed variant raises :class:`RuntimeUnavailable`
        with an actionable message. The signature (variant + progress callback,
        same shape as ``core.models.download_model``) is final.
        """
        pack = self.pack(variant)
        if pack.installed:
            return pack
        raise RuntimeUnavailable(
            f"runtime pack {variant!r} for {self._tag} is not installed and packs are "
            "not published yet; the built-in runtime "
            f"({self._builtin!r}) is used instead"
        )

    # --- 4: activate --------------------------------------------------------

    def activate(self, selection: RuntimeSelection | None = None) -> RuntimeSelection:
        """Put the selected pack on the import path. Idempotent. Must run before
        any native backend module is imported (they import lazily in ``load()``)."""
        if self._active is not None:
            return self._active
        sel = selection or self.select()
        pack = self.pack(sel.variant)
        if not pack.builtin and pack.path is not None:
            site = pack.path / "site-packages"
            if site.exists() and str(site) not in sys.path:
                sys.path.insert(0, str(site))
            dll_dir = pack.path / "bin"
            add_dll = getattr(os, "add_dll_directory", None)
            if add_dll is not None and dll_dir.exists():
                add_dll(str(dll_dir))
        self._active = sel
        log.info(
            "compute runtime: %s (%s; chain=%s; builtin=%s)",
            sel.variant, sel.reason, "→".join(sel.chain), self._builtin,
        )
        return sel

    # --- failure feedback -----------------------------------------------------

    def mark_bad(self, variant: str, reason: str) -> None:
        """Remember that ``variant`` failed here so future launches skip it."""
        if variant == self._builtin:
            log.error("built-in runtime %s failed (%s); nothing to fall back to", variant, reason)
            return
        self._bad[variant] = reason
        self._save_state()
        log.warning("compute runtime %s marked bad: %s", variant, reason)

    def clear_bad(self) -> None:
        self._bad = {}
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

    def status(self) -> dict[str, Any]:
        """Everything a settings UI needs to render the compute panel."""
        selection = self._active or self.select()
        return {
            "platform_tag": self._tag,
            "hardware": self._hardware.to_dict(),
            "capabilities": self._platform.capabilities.to_dict(),
            "config": self._config.model_dump(),
            "builtin": self._builtin,
            "candidates": list(self.candidates()),
            "selection": selection.to_dict(),
            "active": self._active.variant if self._active else None,
            "packs": [self.pack(v).to_dict() for v in VARIANTS],
            "bad": self.bad,
            "vram_budget_mb": self.vram_budget_mb(),
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
