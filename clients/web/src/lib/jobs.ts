import { API_BASE as BASE } from "./api";
// Subscribe to a backend job's SSE stream. Returns a function to close
// the stream early (browser closes automatically on terminal frame too).

// Same-origin: the SPA is served by the backend, so talk to our own origin
// (port included). Vite dev proxies these paths to the backend — see vite.config.ts.

export interface JobSnapshot {
  id: string;
  kind: string;
  status: "running" | "done" | "error";
  phase: string;
  phase_idx: number;
  phase_count: number;
  fraction: number; // 0..1
  elapsed_s: number;
  eta_s: number | null;
  log: string[];
  error: string | null;
  result: Record<string, unknown> | null;
}

export interface SubscribeOptions {
  onSnapshot: (snap: JobSnapshot) => void;
  onError?: (err: Error) => void;
}

export function subscribeJob(
  jobId: string,
  opts: SubscribeOptions,
): () => void {
  const es = new EventSource(`${BASE}/jobs/${encodeURIComponent(jobId)}/stream`);
  es.onmessage = (ev) => {
    try {
      const snap = JSON.parse(ev.data) as JobSnapshot;
      opts.onSnapshot(snap);
      if (snap.status !== "running") {
        // Backend sends one terminal frame then closes. Help the browser
        // not retry — it will, on EventSource error, otherwise.
        es.close();
      }
    } catch {
      // ignore malformed frames
    }
  };
  es.onerror = () => {
    // Auto-reconnect spam after the server closes the stream is normal
    // for EventSource — only surface real failures via the error callback
    // if we haven't yet received a terminal snapshot.
    if (es.readyState === EventSource.CLOSED) return;
    opts.onError?.(new Error("job stream errored"));
    es.close();
  };
  return () => es.close();
}

export function formatEta(seconds: number | null): string {
  if (seconds == null) return "estimating…";
  if (seconds <= 1) return "< 1s";
  if (seconds < 60) return `~${Math.round(seconds)}s`;
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return s > 0 ? `~${m}m ${s}s` : `~${m}m`;
}
