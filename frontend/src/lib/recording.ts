// Helpers for talking to the backend's recording endpoints.
// Single-user local app — no auth, no retries beyond browser defaults.

// Same-origin: the SPA is served by the backend; talk to our own origin.
// Vite dev proxies /recordings to the backend — see vite.config.ts.
const BACKEND_BASE =
  typeof window !== "undefined" ? window.location.origin : "http://localhost:8000";

/** URL the browser can navigate to (or anchor `href`) to download the WAV. */
export function recordingUrl(sessionId: string): string {
  return `${BACKEND_BASE}/recordings/${encodeURIComponent(sessionId)}.wav`;
}

/** Remove the WAV file for a session. Resolves regardless of 404. */
export async function deleteRecording(sessionId: string): Promise<void> {
  try {
    const res = await fetch(recordingUrl(sessionId), { method: "DELETE" });
    if (!res.ok && res.status !== 404) {
      console.warn("delete recording non-ok", res.status);
    }
  } catch (e) {
    console.warn("delete recording failed", e);
  }
}
