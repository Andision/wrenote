// Global app state via Zustand. Holds the active session's segments,
// connection state, settings, and the list of past sessions.
import { create } from "zustand";

import {
  deleteSession as deleteSessionFromStorage,
  loadAllSessions,
  loadSession as loadSessionFromBackend,
  newSessionId,
  renameSession as renameSessionOnBackend,
} from "../lib/storage";
import type {
  ConnectionState,
  ReadyInfo,
  Segment,
  SessionMeta,
  TranscriptEvent,
  TranslationEvent,
  VADEvent,
} from "../types";

export interface SessionSettings {
  srcLang: string;
  tgtLang: string;
  /** When false, skip the translator entirely (transcribe-only mode).
   * Affects live sessions and uploads alike. */
  translateEnabled: boolean;
  minSilenceMs: number;
  maxSegmentMs: number;
  partialIntervalMs: number;
  translatePartials: boolean;
  /** Speaker identification is shipped disabled by default — it's unreliable
   * for tight back-and-forth conversation. The backend still supports it. */
  speakerEnabled: boolean;
  /** Per-segment playback mode: "single" stops at the segment boundary;
   * "continuous" plays through. Personal preference, applies to all sessions. */
  playbackMode: "single" | "continuous";
  /** Whether the StatusBar shows the mic + speaker level meters. */
  showLevelMeters: boolean;
}

const DEFAULT_SETTINGS: SessionSettings = {
  // "auto" → STT auto-detects the source language per segment. Pin to a
  // specific code ("en", "zh", …) to force a single language.
  srcLang: "auto",
  tgtLang: "zh",
  translateEnabled: true,
  minSilenceMs: 800,
  maxSegmentMs: 25000,
  partialIntervalMs: 800,
  translatePartials: true,
  speakerEnabled: false,
  playbackMode: "continuous",
  showLevelMeters: true,
};

interface State {
  // Active session
  sessionId: string | null;
  sessionTitle: string;
  sessionStartedAt: string | null;
  segmentOrder: string[];
  segments: Record<string, Segment>;

  // Connection / mic
  connection: ConnectionState;
  ready: ReadyInfo | null;
  micLevel: number;
  errorMsg: string | null;

  // History
  pastSessions: SessionMeta[];

  // Settings
  settings: SessionSettings;
  settingsOpen: boolean;
  sidebarOpen: boolean;
  chatOpen: boolean;

  // Playback (the actual <audio> element lives in usePlayback; this is
  // just the bit of state every segment card needs to read to highlight
  // itself or swap its play/pause icon).
  playingSegmentId: string | null;
  isPlaying: boolean;
  /** Audio playhead in seconds (session-relative). */
  playbackCurrentTime: number;
  /** Total session duration in seconds; 0 until the audio loads metadata. */
  playbackDuration: number;
  /** RMS of the playback audio (0..~0.5). Mirrors `micLevel`. */
  playbackLevel: number;
}

interface Actions {
  // Connection lifecycle
  setConnection: (state: ConnectionState) => void;
  setReady: (info: ReadyInfo) => void;
  setError: (msg: string | null) => void;
  setMicLevel: (lvl: number) => void;

  // Session lifecycle. Persistence happens server-side as events flow; the
  // returning Promises here only finish the round-trip — UI doesn't need to
  // await them unless it shows a spinner.
  startNewSession: () => string;
  renameSession: (title: string) => void;
  saveCurrent: () => Promise<void>;
  loadSession: (id: string) => Promise<void>;
  deletePastSession: (id: string) => Promise<void>;
  refreshPastSessions: () => Promise<void>;

  // Server events
  handleSpeechStart: (e: VADEvent) => void;
  handleSpeechEnd: (e: VADEvent) => void;
  handleTranscript: (e: TranscriptEvent) => void;
  handleTranslation: (e: TranslationEvent) => void;

