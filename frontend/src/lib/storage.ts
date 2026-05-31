// HTTP-backed session persistence. The backend SQLite store at
// ~/.wrenote/data.db owns the catalog; this module is just the thin
// fetch layer. All functions are async — the store calls them from
// useEffect-style callbacks (refreshPastSessions, loadSession).
import type { Segment, SessionMeta, StoredSession } from "../types";

const BACKEND_HOST =
  typeof window !== "undefined" ? window.location.hostname : "localhost";
const BASE = `http://${BACKEND_HOST}:8000`;

// snake_case row → camelCase frontend object
interface SessionRow {
  id: string;
  title: string;
  created_at: string;
  src_lang: string;
  tgt_lang: string;
  duration_s: number;
}

interface SegmentRow {
  segment_id: string;
  ord: number;
  started_at: number;
  ended_at: number;
  orig_text: string;
  orig_status: string;
  orig_lang: string | null;
  trans_text: string;
  trans_status: string;
  trans_lang: string | null;
  speaker: string | null;
}

function toMeta(row: SessionRow): SessionMeta {
  return {
    id: row.id,
    title: row.title,
    createdAt: row.created_at,
    durationS: row.duration_s,
    srcLang: row.src_lang,
    tgtLang: row.tgt_lang,
  };
}

function toSegment(row: SegmentRow): Segment {
  const orig = (row.orig_status === "partial" ? "partial" : "final") as
    | "partial"
    | "final";
  let trans: Segment["transStatus"] = "final";
  if (row.trans_status === "partial") trans = "partial";
  else if (row.trans_status === "skipped") trans = "skipped";
  else if (row.trans_status === "pending") trans = "pending";
  return {
    segmentId: row.segment_id,
    startedAt: row.started_at,
    endedAt: row.ended_at,
    origText: row.orig_text,
    origStatus: orig,
    origLang: row.orig_lang ?? undefined,
    transText: row.trans_text,
    transStatus: trans,
    transLang: row.trans_lang ?? undefined,
    speaker: row.speaker,
  };
}

export async function loadAllSessions(): Promise<SessionMeta[]> {
  try {
    const res = await fetch(`${BASE}/sessions`);
    if (!res.ok) {
      console.warn("loadAllSessions: HTTP", res.status);
      return [];
    }
    const json = (await res.json()) as { sessions: SessionRow[] };
    return json.sessions.map(toMeta);
  } catch (e) {
    console.warn("loadAllSessions: network failure", e);
    return [];
  }
}

export async function loadSession(id: string): Promise<StoredSession | null> {
  try {
    const res = await fetch(`${BASE}/sessions/${encodeURIComponent(id)}`);
    if (res.status === 404) return null;
    if (!res.ok) {
      console.warn("loadSession: HTTP", res.status);
      return null;
    }
    const row = (await res.json()) as SessionRow & { segments: SegmentRow[] };
    return {
      ...toMeta(row),
      segments: row.segments.map(toSegment),
    };
  } catch (e) {
    console.warn("loadSession: network failure", e);
    return null;
  }
}

export async function deleteSession(id: string): Promise<void> {
  try {
    await fetch(`${BASE}/sessions/${encodeURIComponent(id)}`, {
      method: "DELETE",
    });
  } catch (e) {
    console.warn("deleteSession: network failure", e);
  }
}

export async function renameSession(id: string, title: string): Promise<void> {
  try {
    await fetch(`${BASE}/sessions/${encodeURIComponent(id)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title }),
    });
  } catch (e) {
    console.warn("renameSession: network failure", e);
  }
}

export function newSessionId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `s-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}
