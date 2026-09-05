import { API_BASE as BASE } from "./api";
// The whole-recording pass (engine core/refine.py): re-transcribe a finished
// session from its WAV and replace the transcript. Runs on its own after a
// recording stops; this is the manual trigger. Progress streams via jobsStore.

export interface RefineStarted {
  jobId: string;
}

/** Engine reason codes the endpoint answers with (its `detail`). */
export type RefineRefusal =
  | "busy"
  | "recording"
  | "no_recording"
  | "unsupported_backend"
  | "no_model";

const REFUSALS: readonly string[] = ["busy", "recording", "no_recording", "unsupported_backend", "no_model"];

export function isRefineRefusal(code: string): code is RefineRefusal {
  return REFUSALS.includes(code);
}

export class RefineRefusedError extends Error {
  readonly code: RefineRefusal | string;
  constructor(code: RefineRefusal | string) {
    super(`refine refused: ${code}`);
    this.code = code;
  }
}

export async function startRefine(
  sessionId: string,
  translate?: boolean,
): Promise<RefineStarted> {
  const res = await fetch(`${BASE}/sessions/${encodeURIComponent(sessionId)}/refine`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(translate === undefined ? {} : { translate }),
  });
  if (!res.ok) {
    const detail = await res
      .json()
      .then((j: { detail?: unknown }) => String(j.detail ?? ""))
      .catch(() => res.statusText);
    throw new RefineRefusedError(detail || `http_${res.status}`);
  }
  const json = (await res.json()) as { job_id: string };
  return { jobId: json.job_id };
}
