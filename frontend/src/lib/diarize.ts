// Offline diarization + speaker-rename HTTP helpers. Diarize is now
// async — returns a job id; progress is streamed via jobsStore.

const BACKEND_HOST =
  typeof window !== "undefined" ? window.location.hostname : "localhost";
const BASE = `http://${BACKEND_HOST}:8000`;

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
): Promise<TranslateStarted> {
  const res = await fetch(
    `${BASE}/sessions/${encodeURIComponent(sessionId)}/translate`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(tgtLang ? { tgt_lang: tgtLang } : {}),
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
