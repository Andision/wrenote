"""The model catalogue: which models exist, and which one a config means.

Three things used to be tangled together. What models exist was a dict keyed by
filename in :mod:`wrenote.core.models`; which one to use was a *path* in the
config that had to spell that same filename; how to run one is the backend.
The first is now data (``engine/models.yaml``), this module resolves the second,
and the third is unchanged — ``core/registry.py`` was already a factory.

Resolution order for a kind (``stt`` / ``translator`` / ``chat`` / ``speaker``):

1. ``params.model_path`` — an explicit path wins. This is the escape hatch for a
   model we don't ship a catalogue entry for, and it keeps every existing
   ``~/.wrenote/config.yaml`` working untouched. Nothing can be downloaded for
   it, because nothing knows where it came from.
2. ``model: <id>`` — an entry in the catalogue.
3. the catalogue's ``defaults`` for that kind, if its backend matches the
   configured one.

Users add or override entries in ``~/.wrenote/models.yaml``; ids merge, so the
same id replaces a shipped entry and a new id extends the list.
"""
from __future__ import annotations

import logging
import os
import sys
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from ..platform import HardwareInfo

if TYPE_CHECKING:
    from .config import Config

log = logging.getLogger(__name__)

SCHEMA = 1
KINDS = ("stt", "translator", "chat", "speaker")
# Config sections that hold a model. Two of them are speech recognition:
# what a live session hears (`stt`, may be a streaming model) and what a
# whole recording goes through afterwards (`stt_offline`, Whisper). Models
# are catalogued by kind; a slot names which kind it takes.
SLOTS = ("stt", "stt_offline", "translator", "chat", "speaker")
SLOT_KIND = {"stt": "stt", "stt_offline": "stt", "translator": "translator",
             "chat": "chat", "speaker": "speaker"}
TIERS = ("small", "medium", "large")

USER_CATALOGUE = Path.home() / ".wrenote" / "models.yaml"


