"""WebSocket session endpoint.

Per design.v1.1 §5. The single ``/ws`` endpoint carries binary PCM frames +
JSON control messages (``start``/``stop``/``pause``/``resume``) client→server,
and JSON events (``ready``/``partial``/``final``/``translation``/``error``)
server→client. A fresh :class:`Pipeline` is built per connection.

Carries its own origin + token gate (the HTTP loopback-auth middleware does not
cover WebSocket upgrades), so it imports those from :mod:`wrenote.auth`.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status

from .api._common import SAFE_SESSION_ID
from .auth import AUTH_COOKIE, AUTH_TOKEN, origin_allowed
from .core import glossary, screenrec
from .core import refine as refine_mod
from .core.catalogue import resolve
from .core.config import Config
from .core.events import (
    ErrorEvent,
    ReadyEvent,
    TranscriptEvent,
    TranslationEvent,
    VADEvent,
)
from .core.pipeline import Pipeline, SessionParams
from .core.recording import WavWriter, resolve_recording_path
from .core.registry import make_speaker, make_stt, make_translator, make_vad
from .core.store import Store
from .core.syscap import SystemAudioMixer

log = logging.getLogger(__name__)
router = APIRouter()


async def _send_event(ws: WebSocket, event: Any) -> None:
    """Send a Pydantic event over the WebSocket as JSON."""
    await ws.send_text(event.model_dump_json())


async def _send_error(
    ws: WebSocket, code: str, msg: str, recoverable: bool = True
) -> None:
    try:
        await _send_event(ws, ErrorEvent(code=code, msg=msg, recoverable=recoverable))
    except Exception:
        log.exception("Failed to send error to client")


async def _finish_session(
    state: Any, session_id: str, *, refine: bool, recordings_dir: Path
) -> None:
    """Close out a session's ``recording`` status; start the refine pass if asked."""
    store: Store | None = getattr(state, "store", None)
    if store is None:
        return
    try:
        await store.set_session_status(session_id, "ready")
    except Exception:
        log.exception("could not mark session %s ready", session_id)
        return
    if not refine:
        return
    try:
        session = await store.get_session(session_id)
        if session is None:
            return
        job_id = await refine_mod.launch(
            session=session,
            cfg=state.config,
            catalogue=state.catalogue,
            store=store,
            registry=state.jobs,
            recordings_dir=recordings_dir,
        )
        log.info("refine job %s started for session %s", job_id, session_id)
    except refine_mod.RefineError as e:
        # Not an error the user did anything about (no whisper backend, say):
        # the live transcript simply stands.
        log.info("no refine pass for session %s: %s", session_id, e.code)
    except Exception:
        log.exception("could not start refine pass for session %s", session_id)


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    origin = ws.headers.get("origin")
    if not origin_allowed(origin):
        log.warning("Rejecting WS connection from origin=%r", origin)
        await ws.close(code=status.WS_1008_POLICY_VIOLATION, reason="origin not allowed")
        return

    if AUTH_TOKEN:
        provided = ws.cookies.get(AUTH_COOKIE) or ws.query_params.get("token")
        if provided != AUTH_TOKEN:
            log.warning("Rejecting WS connection: missing/invalid token")
            await ws.close(code=status.WS_1008_POLICY_VIOLATION, reason="unauthorized")
            return

    await ws.accept()
    log.info("WS connected (client=%s, origin=%s)", ws.client, origin)

    cfg: Config = ws.app.state.config
    recordings_dir = Path(cfg.data.recordings_dir)
    pipeline: Pipeline | None = None
    pump_task: asyncio.Task[None] | None = None
    mixer: SystemAudioMixer | None = None
    recorder: screenrec.ScreenRecorder | None = None
    screen_video_path: Path | None = None
    wav_writer: WavWriter | None = None
    # Set in the start-config parse; finally needs them in scope.
    session_id: str | None = None
    max_ended_at: float = 0.0
    session_row_exists = False
    refine_after_stop = False
    wav_bytes = 0

    try:
        # First message must be 'start'
        try:
            first_text = await ws.receive_text()
        except WebSocketDisconnect:
            return

        try:
            first = json.loads(first_text)
        except json.JSONDecodeError as e:
            await _send_error(ws, "BAD_CONFIG", f"Invalid JSON: {e}", recoverable=False)
            return

        if first.get("type") != "start":
            await _send_error(
                ws, "BAD_CONFIG",
                f"First message must be 'start', got {first.get('type')!r}",
                recoverable=False,
            )
            return

        session_cfg = first.get("config") or {}
        # Session id is client-generated (frontend uses it as primary key in
        # localStorage). Fall back to a server-side UUID if absent. Sanitized
        # so it can be used as a filename for the per-session WAV.
        raw_sid = str(session_cfg.get("session_id") or uuid.uuid4())
        session_id = raw_sid if SAFE_SESSION_ID.match(raw_sid) else uuid.uuid4().hex
        src_lang = session_cfg.get("src", cfg.session.default_src_lang)
        tgt_lang = session_cfg.get("tgt", cfg.session.default_tgt_lang)
        capture_system = bool(session_cfg.get("capture_system"))
        capture_screen = bool(session_cfg.get("capture_screen"))
        # Optional chosen target {type: "window"|"display", id, title}. None =
        # legacy full-screen.
        capture_target = session_cfg.get("capture_target") or None
        min_silence_ms = int(session_cfg.get("min_silence_ms", 800))
        max_segment_ms = int(session_cfg.get("max_segment_ms", 25000))
        partial_interval_ms = int(session_cfg.get("partial_interval_ms", 800))
        partial_min_audio_ms = int(session_cfg.get("partial_min_audio_ms", 500))
        translate_partials = bool(session_cfg.get("translate_partials", True))
        translate_enabled = bool(session_cfg.get("translate_enabled", True))
        extended_silence_factor = float(session_cfg.get("extended_silence_factor", 2.25))
        speaker_enabled = bool(session_cfg.get("speaker_enabled", True))
        speaker_threshold = float(session_cfg.get("speaker_threshold", 0.65))
        speaker_min_audio_ms = int(session_cfg.get("speaker_min_audio_ms", 1000))
        stt_context_chars = int(session_cfg.get("stt_context_chars", 200))
        translate_context_segments = int(session_cfg.get("translate_context_segments", 1))
        refine_after_stop = bool(
            session_cfg.get("refine_after_stop", cfg.session.refine_after_stop)
        )

        # Build backends per connection (P1-a; share via app.state in a later pass)
        try:
            catalogue = ws.app.state.catalogue
            stt = make_stt(cfg.stt.backend, resolve(cfg, "stt", catalogue).params)
            vad = make_vad(cfg.vad.backend, cfg.vad.params)  # no model file
            translator = make_translator(
                cfg.translator.backend, resolve(cfg, "translator", catalogue).params
            )
            speaker = None
            if speaker_enabled and cfg.speaker.backend not in (None, "", "disabled"):
                speaker = make_speaker(cfg.speaker.backend, resolve(cfg, "speaker", catalogue).params)
        except ValueError as e:
            await _send_error(ws, "BAD_CONFIG", str(e), recoverable=False)
            return

        # Custom-vocabulary glossary → bias STT + pin translations for this session.
        try:
            entries = await ws.app.state.store.list_glossary()
            glossary.apply_to_backends(entries, stt=stt, translator=translator)
        except Exception:
            log.exception("could not apply glossary; continuing without it")

        pipeline = Pipeline(
            stt=stt,
            vad=vad,
            translator=translator,
            speaker=speaker,
            params=SessionParams(
                src_lang=src_lang,
                tgt_lang=tgt_lang,
                min_silence_ms=min_silence_ms,
                max_segment_ms=max_segment_ms,
                partial_interval_ms=partial_interval_ms,
                partial_min_audio_ms=partial_min_audio_ms,
                translate_partials=translate_partials,
                translate_enabled=translate_enabled,
                extended_silence_factor=extended_silence_factor,
                speaker_threshold=speaker_threshold,
                speaker_min_audio_ms=speaker_min_audio_ms,
                speaker_enabled=speaker_enabled,
                stt_context_chars=stt_context_chars,
                translate_context_segments=translate_context_segments,
            ),
        )

        try:
            await pipeline.start()
        except FileNotFoundError as e:
            await _send_error(ws, "MODEL_NOT_FOUND", str(e), recoverable=False)
            return
        except Exception as e:
            log.exception("Pipeline start failed")
            await _send_error(ws, "MODEL_LOAD_FAILED", f"{type(e).__name__}: {e}", recoverable=False)
            return

        # System-audio capture (meeting recording): mix the system output into
        # the mic stream. Falls back to mic-only if the helper/permission isn't
        # available, so recording still works.
        if capture_system:
            mixer = SystemAudioMixer()
            if not await mixer.start():
                mixer = None

        # Optional screen/window recording → muxed with the session audio into an
        # MP4 on stop. `capture_target` picks a window/display; None = full screen.
        # Falls back gracefully if unavailable / no permission.
        if capture_screen and session_id:
            recorder = screenrec.ScreenRecorder()
            screen_video_path = resolve_recording_path(
                session_id, recordings_dir=recordings_dir
            ).with_suffix(".screen.mp4")
            if not await recorder.start(screen_video_path, target=capture_target):
                recorder = None
                screen_video_path = None

        # Open the raw-audio WAV file for this session. Failures are
        # non-fatal: we log and continue without recording (translation
        # still works). The writer streams to disk; memory cost is O(1).
        try:
            wav_writer = WavWriter(session_id, recordings_dir=recordings_dir)
        except Exception:
            log.exception("Failed to open WAV writer for session %s — continuing without recording", session_id)
            wav_writer = None

        # Create / refresh the SQLite session row up-front so a crash
        # mid-recording still leaves *something* discoverable.
        store: Store = ws.app.state.store
        session_title = str(session_cfg.get("title") or "Untitled session")
        created_at = str(
            session_cfg.get("created_at")
            or datetime.now(UTC).isoformat()
        )
        try:
            await store.upsert_session(
                session_id=session_id,
                title=session_title,
                created_at=created_at,
                src_lang=src_lang,
                tgt_lang=tgt_lang,
                status="recording",
            )
            session_row_exists = True
        except Exception:
            log.exception("upsert_session failed for %s", session_id)

        # Send ready event
        await _send_event(
            ws,
            ReadyEvent(
                stt=stt.info,
                vad=vad.info,
                translator=translator.info,
                speaker=speaker.info if speaker is not None else None,
            ),
        )

        # Pump pipeline events → WebSocket + persist to SQLite as they fly by.
        # Segment ordinals come from a local counter; SQLite would do this
        # too but we want a stable insertion order regardless of clock skew.
        segment_ord: dict[str, int] = {}
        max_ended_at: float = 0.0

        async def _persist_event(event: Any) -> None:
            nonlocal max_ended_at
            try:
                if isinstance(event, TranscriptEvent):
                    sid = event.segment_id
                    if sid not in segment_ord:
                        segment_ord[sid] = len(segment_ord)
                    if event.t1 is not None and event.t1 > max_ended_at:
                        max_ended_at = event.t1
                    # Only the *orig* half of the row — preserves any
                    # translation already persisted for this segment.
                    await store.upsert_segment_orig(
                        session_id=session_id,
                        segment_id=sid,
                        ord_=segment_ord[sid],
                        started_at=event.t0 or 0.0,
                        ended_at=event.t1 or 0.0,
                        orig_text=event.text or "",
                        orig_status=event.type,
                        orig_lang=event.lang,
                        speaker=event.speaker,
                    )
                elif isinstance(event, TranslationEvent):
                    sid = event.segment_id
                    if sid not in segment_ord:
                        # Translation may arrive for a segment we haven't
                        # seen a TranscriptEvent for yet — preserve order.
                        segment_ord[sid] = len(segment_ord)
                    status = (
                        "skipped" if event.skipped
                        else "partial" if event.partial
                        else "final"
                    )
                    # Only the *trans* half — never touches orig_text /
                    # started_at / ended_at written by the transcript path.
                    await store.upsert_segment_trans(
                        session_id=session_id,
                        segment_id=sid,
                        ord_=segment_ord[sid],
                        trans_text=event.text or "",
                        trans_status=status,
                        trans_lang=event.tgt_lang,
                        speaker=event.speaker,
                    )
                elif isinstance(event, VADEvent) and event.type == "speech_end":
                    if event.ts > max_ended_at:
                        max_ended_at = event.ts
            except Exception:
                log.exception("persist failed for %s", type(event).__name__)

        async def event_pump() -> None:
            try:
                async for event in pipeline.client_event_stream():
                    # Persist before sending so a slow DB doesn't drop events,
                    # and so the client can immediately re-fetch from /sessions.
                    await _persist_event(event)
                    await _send_event(ws, event)
            except (WebSocketDisconnect, asyncio.CancelledError):
                pass
            except Exception:
                log.exception("event_pump crashed")

        pump_task = asyncio.create_task(event_pump(), name="ws-event-pump")

        # Main receive loop
        while True:
            try:
                msg = await ws.receive()
            except WebSocketDisconnect:
                break

            if msg.get("type") == "websocket.disconnect":
                break

            payload_bytes = msg.get("bytes")
            payload_text = msg.get("text")

            if payload_bytes:
                # P1 audio contract: 16kHz mono int16 PCM. Tee to both the
                # live pipeline and the per-session WAV file. Paused chunks
                # never arrive here (frontend gates them) so the WAV
                # naturally excludes silence-from-pause.
                if mixer is not None:
                    payload_bytes = await mixer.mix(payload_bytes)
                await pipeline.feed_audio(payload_bytes)
                if wav_writer is not None:
                    wav_writer.append(payload_bytes)
                continue

            if not payload_text:
                continue

            try:
                data = json.loads(payload_text)
            except json.JSONDecodeError:
                await _send_error(ws, "BAD_CONFIG", "Invalid JSON in control message")
                continue

            msg_type = data.get("type")
            if msg_type == "stop":
                log.info("WS received 'stop' — flushing pipeline before close")
                # Graceful drain: flush any in-flight segment, wait for
                # STT/translation to complete, give pump a moment to send.
                if pipeline is not None:
                    try:
                        await pipeline.flush()
                    except Exception:
                        log.exception("flush failed during stop")
                # Brief pause so the pump can ship the final TranslationEvent.
                await asyncio.sleep(0.5)
                break
            elif msg_type == "pause":
                # Frontend already stopped feeding PCM. Tell the pipeline to
                # flush any in-flight VAD segment now so the user sees their
                # pre-pause speech finalized instead of left hanging as a partial.
                log.info("WS received 'pause'")
                if pipeline is not None:
                    try:
                        await pipeline.close_open_segment()
                    except Exception:
                        log.exception("close_open_segment failed during pause")
            elif msg_type == "resume":
                log.info("WS received 'resume'")
            elif msg_type == "switch_lang":
                # P1: log and ignore (UI doesn't expose). Architecture supports it later.
                log.info("WS switch_lang requested but not implemented: %s", data)
            else:
                log.warning("WS ignoring unknown control message: %s", data)

    except Exception:
        log.exception("WebSocket handler crashed")
        try:
            await _send_error(ws, "STT_FAILED", "internal error", recoverable=False)
        except Exception:
            pass
    finally:
        if mixer is not None:
            try:
                await mixer.stop()
            except Exception:
                log.exception("syscap stop failed during cleanup")
        if pump_task is not None:
            pump_task.cancel()
            try:
                await pump_task
            except (asyncio.CancelledError, Exception):
                pass
        if pipeline is not None:
            try:
                await pipeline.stop()
            except Exception:
                log.exception("Pipeline stop failed during cleanup")
        if wav_writer is not None:
            try:
                wav_bytes = wav_writer.bytes_written
                wav_writer.close()
            except Exception:
                log.exception("WAV writer close failed during cleanup")
        if recorder is not None:
            try:
                await recorder.stop()
                wav_path = (
                    resolve_recording_path(session_id, recordings_dir=recordings_dir)
                    if session_id else None
                )
                if (
                    screen_video_path and screen_video_path.exists()
                    and wav_path and wav_path.exists()
                ):
                    out_mp4 = wav_path.with_suffix(".mp4")
                    if await screenrec.mux(screen_video_path, wav_path, out_mp4):
                        screen_video_path.unlink(missing_ok=True)
                        log.info("screen recording saved: %s", out_mp4)
            except Exception:
                log.exception("screen recording finalize failed")
        # Stamp the final session duration so the past-session list shows
        # the right "X minutes ago" — derived from the last speech_end.
        try:
            store_ref: Store | None = getattr(ws.app.state, "store", None)
            if store_ref is not None and max_ended_at > 0:
                await store_ref.update_session_duration(session_id, max_ended_at)
        except Exception:
            log.exception("duration update failed during cleanup")
        # The session leaves `recording` here whatever happened above. Then,
        # if the user asked for it and there is something to work with, the
        # whole-recording pass takes over (status → processing); the live
        # rows stay on screen until it replaces them.
        if session_row_exists and session_id:
            await _finish_session(
                ws.app.state,
                session_id,
                refine=refine_after_stop and wav_bytes > 0 and max_ended_at > 0,
                recordings_dir=recordings_dir,
            )
        try:
            await ws.close()
        except Exception:
            pass
        log.info("WS connection closed")
