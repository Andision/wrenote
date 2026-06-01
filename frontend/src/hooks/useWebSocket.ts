// WebSocket lifecycle hook. Connects to /ws on the same origin (default), sends
// a "start" message with session config, dispatches incoming events to the
// store, and exposes a `feedAudio` method for the microphone hook.
import { useCallback, useEffect, useRef } from "react";

import { useSessionStore } from "../store/sessionStore";
import type { ServerEvent } from "../types";

interface UseWebSocketOptions {
  url?: string;
}

// Same-origin WebSocket: the SPA is served by the backend, so /ws lives on the
// page's own host+port (works under a dynamic port too). Vite dev proxies /ws.
//
// The desktop build gates /ws with a per-launch token. WKWebView/WebView2 send
// the same-origin cookie on the handshake, but to not depend on that we also
// read the (non-HttpOnly) token cookie and pass it as ?token=. In dev / browser
// there is no token cookie, so the query is simply omitted.
function loopbackToken(): string {
  if (typeof document === "undefined") return "";
  const m = document.cookie.match(/(?:^|;\s*)wrenote_token=([^;]+)/);
  return m ? decodeURIComponent(m[1]) : "";
}

function defaultWsUrl(): string {
  if (typeof window === "undefined") return "ws://localhost:8000/ws";
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  const token = loopbackToken();
  const query = token ? `?token=${encodeURIComponent(token)}` : "";
  return `${proto}://${window.location.host}/ws${query}`;
}

const DEFAULT_URL = defaultWsUrl();

export function useWebSocket({ url = DEFAULT_URL }: UseWebSocketOptions = {}) {
  const wsRef = useRef<WebSocket | null>(null);

  const startSession = useCallback(() => {
    if (wsRef.current && wsRef.current.readyState <= WebSocket.OPEN) {
      console.warn("startSession: already connected");
      return;
    }

    const store = useSessionStore.getState();
    store.startNewSession();
    store.setConnection("connecting");
    store.setError(null);

    const ws = new WebSocket(url);
    ws.binaryType = "arraybuffer";
    wsRef.current = ws;

    ws.onopen = () => {
      const state = useSessionStore.getState();
      const s = state.settings;
      ws.send(
        JSON.stringify({
          type: "start",
          config: {
            // Frontend-generated session id; backend uses it as the WAV
            // filename + SQLite primary key so a download URL and a
            // future GET /sessions/{id} both work off the same id.
            session_id: state.sessionId,
            title: state.sessionTitle,
            created_at: state.sessionStartedAt,
            src: s.srcLang,
            tgt: s.tgtLang,
            min_silence_ms: s.minSilenceMs,
            max_segment_ms: s.maxSegmentMs,
            partial_interval_ms: s.partialIntervalMs,
            translate_partials: s.translatePartials,
            translate_enabled: s.translateEnabled,
            speaker_enabled: s.speakerEnabled,
          },
        }),
      );
      useSessionStore.getState().setConnection("connected");
    };

    ws.onmessage = (ev) => {
      let msg: ServerEvent;
      try {
        msg = JSON.parse(ev.data);
      } catch {
        console.error("bad JSON from server:", ev.data);
        return;
      }
      handleServerEvent(msg);
    };

    ws.onclose = (ev) => {
      console.log("ws closed", ev.code, ev.reason);
      const store = useSessionStore.getState();
      // Persistence already happened on the server side as events flowed.
      // saveCurrent() now just refreshes the visible past-session list.
      void store.saveCurrent();
      store.setConnection("disconnected");
    };

    ws.onerror = () => {
      useSessionStore.getState().setError("WebSocket error — check that the backend is running");
      useSessionStore.getState().setConnection("error");
    };
  }, [url]);

  const stopSession = useCallback(() => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      useSessionStore.getState().setConnection("disconnected");
      return;
    }
    useSessionStore.getState().setConnection("stopping");
    try {
      ws.send(JSON.stringify({ type: "stop" }));
    } catch (e) {
      console.warn("stop send failed", e);
    }
    // Server will flush in-flight segments then close the WS; safety timeout
    // in case the backend hangs.
    setTimeout(() => {
      if (ws.readyState !== WebSocket.CLOSED) {
        try {
          ws.close();
        } catch {
          /* ignore */
        }
      }
    }, 12_000);
  }, []);

  const feedAudio = useCallback((pcm: ArrayBuffer) => {
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(pcm);
    }
  }, []);

  const pauseSession = useCallback(() => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    try {
      ws.send(JSON.stringify({ type: "pause" }));
    } catch (e) {
      console.warn("pause send failed", e);
    }
    useSessionStore.getState().setConnection("paused");
  }, []);

  const resumeSession = useCallback(() => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    try {
      ws.send(JSON.stringify({ type: "resume" }));
    } catch (e) {
      console.warn("resume send failed", e);
    }
    useSessionStore.getState().setConnection("recording");
  }, []);

  useEffect(() => {
    return () => {
      const ws = wsRef.current;
      if (ws && ws.readyState <= WebSocket.OPEN) {
        try {
          ws.close();
        } catch {
          /* ignore */
        }
      }
    };
  }, []);

  return { startSession, stopSession, pauseSession, resumeSession, feedAudio };
}

function handleServerEvent(msg: ServerEvent): void {
  const store = useSessionStore.getState();
  switch (msg.type) {
    case "ready":
      store.setReady({
        stt: msg.stt,
        vad: msg.vad,
        translator: msg.translator,
        speaker: msg.speaker ?? null,
      });
      store.setConnection("recording");
      return;
    case "speech_start":
      store.handleSpeechStart(msg);
      return;
    case "speech_end":
      store.handleSpeechEnd(msg);
      return;
    case "partial":
    case "final":
      store.handleTranscript(msg);
      return;
    case "translation":
      store.handleTranslation(msg);
      return;
    case "error":
      store.setError(`${msg.code}: ${msg.msg}`);
      if (!msg.recoverable) store.setConnection("error");
      return;
    case "metric":
      // No-op for now; will wire to StatusBar later.
      return;
    default:
      console.warn("unknown server event", msg);
  }
}
