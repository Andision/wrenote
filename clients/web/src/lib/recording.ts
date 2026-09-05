// Helpers for talking to the backend's recording endpoints.
// Single-user local app — no auth, no retries beyond browser defaults.

import { API_BASE } from "./api";

/** URL the browser can navigate to (or anchor `href`) to download the WAV. */
export function recordingUrl(sessionId: string): string {
  return `${API_BASE}/recordings/${encodeURIComponent(sessionId)}.wav`;
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
