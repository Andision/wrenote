// Mirrors engine/wrenote/core/events.py (see engine/contract/ws-protocol.md) — keep in sync.

export interface BackendInfo {
  name: string;
  version: string;
  model: string;
  device: string;
  supported_languages?: string[];
  capabilities?: Record<string, unknown>;
}

export interface ReadyEvent {
  type: "ready";
  stt: BackendInfo;
  vad: BackendInfo;
  translator: BackendInfo;
  speaker?: BackendInfo | null;
}

export interface VADEvent {
  type: "speech_start" | "speech_end";
  segment_id: string;
  ts: number;
}

export interface TranscriptEvent {
  type: "partial" | "final";
  segment_id: string;
  text: string;
  lang: string;
  t0?: number | null;
  t1?: number | null;
  confidence?: number | null;
  speaker?: string | null;
}

export interface TranslationEvent {
  type: "translation";
  segment_id: string;
  text: string;
  src_lang: string;
  tgt_lang: string;
  partial: boolean;
  /** True when the backend chose not to translate (detected source already
   *  equals target). `text` will be empty; the UI should collapse the row. */
  skipped?: boolean;
  speaker?: string | null;
}

export interface ErrorEvent {
  type: "error";
  code: string;
  msg: string;
  recoverable: boolean;
}

export interface MetricsEvent {
  type: "metric";
  queues: Record<string, number>;
  latencies: Record<string, number>;
  ts: number;
}

export type ServerEvent =
  | ReadyEvent
  | VADEvent
  | TranscriptEvent
  | TranslationEvent
  | ErrorEvent
  | MetricsEvent;

// ---------- App-side data model ----------

export interface Segment {
  /** UUID matching the backend's segment_id. */
  segmentId: string;
  /** Session-relative seconds; first speech_start time. */
  startedAt: number;
  /** Session-relative seconds; speech_end or last update. */
  endedAt: number;
  /** Original transcript. */
  origText: string;
  origStatus: "partial" | "final";
  origLang?: string;
  /** Translation. `skipped` means the backend decided no translation was
   *  needed (detected source already matches target). `stale` means the
   *  original was edited after translating, so this no longer matches. */
  transText: string;
  transStatus: "partial" | "final" | "pending" | "skipped" | "stale";
  transLang?: string;
  /** Speaker label, kept for future post-processing — currently not shown. */
  speaker?: string | null;
}

/** Where a session is in its life — mirrors engine/wrenote/core/store.py
 *  SESSION_STATUSES.
 *   recording   live; segments arrive as the user speaks
 *   processing  the engine is rewriting the transcript from the recording
 *               (the pass after a recording stops, or an upload); the rows
 *               on screen stay until it replaces them
 *   ready       the transcript is what the user gets
 *   failed      that pass died; `statusDetail` says why and the previous
 *               transcript is still there */
export type SessionStatus = "recording" | "processing" | "ready" | "failed";

export interface SessionMeta {
  id: string;
  title: string;
  /** ISO timestamp when the session was first started. */
  createdAt: string;
  /** Total speech seconds (sum of segment durations). */
  durationS: number;
  /** Source / target language pair used. */
  srcLang: string;
  tgtLang: string;
  /** Group/folder this session belongs to, or null when ungrouped. */
  groupId: string | null;
  status: SessionStatus;
  /** Engine-side reason code or message when `status` is "failed". */
  statusDetail: string | null;
  /** When the transcript last came from a whole-recording pass; null if never. */
  refinedAt: string | null;
  /** The job rewriting this session while `status` is "processing", so a
   *  client that finds one (after a reload, say) can follow its progress. */
  jobId: string | null;
}

export interface StoredSession extends SessionMeta {
  segments: Segment[];
}

export interface SessionGroup {
  id: string;
  name: string;
  createdAt: string;
  position: number;
}

export type ConnectionState =
  | "disconnected"
  | "connecting"
  | "connected"
  | "recording"
  | "paused"
  | "stopping"
  | "error";

export interface ReadyInfo {
  stt?: BackendInfo;
  vad?: BackendInfo;
  translator?: BackendInfo;
  speaker?: BackendInfo | null;
}

export interface ClientStartConfig {
  src: string;
  tgt: string;
  min_silence_ms?: number;
  max_segment_ms?: number;
  partial_interval_ms?: number;
  translate_partials?: boolean;
  speaker_enabled?: boolean;
}
