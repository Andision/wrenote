import { API_BASE as BASE } from "./api";
import { toMeta } from "./storage";
import type { SessionMeta } from "../types";
// Full-text search over the library (engine core/search.py): segments whose
// original or translation contains the query, plus sessions whose title does.

export interface SegmentHit {
  sessionId: string;
  sessionTitle: string;
  sessionCreatedAt: string;
  segmentId: string;
  ord: number;
  startedAt: number;
  speaker: string | null;
  origText: string;
  transText: string;
}

export interface SearchResult {
  query: string;
  segments: SegmentHit[];
  sessions: SessionMeta[];
}

interface HitRow {
  session_id: string;
  session_title: string;
  session_created_at: string;
  segment_id: string;
  ord: number;
  started_at: number;
  speaker: string | null;
  orig_text: string;
  trans_text: string;
}

export async function search(
  q: string,
  opts: { sessionId?: string; limit?: number; signal?: AbortSignal } = {},
): Promise<SearchResult> {
  const params = new URLSearchParams({ q });
  if (opts.sessionId) params.set("session_id", opts.sessionId);
  if (opts.limit) params.set("limit", String(opts.limit));
  const res = await fetch(`${BASE}/search?${params}`, { signal: opts.signal });
  if (!res.ok) throw new Error(`search failed (${res.status})`);
  const json = (await res.json()) as {
    query: string;
    segments: HitRow[];
    sessions: Parameters<typeof toMeta>[0][];
  };
  return {
    query: json.query,
    segments: json.segments.map((r) => ({
      sessionId: r.session_id,
      sessionTitle: r.session_title,
      sessionCreatedAt: r.session_created_at,
      segmentId: r.segment_id,
      ord: r.ord,
      startedAt: r.started_at,
      speaker: r.speaker,
      origText: r.orig_text,
      transText: r.trans_text,
    })),
    sessions: json.sessions.map(toMeta),
  };
}

/** Split `text` into plain and matching runs of `query`, case-insensitively,
 *  so a component can wrap the matches. Whole text as one plain run when the
 *  query is empty or absent. */
export function highlightRuns(text: string, query: string): { text: string; hit: boolean }[] {
  const q = query.trim();
  if (!q) return [{ text, hit: false }];
  const lower = text.toLowerCase();
  const needle = q.toLowerCase();
  const runs: { text: string; hit: boolean }[] = [];
  let i = 0;
  for (;;) {
    const at = lower.indexOf(needle, i);
    if (at < 0) break;
    if (at > i) runs.push({ text: text.slice(i, at), hit: false });
    runs.push({ text: text.slice(at, at + needle.length), hit: true });
    i = at + needle.length;
  }
  if (i < text.length) runs.push({ text: text.slice(i), hit: false });
  return runs.length ? runs : [{ text, hit: false }];
}
