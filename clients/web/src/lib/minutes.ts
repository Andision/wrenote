import { API_BASE as BASE } from "./api";
// Meeting minutes the engine's chat model writes for a session (engine
// core/minutes.py). One document per language; generation is a job.

export interface ActionItem {
  text: string;
  owner: string | null;
  due: string | null;
}

export interface MinutesDoc {
  summary: string;
  key_points: string[];
  decisions: string[];
  action_items: ActionItem[];
  open_questions: string[];
}

export interface Minutes {
  lang: string;
  content: MinutesDoc;
  generatedAt: string;
  model: string;
  /** The transcript changed after these were written. */
  stale: boolean;
}

export interface MinutesState {
  minutes: Minutes[];
  /** A job writing minutes right now, if any, and the language it is for. */
  jobId: string | null;
  jobLang: string | null;
}

interface MinutesRow {
  lang: string;
  content: MinutesDoc;
  generated_at: string;
  model: string;
  stale: boolean;
}

const sid = (s: string) => encodeURIComponent(s);

export async function getMinutes(sessionId: string): Promise<MinutesState> {
  const res = await fetch(`${BASE}/sessions/${sid(sessionId)}/minutes`);
  if (!res.ok) throw new Error(`minutes failed (${res.status})`);
  const json = (await res.json()) as {
    minutes: MinutesRow[];
    job_id: string | null;
    job_lang: string | null;
  };
  return {
    minutes: json.minutes.map((r) => ({
      lang: r.lang,
      content: r.content,
      generatedAt: r.generated_at,
      model: r.model,
      stale: r.stale,
    })),
    jobId: json.job_id,
    jobLang: json.job_lang,
  };
}

export class MinutesRefusedError extends Error {
  readonly code: string;
  constructor(code: string) {
    super(`minutes refused: ${code}`);
    this.code = code;
  }
}

export async function startMinutes(sessionId: string, lang: string): Promise<{ jobId: string }> {
  const res = await fetch(`${BASE}/sessions/${sid(sessionId)}/minutes`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ lang }),
  });
  if (!res.ok) {
    const detail = await res
      .json()
      .then((j: { detail?: unknown }) => String(j.detail ?? ""))
      .catch(() => res.statusText);
    throw new MinutesRefusedError(detail || `http_${res.status}`);
  }
  const json = (await res.json()) as { job_id: string };
  return { jobId: json.job_id };
}

export async function fetchMinutesMarkdown(sessionId: string, lang: string): Promise<string> {
  const res = await fetch(
    `${BASE}/sessions/${sid(sessionId)}/minutes/markdown?lang=${encodeURIComponent(lang)}`,
  );
  if (!res.ok) throw new Error(`minutes export failed (${res.status})`);
  return res.text();
}
