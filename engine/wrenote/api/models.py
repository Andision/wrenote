"""First-run model download (status + background download job).

Which files are needed comes from :mod:`wrenote.core.catalogue` — the config
names a model id, the catalogue says which files that is and where they live.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from ..core.catalogue import (
    OPTIONAL_SLOTS,
    SLOT_KIND,
    SLOTS,
    ModelCatalogue,
    resolve,
    resolve_all,
)
from ..core.config import Config, write_user_config
from ..core.jobs import JobRegistry, Phase
from ..core.models import download_model, required_models
from ..core.registry import make_chat, make_speaker
from ..deps import get_catalogue, get_config, get_jobs

log = logging.getLogger(__name__)
router = APIRouter()


@router.get("/models/status")
async def models_status(
    request: Request,
    cfg: Config = Depends(get_config),
    catalogue: ModelCatalogue = Depends(get_catalogue),
) -> dict[str, Any]:
    """Which model files are needed, which are present, and what else could be
    chosen for this machine.

    ``options`` is per kind, ranked and with one recommended — the setup wizard
    and Settings → Models both render it, the same way they render the compute
    runtime's options.
    """
    entries = required_models(cfg, catalogue)
    hw = request.app.state.runtimes.hardware
    models_dir = Path(cfg.models.dir).expanduser()
    resolved = resolve_all(cfg, catalogue)
    options = [
        catalogue.options(
            kind, hw, models_dir=models_dir,
            selected=(resolved[kind].spec.id if resolved[kind].spec else None),
        ).to_dict()
        for kind in SLOTS
        if catalogue.for_kind(SLOT_KIND[kind])
    ]
    return {
        "models": [e.status_dict() for e in entries],
        "all_present": all(e.present for e in entries),
        "options": options,
        "selected": {
            kind: (r.spec.id if r.spec else None) for kind, r in resolved.items()
        },
        # Which optional features are on. A slot that is off contributes no
        # entry above, so `all_present` — which decides whether the first-run
        # wizard appears at all — ignores what it would have downloaded.
        "features": {slot: getattr(cfg, slot).enabled for slot in OPTIONAL_SLOTS},
    }


class SelectRequest(BaseModel):
    kind: str
    model: str


@router.post("/models/select")
async def models_select(
    body: SelectRequest,
    request: Request,
    cfg: Config = Depends(get_config),
    catalogue: ModelCatalogue = Depends(get_catalogue),
) -> dict[str, Any]:
    """Choose the model for one kind, persisting it to the user config.

    How soon it applies differs by kind, and the response says which: STT and
    the translator are constructed per WebSocket session, so the next session
    picks the new model up; chat and the diarization speaker are held by
    :class:`ModelManager`, so they are swapped here and now. Neither needs a
    restart — claiming otherwise would train people to restart for nothing.
    """
    kind = body.kind.strip().lower()
    if kind not in SLOTS:
        raise HTTPException(status_code=400, detail=f"unknown kind {body.kind!r}")
    spec = catalogue.get(body.model)
    if spec is None or spec.kind != SLOT_KIND[kind]:
        raise HTTPException(
            status_code=404, detail=f"no {kind} model {body.model!r} in the catalogue"
        )
    section = getattr(cfg, kind)
    if section.params.get("model_path"):
        raise HTTPException(
            status_code=409,
            detail=(f"{kind}.params.model_path pins an explicit file; "
                    "remove it to choose from the catalogue"),
        )

    # A model names its backend; choosing a model on another backend (a
    # streaming recogniser instead of Whisper for the live slot) switches
    # the backend with it. Each backend ignores the other's tuning keys.
    update: dict[str, Any] = {"model": body.model}
    if spec.backend != section.backend:
        update["backend"] = spec.backend
    path = await asyncio.to_thread(write_user_config, {kind: update})
    section.model = body.model  # the running config, so the next session agrees
    section.backend = spec.backend

    applies = "next_session"
    if kind in ("chat", "speaker"):
        params = resolve(cfg, kind, catalogue).params
        manager = request.app.state.models
        if kind == "chat":
            await manager.replace_chat(make_chat(section.backend, params))
        else:
            await manager.replace_diarize_speaker(make_speaker(section.backend, params))
        applies = "now"
    return {
        "kind": kind,
        "model": body.model,
        "applies": applies,
        "restart_required": False,
        "config_path": str(path),
    }


class FeaturesRequest(BaseModel):
    """The optional features to switch on or off; omitted slots are left alone."""

    translator: bool | None = None
    chat: bool | None = None
    speaker: bool | None = None


@router.post("/models/features")
async def models_features(
    body: FeaturesRequest,
    request: Request,
    cfg: Config = Depends(get_config),
    catalogue: ModelCatalogue = Depends(get_catalogue),
) -> dict[str, Any]:
    """Switch optional features on or off, persisting to the user config.

    Switching one on does not download anything: the caller follows with
    ``POST /models/download``, which now sees the slot in ``required_models``.
    Switching one off keeps the files — deleting a 2.5 GB model because a
    toggle moved is not a thing to do silently — but drops the running
    backend, so the memory goes back.
    """
    changed = {
        slot: value
        for slot, value in body.model_dump(exclude_none=True).items()
        if slot in OPTIONAL_SLOTS
    }
    if not changed:
        return {"features": {s: getattr(cfg, s).enabled for s in OPTIONAL_SLOTS}}

    path = await asyncio.to_thread(
        write_user_config, {slot: {"enabled": value} for slot, value in changed.items()}
    )
    for slot, value in changed.items():
        getattr(cfg, slot).enabled = value

    # chat and speaker are held by ModelManager for the process's lifetime, so
    # they are swapped here; the translator is built per session and needs no
    # such handling.
    manager = request.app.state.models
    if "chat" in changed:
        r = resolve(cfg, "chat", catalogue)
        await manager.replace_chat(
            None if r.disabled else make_chat(cfg.chat.backend, r.params)
        )
    if "speaker" in changed:
        r = resolve(cfg, "speaker", catalogue)
        await manager.replace_diarize_speaker(
            None if r.disabled or cfg.speaker.backend in (None, "", "disabled")
            else make_speaker(cfg.speaker.backend, r.params)
        )
    return {
        "features": {s: getattr(cfg, s).enabled for s in OPTIONAL_SLOTS},
        "config_path": str(path),
    }


@router.delete("/models/{model_id}")
async def models_delete(
    model_id: str,
    request: Request,
    cfg: Config = Depends(get_config),
    catalogue: ModelCatalogue = Depends(get_catalogue),
) -> dict[str, Any]:
    """Delete a catalogue model's files from ``models.dir``.

    For testing what the app does when a model is absent — the first-run
    wizard, the download progress, a slot with nothing to run — which
    otherwise means finding the files by hand. Recoverable by definition:
    the catalogue knows where every file came from, so the next download
    fetches it again.

    A model that is currently loaded is dropped from the manager first;
    otherwise Windows would refuse to unlink the file under it.
    """
    spec = catalogue.get(model_id)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"no model {model_id!r} in the catalogue")

    resolved = resolve_all(cfg, catalogue)
    in_use = [slot for slot, r in resolved.items() if r.spec and r.spec.id == model_id]
    manager = request.app.state.models
    if "chat" in in_use:
        await manager.replace_chat(None)
    if "speaker" in in_use:
        await manager.replace_diarize_speaker(None)

    models_dir = Path(cfg.models.dir).expanduser()
    removed: list[str] = []
    failed: list[dict[str, str]] = []
    freed = 0
    for f in spec.files:
        target = f.local_path(models_dir)
        for path in (target, target.with_suffix(target.suffix + ".partial")):
            if not path.exists():
                continue
            try:
                size = path.stat().st_size
                await asyncio.to_thread(path.unlink)
            except OSError as e:
                failed.append({"filename": path.name, "error": str(e)})
                continue
            removed.append(path.name)
            freed += size
    return {
        "model": model_id,
        "removed": removed,
        "failed": failed,
        "freed_mb": freed >> 20,
        # Which slots now have no files. The client re-reads status anyway;
        # this is what makes the response readable on its own.
        "slots": in_use,
    }


@router.post("/models/download")
async def models_download(
    cfg: Config = Depends(get_config),
    jobs: JobRegistry = Depends(get_jobs),
    catalogue: ModelCatalogue = Depends(get_catalogue),
) -> dict[str, Any]:
    """Start downloading any missing models as a background job. Progress streams
    over ``/v1/jobs/{job_id}/stream`` (one weighted phase per model)."""
    missing = [e for e in required_models(cfg, catalogue) if not e.present]
    if not missing:
        return {"job_id": None, "all_present": True}

    total = sum(e.approx_size for e in missing) or 1
    phases = [Phase(name=e.filename, weight=e.approx_size / total) for e in missing]
    job = jobs.create(kind="model_download", phases=phases)

    async def runner() -> None:
        try:
            for idx, entry in enumerate(missing):
                jobs.advance(
                    job.id, phase_idx=idx, phase_inner=0.0,
                    log_line=f"Downloading {entry.filename}",
                )

                def _progress(frac: float, status: str) -> None:
                    jobs.advance(job.id, phase_inner=frac, log_line=status)

                await download_model(entry, _progress)
            jobs.complete(job.id, result={"downloaded": [e.filename for e in missing]})
        except Exception as ex:  # surfaced to the client via the job registry
            log.exception("model download failed")
            jobs.fail(job.id, str(ex))

    task = asyncio.create_task(runner())
    # Hold a reference: the event loop keeps only a weak one, so an
    # unreferenced task can be collected mid-flight (RUF006).
    _background.add(task)
    task.add_done_callback(_background.discard)
    return {"job_id": job.id, "all_present": False}


_background: set[asyncio.Task[None]] = set()
