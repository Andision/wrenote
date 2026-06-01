// Global app state via Zustand. Holds the active session's segments,
// connection state, settings, and the list of past sessions.
import { create } from "zustand";

import {
  createGroup as createGroupRemote,
  deleteGroup as deleteGroupRemote,
  deleteSession as deleteSessionFromStorage,
  listGroups,
  loadAllSessions,
  loadSession as loadSessionFromBackend,
  newSessionId,
  renameGroup as renameGroupRemote,
  renameSession as renameSessionOnBackend,
  setSessionGroup as setSessionGroupRemote,
  suggestSessionTitle,
} from "../lib/storage";
import type {
  ConnectionState,
  ReadyInfo,
  Segment,
  SessionGroup,
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
  /** True while the title is still the generic placeholder — i.e. the user
   *  hasn't renamed it, so we may replace it with an LLM-derived title. */
  titleIsAuto: boolean;
  sessionStartedAt: string | null;
  segmentOrder: string[];
  segments: Record<string, Segment>;
  /** Per-speaker color overrides for this session (label → CSS color). */
  speakerColors: Record<string, string>;

  // Connection / mic
  connection: ConnectionState;
  ready: ReadyInfo | null;
  micLevel: number;
  errorMsg: string | null;

  // History
  pastSessions: SessionMeta[];
  /** Sidebar folders. Membership lives on each session's groupId. */
  groups: SessionGroup[];

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
  /** Playback speed multiplier (0.5–2). Applied to the <audio> element. */
  playbackRate: number;
  /** Loop behaviour: off, repeat the current segment, or loop between A–B. */
  loopMode: "off" | "segment" | "ab";
  /** A–B loop endpoints in seconds (session-relative); null when unset. */
  loopA: number | null;
  loopB: number | null;
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
  /** After a recording stops, ask the LLM for a title (best-effort). */
  autoTitleAfterRecording: () => void;
  saveCurrent: () => Promise<void>;
  loadSession: (id: string) => Promise<void>;
  deletePastSession: (id: string) => Promise<void>;
  refreshPastSessions: () => Promise<void>;
  // Session groups (sidebar folders)
  refreshGroups: () => Promise<void>;
  createGroup: (name?: string) => Promise<void>;
  renameGroup: (id: string, name: string) => void;
  deleteGroup: (id: string) => Promise<void>;
  moveSessionToGroup: (sessionId: string, groupId: string | null) => void;

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
  /** Override the color for a speaker label in the current session. */
  setSpeakerColor: (label: string, color: string) => void;

  // Playback bookkeeping — set by the usePlayback hook.
  setPlayback: (playingSegmentId: string | null, isPlaying: boolean) => void;
  setPlaybackTime: (currentTime: number, duration: number) => void;
  setPlaybackLevel: (level: number) => void;
  setPlaybackRate: (rate: number) => void;
  setLoopMode: (mode: "off" | "segment" | "ab") => void;
  setLoopA: (t: number | null) => void;
  setLoopB: (t: number | null) => void;
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
    __WRENOTE_STORE__?: typeof useSessionStore;
  }
}

// Sidebar open/closed is a UI preference — persist it so a refresh keeps
// the panel where the user left it.
const SIDEBAR_KEY = "wrenote.sidebarOpen";

function loadSidebarOpen(): boolean {
  try {
    return window.localStorage.getItem(SIDEBAR_KEY) === "1";
  } catch {
    return false;
  }
}

function saveSidebarOpen(open: boolean): void {
  try {
    window.localStorage.setItem(SIDEBAR_KEY, open ? "1" : "0");
  } catch {
    // localStorage unavailable / quota — non-critical.
  }
}

// Per-speaker color overrides are a presentation preference, kept client-side
// and scoped per session: { [sessionId]: { [label]: cssColor } }.
const SPEAKER_COLORS_KEY = "wrenote.speakerColors";

function readAllSpeakerColors(): Record<string, Record<string, string>> {
  try {
    const raw = window.localStorage.getItem(SPEAKER_COLORS_KEY);
    return raw ? (JSON.parse(raw) as Record<string, Record<string, string>>) : {};
  } catch {
    return {};
  }
}

function loadSpeakerColors(sessionId: string | null): Record<string, string> {
  if (!sessionId) return {};
  return readAllSpeakerColors()[sessionId] ?? {};
}

function saveSpeakerColors(
  sessionId: string,
  map: Record<string, string>,
): void {
  try {
    const all = readAllSpeakerColors();
    if (Object.keys(map).length === 0) delete all[sessionId];
    else all[sessionId] = map;
    window.localStorage.setItem(SPEAKER_COLORS_KEY, JSON.stringify(all));
  } catch {
    // non-critical
  }
}

