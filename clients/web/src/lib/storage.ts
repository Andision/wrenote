// HTTP-backed session persistence. The backend SQLite store at
// ~/.wrenote/data.db owns the catalog; this module is just the thin
// fetch layer. All functions are async — the store calls them from
// useEffect-style callbacks (refreshPastSessions, loadSession).
import type {
  Segment,
  SessionGroup,
  SessionMeta,
  SessionStatus,
  StoredSession,
} from "../types";
import { API_BASE as BASE } from "./api";

// snake_case row → camelCase frontend object
interface SessionRow {
  id: string;
  title: string;
  created_at: string;
  src_lang: string;
  tgt_lang: string;
  duration_s: number;
  group_id?: string | null;
  status?: string | null;
  status_detail?: string | null;
  refined_at?: string | null;
  job_id?: string | null;
}

interface GroupRow {
  id: string;
  name: string;
  created_at: string;
  position: number;
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

const STATUSES: readonly SessionStatus[] = ["recording", "processing", "ready", "failed"];

/** An engine we don't know the statuses of is one we'd rather treat as done
 *  than as busy: an unknown value reads as `ready`. */
export function toStatus(raw: string | null | undefined): SessionStatus {
  return (STATUSES as readonly string[]).includes(raw ?? "") ? (raw as SessionStatus) : "ready";
}

export function toMeta(row: SessionRow): SessionMeta {
  return {
    id: row.id,
    title: row.title,
    createdAt: row.created_at,
    durationS: row.duration_s,
    srcLang: row.src_lang,
    tgtLang: row.tgt_lang,
    groupId: row.group_id ?? null,
    status: toStatus(row.status),
    statusDetail: row.status_detail ?? null,
    refinedAt: row.refined_at ?? null,
    jobId: row.job_id ?? null,
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
  return (await loadSessions()).sessions;
}

/** One page of the library, newest first. `cursor` is what the previous
 *  page handed back; `limit` 0 asks for everything. */
export async function loadSessions(
  opts: { limit?: number; cursor?: string | null } = {},
): Promise<{ sessions: SessionMeta[]; nextCursor: string | null }> {
  const params = new URLSearchParams();
  if (opts.limit) params.set("limit", String(opts.limit));
  if (opts.cursor) params.set("cursor", opts.cursor);
  const qs = params.toString();
  try {
    const res = await fetch(`${BASE}/sessions${qs ? `?${qs}` : ""}`);
    if (!res.ok) {
      console.warn("loadSessions: HTTP", res.status);
      return { sessions: [], nextCursor: null };
    }
    const json = (await res.json()) as { sessions: SessionRow[]; next_cursor?: string | null };
    return { sessions: json.sessions.map(toMeta), nextCursor: json.next_cursor ?? null };
  } catch (e) {
    console.warn("loadSessions: network failure", e);
    return { sessions: [], nextCursor: null };
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

/**
 * Edit a segment's text. `origText` edits the transcription (its translation is
 * flagged stale server-side); `transText` is a manual translation override.
 */
export async function editSegment(
  sessionId: string,
  segmentId: string,
  patch: { origText?: string; transText?: string },
): Promise<void> {
  const body: Record<string, string> = {};
  if (patch.origText !== undefined) body.orig_text = patch.origText;
  if (patch.transText !== undefined) body.trans_text = patch.transText;
  await fetch(
    `${BASE}/sessions/${encodeURIComponent(sessionId)}/segments/${encodeURIComponent(segmentId)}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    },
  );
}

const segUrl = (sessionId: string, segmentId: string) =>
  `${BASE}/sessions/${encodeURIComponent(sessionId)}/segments/${encodeURIComponent(segmentId)}`;

/** Split a segment's original text at a character offset into two segments. */
export async function splitSegment(
  sessionId: string,
  segmentId: string,
  offset: number,
): Promise<void> {
  await fetch(`${segUrl(sessionId, segmentId)}/split`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ offset }),
  });
}

/** Merge a segment with the one that follows it. */
export async function mergeSegment(sessionId: string, segmentId: string): Promise<void> {
  await fetch(`${segUrl(sessionId, segmentId)}/merge`, { method: "POST" });
}

/**
 * Ask the backend to summarize a title from the session transcript (LLM).
 * Returns the new title, or null on failure. The backend persists it.
 */
export async function suggestSessionTitle(id: string): Promise<string | null> {
  try {
    const res = await fetch(
      `${BASE}/sessions/${encodeURIComponent(id)}/title/suggest`,
      { method: "POST" },
    );
    if (!res.ok) return null;
    const json = (await res.json()) as { title?: string };
    return json.title ?? null;
  } catch (e) {
    console.warn("suggestSessionTitle: network failure", e);
    return null;
  }
}

// ---------- Session groups ----------

function toGroup(row: GroupRow): SessionGroup {
  return {
    id: row.id,
    name: row.name,
    createdAt: row.created_at,
    position: row.position,
  };
}

export async function listGroups(): Promise<SessionGroup[]> {
  try {
    const res = await fetch(`${BASE}/groups`);
    if (!res.ok) return [];
    const json = (await res.json()) as { groups: GroupRow[] };
    return json.groups.map(toGroup);
  } catch (e) {
    console.warn("listGroups: network failure", e);
    return [];
  }
}

export async function createGroup(name = "New group"): Promise<SessionGroup | null> {
  try {
    const res = await fetch(`${BASE}/groups`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    if (!res.ok) return null;
    const json = (await res.json()) as { group: GroupRow };
    return toGroup(json.group);
  } catch (e) {
    console.warn("createGroup: network failure", e);
    return null;
  }
}

export async function renameGroup(id: string, name: string): Promise<void> {
  try {
    await fetch(`${BASE}/groups/${encodeURIComponent(id)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
  } catch (e) {
    console.warn("renameGroup: network failure", e);
  }
}

export async function deleteGroup(id: string): Promise<void> {
  try {
    await fetch(`${BASE}/groups/${encodeURIComponent(id)}`, { method: "DELETE" });
  } catch (e) {
    console.warn("deleteGroup: network failure", e);
  }
}

export async function setSessionGroup(
  sessionId: string,
  groupId: string | null,
): Promise<void> {
  try {
    await fetch(`${BASE}/sessions/${encodeURIComponent(sessionId)}/group`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ groupId }),
    });
  } catch (e) {
    console.warn("setSessionGroup: network failure", e);
  }
}

export function newSessionId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `s-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}
