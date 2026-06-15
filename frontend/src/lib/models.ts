// First-run model setup. The backend reports which required models are present
// in ~/.wrenote/models/ and downloads any that are missing as a job (progress
// streams over the shared /jobs SSE — see lib/jobs.ts).

// Same-origin: the SPA is served by the backend, so talk to our own origin.
// Vite dev proxies /api to the backend — see vite.config.ts.
const BASE =
  typeof window !== "undefined" ? window.location.origin : "http://localhost:8000";

export interface ModelStatusItem {
  key: string; // "stt" | "translator" | "chat"
  filename: string;
  present: boolean;
  size: number; // approx bytes
  downloaded: number; // bytes already on disk (full or .partial)
}

export interface ModelStatus {
  models: ModelStatusItem[];
  all_present: boolean;
}

export async function getModelStatus(): Promise<ModelStatus> {
  const res = await fetch(`${BASE}/api/models/status`);
  if (!res.ok) throw new Error(`model status failed (${res.status})`);
  return (await res.json()) as ModelStatus;
}

export async function startModelDownload(): Promise<{
  job_id: string | null;
  all_present: boolean;
}> {
  const res = await fetch(`${BASE}/api/models/download`, { method: "POST" });
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`download failed (${res.status}): ${text}`);
  }
  return (await res.json()) as { job_id: string | null; all_present: boolean };
}