def bundled_catalogue_path() -> Path:
    """The shipped ``models.yaml``. Frozen builds keep it beside ``config.yaml``
    under ``sys._MEIPASS``; in a checkout it sits next to the package."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", ".")) / "models.yaml"
    return Path(__file__).resolve().parent.parent.parent / "models.yaml"


def _hf_base() -> str:
    """HuggingFace base URL; override with HF_ENDPOINT for a mirror."""
    return os.environ.get("HF_ENDPOINT", "https://huggingface.co").rstrip("/")


@dataclass(frozen=True)
class ModelFile:
    """One downloadable file, and the backend argument it is passed as."""

    param: str  # e.g. "model_path"
    filename: str
    repo: str  # HuggingFace repo id
    path: str  # path within the repo
    size: int
    sha256: str  # "" when upstream exposes no content hash

    @property
    def url(self) -> str:
        return f"{_hf_base()}/{self.repo}/resolve/main/{self.path}"

    def local_path(self, models_dir: Path) -> Path:
        return models_dir / self.filename


@dataclass(frozen=True)
class ModelSpec:
    id: str
    kind: str
    backend: str
    tier: str
    name: str
    files: tuple[ModelFile, ...]
    note_code: str = ""  # what this choice means; the client renders it
    requires: dict[str, int] = field(default_factory=dict)
    params: dict[str, Any] = field(default_factory=dict)

    @property
    def size(self) -> int:
        return sum(f.size for f in self.files)

    def backend_params(self, models_dir: Path) -> dict[str, Any]:
        """The model's contribution to its backend's constructor arguments."""
        args: dict[str, Any] = dict(self.params)
        for f in self.files:
            args[f.param] = str(f.local_path(models_dir))
        return args

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "backend": self.backend,
            "tier": self.tier,
            "name": self.name,
            "note_code": self.note_code,
            "size": self.size,
            "requires": dict(self.requires),
        }


def _parse_file(row: dict[str, Any]) -> ModelFile:
    return ModelFile(
        param=str(row.get("param") or "model_path"),
        filename=str(row["filename"]),
        repo=str(row["repo"]),
        path=str(row.get("path") or row["filename"]),
        size=int(row.get("size") or 0),
        sha256=str(row.get("sha256") or "").lower(),
    )


def _parse_model(row: dict[str, Any]) -> ModelSpec:
    kind = str(row["kind"])
    if kind not in KINDS:
        raise ValueError(f"model {row.get('id')!r}: unknown kind {kind!r}")
    tier = str(row.get("tier") or "medium")
    if tier not in TIERS:
        raise ValueError(f"model {row.get('id')!r}: unknown tier {tier!r}")
    files = tuple(_parse_file(f) for f in row.get("files") or [])
    if not files:
        raise ValueError(f"model {row.get('id')!r}: no files")
    return ModelSpec(
        id=str(row["id"]),
        kind=kind,
        backend=str(row["backend"]),
        tier=tier,
        name=str(row.get("name") or row["id"]),
        files=files,
        note_code=str(row.get("note_code") or ""),
        requires={k: int(v) for k, v in (row.get("requires") or {}).items()},
        params=dict(row.get("params") or {}),
    )


class ModelCatalogue:
    """Everything the app knows how to download and run."""

    def __init__(self, models: list[ModelSpec], defaults: dict[str, str]) -> None:
        self._by_id = {m.id: m for m in models}
        self._order = [m.id for m in models]
        self._defaults = defaults

    # --- loading ----------------------------------------------------------

    @classmethod
    def load(cls, *, bundled: Path | None = None, user: Path | None = None) -> ModelCatalogue:
        """Bundled catalogue, with ``~/.wrenote/models.yaml`` merged over it.

        A broken user file is logged and skipped rather than taking the app
        down: a typo in an optional override should not stop the engine from
        starting with the models it already has.
        """
        models: dict[str, ModelSpec] = {}
        order: list[str] = []
        defaults: dict[str, str] = {}
        for path, required in ((bundled or bundled_catalogue_path(), True),
                               (user or USER_CATALOGUE, False)):
            doc = cls._read(path, required=required)
            if doc is None:
                continue
            for key, value in (doc.get("defaults") or {}).items():
                defaults[str(key)] = str(value)
            for row in doc.get("models") or []:
                try:
                    spec = _parse_model(row)
                except (KeyError, ValueError, TypeError) as e:
                    log.warning("%s: skipping a model entry (%s)", path, e)
                    continue
                if spec.id not in models:
                    order.append(spec.id)
                models[spec.id] = spec
        return cls([models[i] for i in order], defaults)

    @staticmethod
    def _read(path: Path, *, required: bool) -> dict[str, Any] | None:
        try:
            with open(path, encoding="utf-8") as f:
                doc = yaml.safe_load(f) or {}
        except FileNotFoundError:
            if required:
                log.error("model catalogue missing at %s; no model is downloadable", path)
            return None
        except (OSError, yaml.YAMLError) as e:
            log.warning("model catalogue %s is unreadable (%s); ignoring", path, e)
            return None
        if not isinstance(doc, dict):
            log.warning("model catalogue %s is not a mapping; ignoring", path)
            return None
        schema = int(doc.get("schema") or 0)
        if schema > SCHEMA:
            log.warning(
                "model catalogue %s declares schema %d, this build understands %d; "
                "entries using newer fields may be skipped", path, schema, SCHEMA,
            )
        return doc

    # --- lookup -----------------------------------------------------------

    def __len__(self) -> int:
        return len(self._by_id)

    def __iter__(self) -> Iterator[ModelSpec]:
        """Every entry, in catalogue order."""
        return (self._by_id[i] for i in self._order)

    def get(self, model_id: str) -> ModelSpec | None:
        return self._by_id.get(model_id)

    def for_kind(self, kind: str) -> list[ModelSpec]:
        return [self._by_id[i] for i in self._order if self._by_id[i].kind == kind]

    def options(
        self,
        slot: str,
        hw: HardwareInfo,
        *,
        models_dir: Path,
        selected: str | None = None,
    ) -> KindOptions:
        """Every model for ``slot``, smallest first, with one recommended.

        The recommendation is the largest tier this machine is targeted at that
        it can actually run. Models that don't fit stay in the list carrying
        their blocker: an option that silently vanishes reads as a missing
        feature, not as a considered exclusion.
        """
        target, reason, params = _target_tier(hw)
        budget = _memory_budget_mb(hw)
        kind = SLOT_KIND.get(slot, slot)
        specs = sorted(self.for_kind(kind), key=lambda m: TIERS.index(m.tier))
        # The live slot on a machine with no accelerator: Whisper can't keep
        # up in real time on a CPU alone (whisper-small ran at half real time
        # on four cores), so the catalogue's streaming pick is what to
        # recommend there. Whole recordings (stt_offline) can take their time
        # and stay with Whisper.
        cpu_pick = self._defaults.get("stt_cpu") if slot == "stt" else None
        if cpu_pick and cpu_pick in self._by_id and not _accelerated(hw):
            spec = self._by_id[cpu_pick]
            if spec.kind == kind:
                specs = [m for m in specs if m.id != spec.id]
                specs.insert(0, spec)  # listed first: it is the one for this machine
                target, reason, params = spec.tier, "cpu_streaming", {"cpus": str(hw.cpu_count)}

        def fits(spec: ModelSpec) -> tuple[bool, str, dict[str, str]]:
            need = spec.requires.get("ram_mb", 0)
            if budget and need and budget < need:
                return False, "needs_ram", {"need": f"{need // 1024} GB"}
            return True, "", {}

        usable = [m for m in specs if fits(m)[0]]
        at_or_below = [m for m in usable if TIERS.index(m.tier) <= TIERS.index(target)]
        if reason == "cpu_streaming" and usable and usable[0].id == cpu_pick:
            pick = usable[0]
        elif at_or_below:
            pick = at_or_below[-1]  # the largest we aim at
        elif usable:
            pick = usable[0]  # the catalogue offers nothing that small
        elif specs:
            # Nothing fits. Recommend the smallest anyway, with its blocker
            # showing: the user still has to choose something to use the app,
            # and `requires` is a guideline, not a hard gate.
            pick = specs[0]
        else:
            pick = None

        rows: list[ModelOption] = []
        for spec in specs:
            ok, code, blocked_params = fits(spec)
            installed = all(
                f.local_path(models_dir).exists() for f in spec.files
            )
            rows.append(ModelOption(
                id=spec.id,
                kind=spec.kind,
                tier=spec.tier,
                name=spec.name,
                note_code=spec.note_code,
                size_mb=max(1, spec.size >> 20),
                installed=installed,
                fits=ok,
                recommended=pick is not None and spec.id == pick.id,
                selected=selected == spec.id,
                blocked_code=code,
                blocked_params=blocked_params,
            ))
        return KindOptions(kind=slot, reason_code=reason, reason_params=params, options=rows)

    def default_for(self, slot: str) -> ModelSpec | None:
        """The catalogue's pick for a slot: its own default, else its kind's."""
        kind = SLOT_KIND.get(slot, slot)
        for key in (slot, kind):
            chosen = self._defaults.get(key)
            if chosen and chosen in self._by_id:
                return self._by_id[chosen]
        options = self.for_kind(kind)
        return options[0] if options else None


# ---------- matching models to the machine ----------

#: Tier we aim at, by total RAM. Not per model: a live session keeps whisper
#: *and* the translator resident, and the chat model loads on top of them later,
#: so what matters is the machine rather than any one file's size.
_TIER_BY_RAM_MB: tuple[tuple[int, str], ...] = ((16384, "large"), (8192, "medium"), (0, "small"))

#: A discrete GPU with at least this much VRAM lifts the target one tier: the
#: weights move off system RAM and decode gets fast enough for the bigger model
#: to stay real-time.
_GPU_LIFT_VRAM_MB = 6144


def _memory_budget_mb(hw: HardwareInfo) -> int:
    """Memory a model can actually occupy: system RAM plus discrete VRAM.

    With ``n_gpu_layers=-1`` the weights live on the GPU, so a 8 GB laptop with
    a 12 GB card runs models an 8 GB check would reject. Unified memory is *not*
    added — an iGPU's VRAM is system RAM, and counting it twice over-promises.
    """
    vram = sum(g.vram_mb or 0 for g in hw.gpus if not g.unified_memory)
    return (hw.ram_mb or 0) + vram


def _accelerated(hw: HardwareInfo) -> bool:
    """Whether any runtime variant beyond the CPU could run here (Metal, CUDA,
    Vulkan …). A candidate list, not an installed pack: a machine that could
    install one is treated as accelerated, and the wizard offers the pack."""
    return any(a != "cpu" for a in hw.accelerators)


def _target_tier(hw: HardwareInfo) -> tuple[str, str, dict[str, str]]:
    """``(tier, reason_code, params)`` — the tier to aim at, and why.

    The reason is carried as a code, not a sentence: the setup wizard shows it
    to a person and the client owns the wording (see clients/web/src/i18n/).
    """
    ram = hw.ram_mb or 0
    tier = next(t for floor, t in _TIER_BY_RAM_MB if ram >= floor)
    params = {"ram": f"{ram // 1024} GB" if ram else "?"}

    gpu = max(
        (g for g in hw.gpus if not g.unified_memory and (g.vram_mb or 0) >= _GPU_LIFT_VRAM_MB),
        key=lambda g: g.vram_mb or 0,
        default=None,
    )
    if gpu is not None and tier != "large":
        lifted = TIERS[min(TIERS.index(tier) + 1, len(TIERS) - 1)]
        return lifted, "gpu_headroom", {**params, "gpu": gpu.name}
    if tier == "large":
        return tier, "ample_ram", params
    if tier == "medium":
        return tier, "moderate_ram", params
    return tier, "limited_ram", params


@dataclass(frozen=True)
class ModelOption:
    """One model a person could pick for a kind, with the reasoning shown to them.

    Same shape as :class:`wrenote.core.runtimes.RuntimeOption` on purpose: the
    setup wizard renders both, and a second vocabulary for "here are the
    choices, here is the one we suggest" would be one to keep in sync forever.
    """

    id: str
    kind: str
    tier: str
    name: str
    note_code: str  # what this model is for, from the catalogue
    size_mb: int
    installed: bool
    fits: bool  # the machine meets `requires`
    recommended: bool
    selected: bool
    blocked_code: str = ""  # set when `fits` is False
    blocked_params: dict[str, str] = field(default_factory=dict)

    @property
    def download_mb(self) -> int | None:
        return None if self.installed else self.size_mb

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "download_mb": self.download_mb}


@dataclass(frozen=True)
class KindOptions:
    """The choices for one kind, and the hardware verdict behind them."""

    kind: str
    reason_code: str  # why this tier was targeted
    reason_params: dict[str, str]
    options: list[ModelOption]

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "reason_code": self.reason_code,
            "reason_params": dict(self.reason_params),
            "options": [o.to_dict() for o in self.options],
        }


@dataclass(frozen=True)
class ResolvedModel:
    """What a config actually selected for one kind."""

    kind: str
    backend: str
    params: dict[str, Any]  # ready to hand to the registry factory
    spec: ModelSpec | None  # None = a custom path; nothing to download
    reason: str  # "path" | "id" | "default" | "backend-needs-no-model"

    @property
    def downloadable(self) -> bool:
        return self.spec is not None


#: Backends that need no model file at all — resolution stops early for them.
_NO_MODEL = ("mock", "disabled", "")


def resolve(cfg: Config, kind: str, catalogue: ModelCatalogue) -> ResolvedModel:
    """Work out the backend arguments for slot ``kind`` (see the module docstring)."""
    section = getattr(cfg, kind)
    backend = section.backend or ""
    params = dict(section.params or {})
    if backend in _NO_MODEL:
        return ResolvedModel(kind, backend, params, None, "backend-needs-no-model")

    models_dir = Path(cfg.models.dir).expanduser()

    # 1. An explicit path wins, and keeps pre-catalogue configs working.
    if params.get("model_path"):
        return ResolvedModel(kind, backend, params, None, "path")

    # 2. A named catalogue entry.
    chosen = getattr(section, "model", None)
    spec = catalogue.get(chosen) if chosen else None
    if chosen and spec is None:
        log.warning("%s.model=%r is not in the catalogue; falling back", kind, chosen)
    if spec is not None and spec.backend != backend:
        log.warning(
            "%s.model=%r runs on the %r backend but %r is configured; ignoring the model",
            kind, chosen, spec.backend, backend,
        )
        spec = None

    # 3. The catalogue's default, when it fits the configured backend.
    reason = "id"
    if spec is None:
        fallback = catalogue.default_for(kind)
        if fallback is not None and fallback.backend == backend:
            spec, reason = fallback, "default"

    if spec is None:
        log.warning("no catalogue model for %s backend %r; it must be given a model_path",
                    kind, backend)
        return ResolvedModel(kind, backend, params, None, "path")

    # Config params win over the catalogue's: the entry describes the model,
    # the user's config tunes it (n_ctx, temperature, …).
    merged = {**spec.backend_params(models_dir), **params}
    return ResolvedModel(kind, backend, merged, spec, reason)


def resolve_all(cfg: Config, catalogue: ModelCatalogue) -> dict[str, ResolvedModel]:
    return {slot: resolve(cfg, slot, catalogue) for slot in SLOTS}
