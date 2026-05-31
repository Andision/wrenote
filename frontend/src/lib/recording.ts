// Helpers for talking to the backend's recording endpoints.
// Single-user local app — no auth, no retries beyond browser defaults.

const BACKEND_HOST =
  typeof window !== "undefined" ? window.location.hostname : "localhost";
const BACKEND_PORT = 8000;
const BACKEND_BASE = `http://${BACKEND_HOST}:${BACKEND_PORT}`;

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