export const useSessionStore = create<State & Actions>((set, get) => ({
  sessionId: null,
  sessionTitle: "New session",
  titleIsAuto: false,
  sessionStartedAt: null,
  segmentOrder: [],
  segments: {},
  speakerColors: {},

  connection: "disconnected",
  ready: null,
  micLevel: 0,
  errorMsg: null,

  // Filled in on app mount via refreshPastSessions() — async fetch from backend.
  pastSessions: [],
  groups: [],

  settings: { ...DEFAULT_SETTINGS },
  settingsOpen: false,
  sidebarOpen: loadSidebarOpen(),
  chatOpen: false,
  playingSegmentId: null,
  isPlaying: false,
  playbackCurrentTime: 0,
  playbackDuration: 0,
  playbackLevel: 0,
  playbackRate: 1,
  loopMode: "off",
  loopA: null,
  loopB: null,

  setConnection: (connection) => set({ connection }),
  setReady: (ready) => set({ ready }),
  setError: (errorMsg) => set({ errorMsg }),
  setMicLevel: (micLevel) => set({ micLevel }),

  startNewSession: () => {
    const id = newSessionId();
    set({
      sessionId: id,
      sessionTitle: defaultSessionTitle(),
      titleIsAuto: true,
      sessionStartedAt: new Date().toISOString(),
      segmentOrder: [],
      segments: {},
      speakerColors: {},
      errorMsg: null,
    });
    return id;
  },

  renameSession: (title) => {
    const next = title || "New session";
    const s = get();
    // A manual rename pins the title — no LLM auto-title should overwrite it.
    set({ sessionTitle: next, titleIsAuto: false });
    // Fire-and-forget; if the session row doesn't exist yet (user typed a
    // title pre-record) the next WS "start" config carries it instead.
    if (s.sessionId) void renameSessionOnBackend(s.sessionId, next);
  },

  autoTitleAfterRecording: () => {
    const s = get();
    if (!s.titleIsAuto || !s.sessionId || s.segmentOrder.length === 0) return;
    const id = s.sessionId;
    void suggestSessionTitle(id).then((title) => {
      if (!title) return;
      const cur = get();
      // Only apply if we're still on this session and the user hasn't
      // manually renamed it in the meantime.
      if (cur.sessionId === id && cur.titleIsAuto) {
        set({ sessionTitle: title, titleIsAuto: false });
      }
      void loadAllSessions().then((list) => set({ pastSessions: list }));
    });
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
      titleIsAuto: false, // loaded sessions keep their stored title
      sessionStartedAt: target.createdAt,
      segmentOrder: order,
      segments: segmentsById,
      speakerColors: loadSpeakerColors(target.id),
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

  refreshGroups: async () => {
    set({ groups: await listGroups() });
  },

  createGroup: async (name) => {
    const g = await createGroupRemote(name);
    if (g) set((s) => ({ groups: [...s.groups, g] }));
  },

  renameGroup: (id, name) => {
    const trimmed = name.trim();
    if (!trimmed) return;
    set((s) => ({
      groups: s.groups.map((g) => (g.id === id ? { ...g, name: trimmed } : g)),
    }));
    void renameGroupRemote(id, trimmed);
  },

  deleteGroup: async (id) => {
    // Members fall back to ungrouped (backend nulls their group_id too).
    set((s) => ({
      groups: s.groups.filter((g) => g.id !== id),
      pastSessions: s.pastSessions.map((p) =>
        p.groupId === id ? { ...p, groupId: null } : p,
      ),
    }));
    await deleteGroupRemote(id);
  },

  moveSessionToGroup: (sessionId, groupId) => {
    set((s) => ({
      pastSessions: s.pastSessions.map((p) =>
        p.id === sessionId ? { ...p, groupId } : p,
      ),
    }));
    void setSessionGroupRemote(sessionId, groupId);
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
    set((s) => {
      const next = open ?? !s.sidebarOpen;
      saveSidebarOpen(next);
      return { sidebarOpen: next };
    }),
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
      if (changed === 0) return {} as Partial<State>;
      // Carry any color override across the rename.
      let colors = s.speakerColors;
      if (colors[from] && !colors[to]) {
        colors = { ...colors };
        colors[to] = colors[from];
        delete colors[from];
        if (s.sessionId) saveSpeakerColors(s.sessionId, colors);
      }
      return { segments: next, speakerColors: colors };
    }),

  setSpeakerColor: (label, color) =>
    set((s) => {
      if (!label || label === "unknown") return {} as Partial<State>;
      const colors = { ...s.speakerColors, [label]: color };
      if (s.sessionId) saveSpeakerColors(s.sessionId, colors);
      return { speakerColors: colors };
    }),

  setPlayback: (playingSegmentId, isPlaying) =>
    set({ playingSegmentId, isPlaying }),
  setPlaybackTime: (currentTime, duration) =>
    set({ playbackCurrentTime: currentTime, playbackDuration: duration }),
  setPlaybackLevel: (level) => set({ playbackLevel: level }),
  setPlaybackRate: (rate) => set({ playbackRate: rate }),
  setLoopMode: (loopMode) => set({ loopMode }),
  setLoopA: (loopA) => set({ loopA }),
  setLoopB: (loopB) => set({ loopB }),
}));

// Dev hook: lets Playwright (and devtools) reach the store from outside React.
if (typeof window !== "undefined" && import.meta.env.DEV) {
  window.__WRENOTE_STORE__ = useSessionStore;
}

// Placeholder until the session is recorded — at which point an LLM-derived
// title replaces it (see suggestSessionTitle). The sidebar shows the date and
// duration separately, so a generic label here is fine.
function defaultSessionTitle(): string {
  return "New session";
}
