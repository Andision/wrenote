// Offline diarization + speaker-rename HTTP helpers. Diarize is now
// async — returns a job id; progress is streamed via jobsStore.

// Same-origin: the SPA is served by the backend, so talk to our own origin
// (port included). Vite dev proxies these paths to the backend — see vite.config.ts.
const BASE =
  typeof window !== "undefined" ? window.location.origin : "http://localhost:8000";

export interface DiarizeStarted {
  jobId: string;
}

export async function startDiarize(sessionId: string): Promise<DiarizeStarted> {
  const res = await fetch(
    `${BASE}/sessions/${encodeURIComponent(sessionId)}/diarize`,
    { method: "POST" },
  );
  if (!res.ok) {
    const detail = await res.text().catch(() => res.statusText);
    throw new Error(`Diarize failed (${res.status}): ${detail}`);
  }
  const json = (await res.json()) as { job_id: string };
  return { jobId: json.job_id };
}

export interface TranslateStarted {
  jobId: string;
}

export async function startTranslate(
  sessionId: string,
  tgtLang?: string,
  retranslate?: boolean,
): Promise<TranslateStarted> {
  const body: Record<string, unknown> = {};
  if (tgtLang) body.tgt_lang = tgtLang;
  if (retranslate) body.retranslate = true;
  const res = await fetch(
    `${BASE}/sessions/${encodeURIComponent(sessionId)}/translate`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    },
  );
  if (!res.ok) {
    const detail = await res.text().catch(() => res.statusText);
    throw new Error(`Translate failed (${res.status}): ${detail}`);
  }
  const json = (await res.json()) as { job_id: string };
  return { jobId: json.job_id };
}

export async function renameSpeaker(
  sessionId: string,
  from: string,
  to: string,
): Promise<number> {
  const res = await fetch(
    `${BASE}/sessions/${encodeURIComponent(sessionId)}/speakers`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ from, to }),
    },
  );
  if (!res.ok) {
    const detail = await res.text().catch(() => res.statusText);
    throw new Error(`Rename failed (${res.status}): ${detail}`);
  }
  const json = (await res.json()) as { updated: number };
  return json.updated;
}

/**
 * Assign a speaker to specific segments (not a session-wide cascade). Used to
 * label segments diarization left unidentified.
 */
export async function assignSpeaker(
  sessionId: string,
  segmentIds: string[],
  speaker: string,
): Promise<number> {
  const res = await fetch(
    `${BASE}/sessions/${encodeURIComponent(sessionId)}/segments/speaker`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ segmentIds, speaker }),
    },
  );
  if (!res.ok) {
    const detail = await res.text().catch(() => res.statusText);
    throw new Error(`Assign failed (${res.status}): ${detail}`);
  }
  const json = (await res.json()) as { updated: number };
  return json.updated;
}
