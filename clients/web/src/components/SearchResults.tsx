import { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";

import { highlightRuns, search, type SearchResult, type SegmentHit } from "@/lib/search";
import { useSessionStore } from "@/store/sessionStore";
import { useT } from "@/i18n";

/** Wrap the matching runs of `text` so the eye lands on them. */
export function Highlight({ text, query }: { text: string; query: string }) {
  return (
    <>
      {highlightRuns(text, query).map((r, i) =>
        r.hit ? (
          <mark key={i} className="rounded-sm bg-brand-500/25 px-0.5 text-inherit">
            {r.text}
          </mark>
        ) : (
          <span key={i}>{r.text}</span>
        ),
      )}
    </>
  );
}

function clock(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

/**
 * What the sidebar shows while the search box has text: sessions whose
 * title matches, then the matching lines grouped by session. A line opens
 * its session and scrolls to it.
 */
export function SearchResults({ query, locked }: { query: string; locked: boolean }) {
  const t = useT();
  const openSessionAt = useSessionStore((s) => s.openSessionAt);
  const [result, setResult] = useState<SearchResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Debounced, cancellable: a keystroke supersedes the request in flight.
  useEffect(() => {
    const q = query.trim();
    if (!q) return;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      search(q, { signal: controller.signal, limit: 60 })
        .then((r) => {
          setResult(r);
          setError(null);
        })
        .catch((e: unknown) => {
          if (controller.signal.aborted) return;
          setError(e instanceof Error ? e.message : String(e));
        });
    }, 180);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [query]);

  const shown = result?.query === query.trim() ? result : null;
  if (error) {
    return <p className="px-3 py-4 text-xs text-destructive">{error}</p>;
  }
  if (!shown) {
    return (
      <div className="flex items-center gap-2 px-3 py-4 text-xs text-muted-foreground">
        <Loader2 className="size-3.5 animate-spin" />
        {t("search.searching")}
      </div>
    );
  }

  const groups: { sessionId: string; title: string; hits: SegmentHit[] }[] = [];
  for (const h of shown.segments) {
    let g = groups.find((x) => x.sessionId === h.sessionId);
    if (!g) {
      g = { sessionId: h.sessionId, title: h.sessionTitle, hits: [] };
      groups.push(g);
    }
    g.hits.push(h);
  }
  for (const g of groups) g.hits.sort((a, b) => a.ord - b.ord);

  if (shown.sessions.length === 0 && groups.length === 0) {
    return (
      <p className="px-3 py-6 text-center text-xs text-muted-foreground">
        {t("search.empty", { query: shown.query })}
      </p>
    );
  }

  return (
    <div className="space-y-3 px-1">
      {shown.sessions.length > 0 && (
        <section>
          <h3 className="px-2 pb-1 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
            {t("search.sessions")}
          </h3>
          {shown.sessions.map((s) => (
            <button
              key={s.id}
              type="button"
              disabled={locked}
              onClick={() => void openSessionAt(s.id, null)}
              className="block w-full truncate rounded-md px-2 py-1.5 text-left text-sm font-medium text-foreground hover:bg-accent/60 disabled:cursor-not-allowed disabled:opacity-40"
            >
              <Highlight text={s.title} query={shown.query} />
            </button>
          ))}
        </section>
      )}
      {groups.length > 0 && (
        <section>
          <h3 className="px-2 pb-1 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
            {t("search.lines", { count: shown.segments.length })}
          </h3>
          {groups.map((g) => (
            <div key={g.sessionId} className="mb-2">
              <div className="truncate px-2 py-1 text-[12px] font-semibold text-foreground">{g.title}</div>
              {g.hits.map((h) => (
                <button
                  key={h.segmentId}
                  type="button"
                  disabled={locked}
                  onClick={() => void openSessionAt(h.sessionId, h.segmentId)}
                  className="block w-full rounded-md px-2 py-1.5 text-left hover:bg-accent/60 disabled:cursor-not-allowed disabled:opacity-40"
                >
                  <div className="text-[10.5px] tabular-nums text-muted-foreground">
                    {clock(h.startedAt)}
                    {h.speaker ? ` · ${h.speaker}` : ""}
                  </div>
                  <div className="line-clamp-2 text-[12.5px] leading-snug text-foreground">
                    <Highlight text={h.origText} query={shown.query} />
                  </div>
                  {h.transText && (
                    <div className="line-clamp-2 text-[11.5px] leading-snug text-muted-foreground">
                      <Highlight text={h.transText} query={shown.query} />
                    </div>
                  )}
                </button>
              ))}
            </div>
          ))}
        </section>
      )}
    </div>
  );
}
