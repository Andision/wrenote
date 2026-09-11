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
    HTTP_BACKENDS,
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
from ..core.openai_compat import (
    ENDPOINT_SLOTS,
    ChatCompletionsClient,
    LLMHTTPError,
    endpoint_status,
    remote_slots,
)
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
        # Which slots send text off this machine — an `openai_compatible`
        # backend pointed somewhere that isn't loopback. Empty is the normal
        # case and the one the "nothing leaves your device" line is about;
        # the client says something else when it isn't (see PreFlight).
        "remote": remote_slots(cfg),
        # Per slot that can be answered over HTTP: what its endpoint is set to,
        # and whether it is the one in use. The settings panel renders this as
        # a row alongside the downloadable models; a slot missing from here
        # cannot be pointed at a URL at all (speech recognition).
        "endpoints": {slot: endpoint_status(cfg, slot) for slot in ENDPOINT_SLOTS},
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
    backend = section.backend
    if spec.backend != backend:
        backend = spec.backend
        update["backend"] = backend
    path = await asyncio.to_thread(write_user_config, {kind: update})
    section.model = body.model  # the running config, so the next session agrees
    section.backend = backend

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


class EndpointRequest(BaseModel):
    """One slot's HTTP endpoint. Omitted fields keep their current value.

    ``api_key`` is the reason for that rule rather than a plain replace: the
    client never receives the key back (see
    :func:`~wrenote.core.openai_compat.endpoint_status`), so it cannot send it
    again, and a form save must not wipe it. Passing ``""`` explicitly clears
    it — which is how the UI's "forget the key" works.
    """

    kind: str
    base_url: str | None = None
    model: str | None = None
    api_key: str | None = None
    api_key_env: str | None = None
    timeout_s: float | None = None
    #: Switch the slot onto (or off) the HTTP backend. Off restores whichever
    #: catalogue model the slot last had — the endpoint config stays on record.
    active: bool | None = None


def _endpoint_slot(kind: str) -> str:
    slot = kind.strip().lower()
    if slot not in ENDPOINT_SLOTS:
        raise HTTPException(
            status_code=400,
            detail=(f"{kind!r} cannot be answered over HTTP; "
                    f"one of {list(ENDPOINT_SLOTS)}"),
        )
    return slot


def _client_for(cfg: Config, slot: str, catalogue: ModelCatalogue) -> ChatCompletionsClient:
    """A client built the way the backend would build it, for a test call."""
    params = resolve(cfg, slot, catalogue).params
    return ChatCompletionsClient(
        base_url=str(params.get("base_url") or ""),
        model=str(params.get("model") or ""),
        api_key=str(params.get("api_key") or ""),
        api_key_env=str(params.get("api_key_env") or ""),
        headers=params.get("headers") or {},
        extra_body=params.get("extra_body") or {},
        timeout_s=float(params.get("timeout_s") or 120.0),
    )


@router.post("/models/endpoint")
async def models_endpoint(
    body: EndpointRequest,
    request: Request,
    cfg: Config = Depends(get_config),
    catalogue: ModelCatalogue = Depends(get_catalogue),
) -> dict[str, Any]:
    """Point a slot at a model served over HTTP, persisting to the user config.

    Same "applies now / next session" split as :func:`models_select`: chat is
    held by :class:`ModelManager` and swapped here; the translator is built per
    session. Switching ``active`` off restores the slot's catalogue model
    rather than leaving it on a backend it is no longer meant to use.
    """
    slot = _endpoint_slot(body.kind)
    section = getattr(cfg, slot)

    fields = body.model_dump(exclude_none=True, exclude={"kind", "active"})
    if "base_url" in fields:
        fields["base_url"] = fields["base_url"].strip()
    for key, value in fields.items():
        setattr(section.endpoint, key, value)

    # Turning it on without an endpoint would leave the slot unable to answer,
    # and the failure would surface as a broken chat rather than a bad setting.
    turning_on = body.active is True or (body.active is None and section.backend in HTTP_BACKENDS)
    if turning_on and not section.endpoint.configured:
        raise HTTPException(status_code=400, detail="base_url is required")

    if body.active is not None:
        if body.active:
            section.backend = HTTP_BACKENDS[0]
        elif section.backend in HTTP_BACKENDS:
            # Back to a local model: whichever the slot names, or the
            # catalogue's default for it if it never named one.
            spec = catalogue.get(section.model or "") or catalogue.default_for(slot)
            if spec is None:
                raise HTTPException(
                    status_code=409,
                    detail=f"no local {slot} model to fall back to; choose one first",
                )
            section.backend, section.model = spec.backend, spec.id

    stored = {slot: {"backend": section.backend,
                     "model": section.model,
                     "endpoint": section.endpoint.model_dump()}}
    path = await asyncio.to_thread(write_user_config, stored)

    applies = "next_session"
    if slot == "chat":
        r = resolve(cfg, slot, catalogue)
        manager = request.app.state.models
        await manager.replace_chat(
            None if r.disabled else make_chat(section.backend, r.params)
        )
        applies = "now"
    return {
        "kind": slot,
        "applies": applies,
        "restart_required": False,
        "config_path": str(path),
        "endpoint": endpoint_status(cfg, slot),
        "remote": remote_slots(cfg),
    }


@router.post("/models/endpoint/test")
async def models_endpoint_test(
    body: EndpointRequest,
    cfg: Config = Depends(get_config),
    catalogue: ModelCatalogue = Depends(get_catalogue),
) -> dict[str, Any]:
    """Ask the configured endpoint one tiny question, and report what happened.

    This is the probe ``load()`` deliberately doesn't do (a health check on
    every start-up costs a round trip, and some shims serve
    ``/chat/completions`` and nothing else). Here it is worth it: the moment
    someone presses Save is exactly when a typo in a URL or a stale key should
    surface, rather than three days later mid-meeting.

    Tests what is *saved*, not what is typed — the key may only exist in the
    config — so the client saves first and tests second.
    """
    slot = _endpoint_slot(body.kind)
    if not getattr(cfg, slot).endpoint.configured:
        raise HTTPException(status_code=400, detail="base_url is required")
    client = _client_for(cfg, slot, catalogue)
    client.open()
    try:
        reply = await client.complete(
            [{"role": "user", "content": "Reply with the single word: ok"}],
            max_tokens=16,
            temperature=0.0,
            timeout_s=20.0,
        )
        return {"ok": True, "url": client.url, "reply": reply.strip()[:200]}
    except (LLMHTTPError, TimeoutError, OSError) as e:
        # A failed test is an answer, not a server error: the client renders
        # the message next to the field that caused it.
        return {"ok": False, "url": client.url, "error": f"{type(e).__name__}: {e}"}
    finally:
        await client.aclose()


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
