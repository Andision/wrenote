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

/** The slots a user may decline. Speech recognition is not one: the app is a
 *  transcriber. A declined feature downloads nothing and reports itself off,
 *  which is what turns its buttons into an offer to fetch the model. */
export type OptionalFeature = "translator" | "chat" | "speaker";

export const OPTIONAL_FEATURES: OptionalFeature[] = ["translator", "chat", "speaker"];

export type Features = Record<OptionalFeature, boolean>;

/** Everything on until the engine says otherwise, so a slow status fetch
 *  never briefly greys out features the user does have. */
export const ALL_FEATURES_ON: Features = { translator: true, chat: true, speaker: true };

/** The model slot a feature is: what to read a download size off. */
export const SLOT_FOR_FEATURE: Record<OptionalFeature, ModelKind> = {
  translator: "translator",
  chat: "chat",
  speaker: "speaker",
};

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
  ram_mb: number; // the memory floor; 0 when the catalogue states none
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

/** The slots that can be answered by a model over HTTP instead of one on
 *  disk. Speech recognition is not one and is not going to be — the live path
 *  is coupled to the VAD and to partials, so it is not request/response. The
 *  engine is the authority (`status.endpoints` has a key per slot); this is
 *  only for typing. */
export type EndpointSlot = "translator" | "chat";

/** One slot's HTTP endpoint, as the engine reports it.
 *
 *  The API key is never in here — only `has_api_key`. The form shows "saved"
 *  rather than round-tripping a secret through the browser every time the
 *  settings panel opens. */
export interface EndpointStatus {
  /** The slot is currently answered over HTTP (as opposed to merely having an
   *  endpoint on record from last time). */
  active: boolean;
  base_url: string;
  model: string;
  has_api_key: boolean;
  api_key_env: string;
  timeout_s: number;
  /** There is a `base_url` at all. `active` without this is a half-set slot. */
  configured: boolean;
  /** The endpoint is on loopback, so this is still local inference and the
   *  privacy claim is unchanged. The engine decides it — the loopback rule
   *  lives in one place, not two. */
  local: boolean;
}

/** What to send to `saveEndpoint`. Every field is optional and an omitted one
 *  keeps its stored value; that is what lets the form save without knowing the
 *  API key. `api_key: ""` explicitly clears it. */
export interface EndpointPatch {
  base_url?: string;
  model?: string;
  api_key?: string;
  api_key_env?: string;
  active?: boolean;
}

export interface ModelStatus {
  models: ModelStatusItem[];
  all_present: boolean;
  options: KindOptions[];
  selected: Record<string, string | null>;
  features: Features;
  /** Slots configured to reach a model over HTTP somewhere that is not this
   *  machine (`backend: openai_compatible` with a non-loopback `base_url`).
   *  Empty is the normal case, and the one "nothing leaves your device" is
   *  about — a model server on 127.0.0.1 is still local inference. */
  remote: ModelKind[];
  /** Per slot that can be pointed at a URL. A slot absent from here cannot be. */
  endpoints: Record<string, EndpointStatus>;
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

/** Switch optional features on or off. Omitted ones are left alone.
 *  Switching one on downloads nothing — follow with `startModelDownload`. */
export async function setFeatures(patch: Partial<Features>): Promise<Features> {
  const res = await fetch(`${BASE}/models/features`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(patch),
  });
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`features failed (${res.status}): ${text}`);
  }
  return ((await res.json()) as { features: Features }).features;
}

/** Remove a model's files from disk (developer tools). Recoverable: the
 *  catalogue still knows where each file came from. */
export async function deleteModel(modelId: string): Promise<{
  model: string;
  removed: string[];
  failed: { filename: string; error: string }[];
  freed_mb: number;
  slots: string[];
}> {
  const res = await fetch(`${BASE}/models/${encodeURIComponent(modelId)}`, {
    method: "DELETE",
  });
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`delete failed (${res.status}): ${text}`);
  }
  return (await res.json()) as {
    model: string;
    removed: string[];
    failed: { filename: string; error: string }[];
    freed_mb: number;
    slots: string[];
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

/** Point a slot at a model served over HTTP, or (`active: false`) put it back
 *  on a local model — which keeps the endpoint on record, so turning it on
 *  again is one click rather than re-typing a URL and a key. */
export async function saveEndpoint(
  kind: EndpointSlot,
  patch: EndpointPatch,
): Promise<{
  applies: "now" | "next_session";
  endpoint: EndpointStatus;
  remote: ModelKind[];
}> {
  const res = await fetch(`${BASE}/models/endpoint`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ kind, ...patch }),
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = (await res.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      /* not JSON */
    }
    throw new Error(detail);
  }
  return (await res.json()) as {
    applies: "now" | "next_session";
    endpoint: EndpointStatus;
    remote: ModelKind[];
  };
}

/** Ask the saved endpoint one tiny question. Tests what is stored, not what is
 *  typed — the key may only exist server-side — so save first, then test.
 *  A failed test comes back as `ok: false`, not as a thrown error: it is the
 *  answer the user asked for. */
export async function testEndpoint(
  kind: EndpointSlot,
): Promise<{ ok: boolean; url: string; reply?: string; error?: string }> {
  const res = await fetch(`${BASE}/models/endpoint/test`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ kind }),
  });
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`endpoint test failed (${res.status}): ${text}`);
  }
  return (await res.json()) as {
    ok: boolean;
    url: string;
    reply?: string;
    error?: string;
  };
}