  // Settings + UI
  updateSettings: (patch: Partial<SessionSettings>) => void;
  toggleSettings: (open?: boolean) => void;
  toggleSidebar: (open?: boolean) => void;
  toggleChat: (open?: boolean) => void;

  // Speaker post-processing (offline). Patches local segment state to
  // mirror the backend after a diarize / rename round-trip — avoids a
  // full session reload when the only thing that changed is labels.
  applySpeakerLabels: (labels: Record<string, string>) => void;
  applySpeakerRename: (from: string, to: string) => void;

  // Playback bookkeeping — set by the usePlayback hook.
  setPlayback: (playingSegmentId: string | null, isPlaying: boolean) => void;
  setPlaybackTime: (currentTime: number, duration: number) => void;
  setPlaybackLevel: (level: number) => void;
}

function ensureSegment(state: State, segmentId: string): Segment {
  const existing = state.segments[segmentId];
  if (existing) return existing;
  const fresh: Segment = {
    segmentId,
    startedAt: 0,
    endedAt: 0,
    origText: "",
    origStatus: "partial",
    transText: "",
    transStatus: "pending",
  };
  return fresh;
}

declare global {
  interface Window {
    __INTERPRETER_STORE__?: typeof useSessionStore;
  }
}

export const useSessionStore = create<State & Actions>((set, get) => ({
  sessionId: null,
  sessionTitle: "Untitled session",
  sessionStartedAt: null,
  segmentOrder: [],
  segments: {},

  connection: "disconnected",
  ready: null,
  micLevel: 0,
  errorMsg: null,

  // Filled in on app mount via refreshPastSessions() — async fetch from backend.
  pastSessions: [],

  settings: { ...DEFAULT_SETTINGS },
  settingsOpen: false,
  sidebarOpen: false,
  chatOpen: false,
  playingSegmentId: null,
  isPlaying: false,
  playbackCurrentTime: 0,
  playbackDuration: 0,
  playbackLevel: 0,

  setConnection: (connection) => set({ connection }),
  setReady: (ready) => set({ ready }),
  setError: (errorMsg) => set({ errorMsg }),
  setMicLevel: (micLevel) => set({ micLevel }),

  startNewSession: () => {
    const id = newSessionId();
    set({
      sessionId: id,
      sessionTitle: defaultSessionTitle(),
      sessionStartedAt: new Date().toISOString(),
      segmentOrder: [],
      segments: {},
      errorMsg: null,
    });
    return id;
  },

  renameSession: (title) => {
    const next = title || "Untitled session";
    const s = get();
    set({ sessionTitle: next });
    // Fire-and-forget; if the session row doesn't exist yet (user typed a
    // title pre-record) the next WS "start" config carries it instead.
    if (s.sessionId) void renameSessionOnBackend(s.sessionId, next);
  },

  saveCurrent: async () => {
    // Real-time persistence happens server-side via the WS event pump.
    // Here we just bring the new/updated session into the visible catalog.
    const list = await loadAllSessions();
    set({ pastSessions: list });
  },

  loadSession: async (id) => {
    const target = await loadSessionFromBackend(id);
    if (!target) return;
    const segmentsById: Record<string, Segment> = {};
    const order: string[] = [];
    for (const seg of target.segments) {
      segmentsById[seg.segmentId] = seg;
      order.push(seg.segmentId);
    }
    set({
      sessionId: target.id,
      sessionTitle: target.title,
      sessionStartedAt: target.createdAt,
      segmentOrder: order,
      segments: segmentsById,
    });
  },

  deletePastSession: async (id) => {
    // Backend cascades segments + the WAV file in one call.
    await deleteSessionFromStorage(id);
    const list = await loadAllSessions();
    set({ pastSessions: list });
  },

  refreshPastSessions: async () => {
    const list = await loadAllSessions();
    set({ pastSessions: list });
  },

  handleSpeechStart: ({ segment_id, ts }) =>
    set((s) => {
      if (s.segments[segment_id]) return {} as Partial<State>;
      const seg = { ...ensureSegment(s, segment_id), startedAt: ts, endedAt: ts };
      return {
        segments: { ...s.segments, [segment_id]: seg },
        segmentOrder: [...s.segmentOrder, segment_id],
      };
    }),

  handleSpeechEnd: ({ segment_id, ts }) =>
    set((s) => {
      const cur = s.segments[segment_id];
      if (!cur) return {} as Partial<State>;
      return {
        segments: {
          ...s.segments,
          [segment_id]: { ...cur, endedAt: ts },
        },
      };
    }),

  handleTranscript: (e) =>
    set((s) => {
      const cur = ensureSegment(s, e.segment_id);
      const updated: Segment = {
        ...cur,
        origText: e.text,
        origStatus: e.type,
        origLang: e.lang,
        startedAt: e.t0 ?? cur.startedAt,
        endedAt: e.t1 ?? cur.endedAt,
        speaker: e.speaker ?? cur.speaker ?? null,
      };
      const inOrder = s.segmentOrder.includes(e.segment_id);
      return {
        segments: { ...s.segments, [e.segment_id]: updated },
        segmentOrder: inOrder ? s.segmentOrder : [...s.segmentOrder, e.segment_id],
      };
    }),

  handleTranslation: (e) =>
    set((s) => {
      const cur = ensureSegment(s, e.segment_id);
      // Don't let a stale partial translation overwrite a final/skipped one.
      if ((cur.transStatus === "final" || cur.transStatus === "skipped") && e.partial) {
        return {} as Partial<State>;
      }
      const nextStatus: Segment["transStatus"] = e.skipped
        ? "skipped"
        : e.partial
          ? "partial"
          : "final";
      const updated: Segment = {
        ...cur,
        transText: e.text,
        transStatus: nextStatus,
        transLang: e.tgt_lang,
        speaker: e.speaker ?? cur.speaker ?? null,
      };
      const inOrder = s.segmentOrder.includes(e.segment_id);
      return {
        segments: { ...s.segments, [e.segment_id]: updated },
        segmentOrder: inOrder ? s.segmentOrder : [...s.segmentOrder, e.segment_id],
      };
    }),

  updateSettings: (patch) =>
    set((s) => ({ settings: { ...s.settings, ...patch } })),
  toggleSettings: (open) =>
    set((s) => ({ settingsOpen: open ?? !s.settingsOpen })),
  toggleSidebar: (open) =>
    set((s) => ({ sidebarOpen: open ?? !s.sidebarOpen })),
  toggleChat: (open) =>
    set((s) => ({ chatOpen: open ?? !s.chatOpen })),

  applySpeakerLabels: (labels) =>
    set((s) => {
      const next = { ...s.segments };
      for (const [sid, label] of Object.entries(labels)) {
        const cur = next[sid];
        if (cur) next[sid] = { ...cur, speaker: label };
      }
      return { segments: next };
    }),

  applySpeakerRename: (from, to) =>
    set((s) => {
      const next = { ...s.segments };
      let changed = 0;
      for (const sid of Object.keys(next)) {
        if (next[sid].speaker === from) {
          next[sid] = { ...next[sid], speaker: to };
          changed += 1;
        }
      }
      return changed > 0 ? { segments: next } : ({} as Partial<State>);
    }),

  setPlayback: (playingSegmentId, isPlaying) =>
    set({ playingSegmentId, isPlaying }),
  setPlaybackTime: (currentTime, duration) =>
    set({ playbackCurrentTime: currentTime, playbackDuration: duration }),
  setPlaybackLevel: (level) => set({ playbackLevel: level }),
}));

// Dev hook: lets Playwright (and devtools) reach the store from outside React.
if (typeof window !== "undefined" && import.meta.env.DEV) {
  window.__INTERPRETER_STORE__ = useSessionStore;
}

function defaultSessionTitle(): string {
  const d = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
