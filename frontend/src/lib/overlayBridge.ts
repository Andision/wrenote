// Bridge between the main app window and the floating subtitle overlay.
//
// Both windows are served from the same loopback origin, so a BroadcastChannel
// carries the live transcript without any Electron IPC — which also means the
// overlay can be exercised in a plain browser tab during dev (open /#overlay).
// Electron is only involved for window management (toggle/close), exposed by
// preload.js as `window.wrenoteDesktop`.
import { useSessionStore } from "../store/sessionStore";
import type { ConnectionState, Segment } from "../types";

const CHANNEL = "wrenote-overlay";

export interface OverlayLine {
  orig: string;
  origStatus: Segment["origStatus"];
  trans: string;
  transStatus: Segment["transStatus"];
}

export interface OverlayStateMsg {
  type: "overlay-state";
  connection: ConnectionState;
  /** Oldest → newest; at most the two latest segments with any text. */
  lines: OverlayLine[];
  /** Mic RMS (0–~0.25), quantized — drives the compact bar's level meter. */
  micLevel: number;
}

interface OverlayHelloMsg {
  type: "overlay-hello";
}

type OverlayMsg = OverlayStateMsg | OverlayHelloMsg;

declare global {
  interface Window {
    /** Injected by electron/preload.js; absent in a plain browser. */
    wrenoteDesktop?: {
      toggleOverlay: () => Promise<void>;
      closeOverlay: () => Promise<void>;
      /** Resize the overlay window, keeping it anchored at its bottom-center. */
      resizeOverlay: (width: number, height: number) => Promise<void>;
    };
  }
}

export function hasDesktopOverlay(): boolean {
  return typeof window !== "undefined" && Boolean(window.wrenoteDesktop);
}

export function isOverlayWindow(): boolean {
  return typeof window !== "undefined" && window.location.hash === "#overlay";
}

function buildPayload(): OverlayStateMsg {
  const s = useSessionStore.getState();
  const lines: OverlayLine[] = [];
  for (const id of s.segmentOrder.slice(-2)) {
    const seg = s.segments[id];
    if (!seg || (!seg.origText && !seg.transText)) continue;
    lines.push({
      orig: seg.origText,
      origStatus: seg.origStatus,
      trans: seg.transStatus === "skipped" ? "" : seg.transText,
      transStatus: seg.transStatus,
    });
  }
  // Quantize the mic level so a live meter doesn't spam the channel on every
  // audio frame while still moving smoothly (~50 steps over the useful range).
  const micLevel = Math.round(s.micLevel * 50) / 50;
  return { type: "overlay-state", connection: s.connection, lines, micLevel };
}

/**
 * Main-window side: republish the latest transcript slice whenever it changes.
 * The store also updates on high-frequency noise (mic level, playback time),
 * so we serialize the small payload and skip posts when nothing visible moved.
 */
export function initOverlayPublisher(): () => void {
  if (typeof BroadcastChannel === "undefined") return () => {};
  const ch = new BroadcastChannel(CHANNEL);
  let lastKey = "";
  const publish = (force = false) => {
    const payload = buildPayload();
    const key = JSON.stringify(payload);
    if (!force && key === lastKey) return;
    lastKey = key;
    ch.postMessage(payload);
  };
  const unsubscribe = useSessionStore.subscribe(() => publish());
  // A freshly opened overlay asks for the current state.
  ch.onmessage = (e: MessageEvent<OverlayMsg>) => {
    if (e.data?.type === "overlay-hello") publish(true);
  };
  publish();
  return () => {
    unsubscribe();
    ch.close();
  };
}

/** Overlay side: subscribe to state broadcasts and request a first snapshot. */
export function connectOverlayListener(
  onState: (msg: OverlayStateMsg) => void,
): () => void {
  if (typeof BroadcastChannel === "undefined") return () => {};
  const ch = new BroadcastChannel(CHANNEL);
  ch.onmessage = (e: MessageEvent<OverlayMsg>) => {
    if (e.data?.type === "overlay-state") onState(e.data);
  };
  ch.postMessage({ type: "overlay-hello" } satisfies OverlayHelloMsg);
  return () => ch.close();
}
