import { API_BASE as BASE } from "./api";
// First-run model setup. The backend reports which required models are present
// in ~/.wrenote/models/ and downloads any that are missing as a job (progress
// streams over the shared /jobs SSE — see lib/jobs.ts).

// Same-origin: the SPA is served by the backend, so talk to our own origin.
// Vite dev proxies /api to the backend — see vite.config.ts.

/** A model slot in the engine's config. Two are speech recognition: what a
 *  live session hears (`stt`, may be a streaming model) and what a whole
 *  recording goes through afterwards (`stt_offline`, Whisper). */
export type ModelKind = "stt" | "stt_offline" | "translator" | "chat" | "speaker";

export interface ModelStatusItem {
  key: ModelKind;
  filename: string;
  present: boolean;
  size: number; // approx bytes
  downloaded: number; // bytes already on disk (full or .partial)
  model_id: string;
  model_name: string;
}

/** One model that could be picked for a kind. Reasons travel as codes; the
 *  wording is ours (see i18n) — same contract as the compute options. */
export interface ModelOption {
  id: string;
  kind: ModelKind;
  tier: "small" | "medium" | "large";
  name: string;
  note_code: string; // what this model is for
  size_mb: number;
  download_mb: number | null; // null = already on disk
  installed: boolean;
  fits: boolean; // the machine meets its requirements
  recommended: boolean;
  selected: boolean;
  blocked_code: string; // set when !fits
  blocked_params: Record<string, string>;
}

/** The choices for one kind, plus the hardware verdict that ranked them. */
export interface KindOptions {
  kind: ModelKind;
  reason_code: string;
  reason_params: Record<string, string>;
  options: ModelOption[];
}

export interface ModelStatus {
  models: ModelStatusItem[];
  all_present: boolean;
  options: KindOptions[];
  selected: Record<string, string | null>;
}

export async function getModelStatus(): Promise<ModelStatus> {
  const res = await fetch(`${BASE}/models/status`);
  if (!res.ok) throw new Error(`model status failed (${res.status})`);
  return (await res.json()) as ModelStatus;
}

/** Pick the model for one kind. `applies` says when it takes effect: "now"
 *  (the engine swapped it) or "next_session" (backends are per-session). */
export async function selectModel(
  kind: ModelKind,
  model: string,
): Promise<{ kind: string; model: string; applies: "now" | "next_session" }> {
  const res = await fetch(`${BASE}/models/select`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ kind, model }),
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = (await res.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      /* not JSON */
    }
    throw new Error(`model select failed (${res.status}): ${detail}`);
  }
  return (await res.json()) as {
    kind: string;
    model: string;
    applies: "now" | "next_session";
  };
}

export async function startModelDownload(): Promise<{
  job_id: string | null;
  all_present: boolean;
}> {
  const res = await fetch(`${BASE}/models/download`, { method: "POST" });
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`download failed (${res.status}): ${text}`);
  }
  return (await res.json()) as { job_id: string | null; all_present: boolean };
}
