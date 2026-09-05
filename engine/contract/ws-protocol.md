# Wrenote engine — WebSocket protocol (contract v1)

Endpoint: `GET /v1/ws` (WebSocket upgrade). One connection = one live
transcription session; a fresh pipeline is built per connection. OpenAPI cannot
describe this endpoint, so this file is the normative reference for it; the HTTP
resources are in `openapi.json` next to it. The Pydantic models in
`wrenote/core/events.py` are the source of truth for field names and types —
keep the tables below in sync when they change.

## Connection gate

* **Origin**: only loopback origins are accepted (`http(s)://localhost`,
  `127.0.0.1`, `[::1]`, any port), plus a missing or `null` Origin header.
  Anything else is closed with `1008 origin not allowed`.
* **Token**: when the engine was launched with `WRENOTE_AUTH_TOKEN` (every
  desktop launch), the handshake must carry it as the `wrenote_token` cookie or
  the `?token=` query parameter, else `1008 unauthorized`. In plain dev the
  token is empty and the check is skipped.

## Client → server

The **first** message must be a JSON text frame of type `start`; anything else
(including invalid JSON) yields an `error` event with code `BAD_CONFIG`,
`recoverable: false`, and the connection is closed.

```jsonc
{
  "type": "start",
  "config": {
    "session_id": "…",            // client-generated, [A-Za-z0-9._-]{1,128}; else the server picks a UUID
    "title": "Daily sync",         // optional
    "src": "en", "tgt": "zh",      // languages; defaults from engine config `session.*`
    "capture_system": false,       // mix system output into the mic (needs platform capability)
    "capture_screen": false,       // record screen/window video alongside audio
    "capture_target": null,        // {type: "window"|"display", id, title} from GET /v1/capture/targets; null = full screen
    "min_silence_ms": 800, "max_segment_ms": 25000,
    "partial_interval_ms": 800, "partial_min_audio_ms": 500,
    "translate_partials": true, "translate_enabled": true,
    "extended_silence_factor": 2.25,
    "speaker_enabled": true, "speaker_threshold": 0.65, "speaker_min_audio_ms": 1000,
    "stt_context_chars": 200,          // tail of the previous segment's text given to Whisper; 0 = off
    "translate_context_segments": 1,   // earlier segments the translator sees with each new one; 0 = off
    "refine_after_stop": true          // after `stop`, re-transcribe the whole recording and replace the
                                       // transcript (session status → "processing"; see GET /v1/sessions)
  }
}
```

After `ready`, the client streams audio as **binary frames**: raw PCM,
16 kHz, mono, signed 16-bit little-endian, ~100 ms per frame. Any client that
can produce that stream (browser AudioWorklet, AVAudioEngine, WASAPI, …) works
unchanged — this is the universal audio input to the engine.

Control messages (JSON text frames) during a session:

| type          | payload                | effect                                                   |
|---------------|------------------------|----------------------------------------------------------|
| `stop`        | —                      | flush, finalize the recording, close cleanly; then, with `refine_after_stop`, the session goes `processing` and its `job_id` is on `GET /v1/sessions/{id}` |
| `pause`       | —                      | drop incoming audio until `resume`                       |
| `resume`      | —                      | resume processing                                        |
| `switch_lang` | `{"src": …, "tgt": …}` | reserved: accepted and logged, not yet acted on          |

Unknown control types are logged and ignored.

## Server → client (JSON text frames)

| type                          | fields (beyond `type`)                                                                 |
|-------------------------------|----------------------------------------------------------------------------------------|
| `ready`                       | `stt`, `vad`, `translator`, `speaker?` — one `BackendInfo` each                        |
| `speech_start` / `speech_end` | `segment_id`, `ts`                                                                     |
| `partial` / `final`           | `segment_id`, `text`, `lang`, `t0?`, `t1?`, `confidence?`, `speaker?`                  |
| `translation`                 | `segment_id`, `text`, `src_lang`, `tgt_lang`, `partial`, `skipped`, `speaker?`         |
| `error`                       | `code`, `msg`, `recoverable`                                                           |
| `metric`                      | `queues` (name → depth), `latencies` (name → seconds), `ts`                            |

`BackendInfo`: `name`, `version`, `model`, `device`, `supported_languages`,
`capabilities`.

`partial` may repeat for a segment as its transcription grows; `final` is sent
once when the segment is closed. A `translation` with `partial: true` is a
best-effort translation of a growing partial and will be overwritten;
`skipped: true` means source and target language were the same, so no
translation was performed and the UI should not show "pending".

Error codes:

| code                  | recoverable | when                                                                 |
|-----------------------|-------------|----------------------------------------------------------------------|
| `BAD_CONFIG`          | no          | first message wasn't a valid `start`, or its config was rejected      |
| `MODEL_NOT_FOUND`     | no          | a configured model file is missing (`GET /v1/models/status` to fix)   |
| `MODEL_LOAD_FAILED`   | no          | a backend failed to load its model                                    |
| `STT_FAILED`          | varies      | transcription of a segment failed; also the handler's internal error  |
| `TRANSLATION_TIMEOUT` | yes         | translation exceeded its deadline; the segment stays untranslated     |
| `TRANSLATION_FAILED`  | yes         | translation raised; the segment stays untranslated                    |

## Persistence guarantee

`partial`, `final` and `translation` events are written to the store **before**
they are sent, so a client that reconnects can rebuild the transcript from
`GET /v1/sessions/{session_id}` without gaps. The session row itself is
upserted on `start`.

## Versioning

Breaking changes to this protocol ship under a new prefix (`/v2/ws`) with the
old endpoint kept alive for at least one release; additive fields are not
breaking. Clients pin the version they were built against.
