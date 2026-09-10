// Tracks long-running backend jobs (upload, diarize, a recording's pass…).
// Two components read from here: the toasts bottom-right (ProgressOverlay)
// and the list bottom-left (TaskList).
//
// Dismissing hides the toast; it does not forget the job. It used to delete
// it, and `syncFromSessions` — which learns about the engine's own passes
// from the session list — put it straight back the next time that list was
// refreshed, i.e. on the next session switch. A dismissed job stays here,
// marked, so the list can still show it and nothing resurrects the toast.
//
// State persists to localStorage so a page refresh during a long-running job
// picks the progress UI back up — backend jobs survive disconnect (registry
// caps at 64) — and `dismissed` persists with it, so a refresh doesn't bring
// a hidden toast back either.
import { create } from "zustand";

import { subscribeJob, type JobSnapshot } from "@/lib/jobs";
import { useSessionStore } from "@/store/sessionStore";
import { isPassInFlight, type SessionMeta } from "@/types";

const STORAGE_KEY = "wrenote.activeJobs";
const LINGER_MS = 4000;
/** Finished jobs kept for the list. A session's history, not a permanent log. */
const KEEP_FINISHED = 20;

/** Kind tells us how to rebuild onDone after a refresh. */
export type JobKind = "upload" | "diarize" | "translate" | "refine" | "minutes";

/** What we persist — enough to re-track + reconstruct the completion side-effect. */
interface PersistedJob {
  jobId: string;
  label: string;
  kind: JobKind;
  /** Session this job is operating on; needed by both kinds' onDone. */
  sessionId: string;
  /** The user closed its toast. Persisted, or a refresh would re-raise it. */
  dismissed?: boolean;
}

export interface TrackedJob {
  id: string;
  label: string;
  kind: JobKind;
  sessionId: string;
  snapshot: JobSnapshot | null;
  /** Brief "we just finished" state — the toast holds the success frame
   * for a moment before hiding itself. */
  lingerUntil: number | null;
  /** Hidden from the toasts. Still in the list — that is the point. */
  dismissed: boolean;
  /** When we started tracking it, so the list can order and trim by age. */
  startedAt: number;
}

interface JobsState {
  jobs: Record<string, TrackedJob>;
  order: string[];

  /** Start tracking a job and open its SSE stream. */
  track: (params: {
    jobId: string;
    label: string;
    kind: JobKind;
    sessionId: string;
  }) => void;
  /** Hide a job's toast. It stays in the list. */
  dismiss: (jobId: string) => void;
  /** Drop a job entirely — the list's per-row remove. */
  forget: (jobId: string) => void;
  /** Drop every finished job from the list. */
  clearFinished: () => void;
  /** Keep only the newest KEEP_FINISHED finished jobs. */
  trimFinished: () => void;
  /** Called once at app mount: re-track every persisted job. */
  hydrateFromStorage: () => void;
  /** Follow the jobs the engine reports on sessions in `processing` — the
   * pass it starts by itself after a recording stops is one this client
   * never asked for, so the session list is how it learns the job id. */
  syncFromSessions: (sessions: SessionMeta[]) => void;
}

function readPersisted(): PersistedJob[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(
      (x): x is PersistedJob =>
        x && typeof x.jobId === "string" && typeof x.label === "string",
    );
  } catch {
    return [];
  }
}

function writePersisted(list: PersistedJob[]): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(list));
  } catch {
    // localStorage quota — silently drop, this is non-critical.
  }
}

/** Kind-specific work that fires when a job's status flips to "done".
 * Reconstructable from the persisted record alone, so it survives refresh. */
async function runOnDone(job: TrackedJob, snap: JobSnapshot): Promise<void> {
  if (snap.status !== "done" || !snap.result) return;
  const store = useSessionStore.getState();
  if (job.kind === "upload") {
    await store.refreshPastSessions();
    const newSid = (snap.result.session_id as string | undefined) ?? job.sessionId;
    if (newSid) await store.loadSession(newSid);
  } else if (job.kind === "diarize") {
    // Diarize can now rewrite speaker-aware segment boundaries, so reload
    // the whole session instead of patching labels in place.
    if (store.sessionId === job.sessionId) {
      await store.loadSession(job.sessionId);
    }
  } else if (job.kind === "translate") {
    // Reload the session from backend so the newly-filled translations
    // appear in the UI. Only refresh if user is still on that session.
    if (store.sessionId === job.sessionId) {
      await store.loadSession(job.sessionId);
    }
  } else if (job.kind === "refine") {
    // The transcript was replaced wholesale: swap the live rows for the
    // refined ones if the user is still looking at them, refresh the list
    // so the session's status badge clears, and — now that the text is the
    // good one — title the session if it is still waiting for a title.
    if (store.sessionId === job.sessionId) {
      await store.loadSession(job.sessionId);
      store.autoTitleAfterRecording();
    }
    await store.refreshPastSessions();
  }
}

/** A job that died may have left its session `failed`; the list is where
 * that shows, so refresh it. */
async function runOnError(job: TrackedJob): Promise<void> {
  if (job.kind === "refine" || job.kind === "upload") {
    await useSessionStore.getState().refreshPastSessions();
  }
}

export const useJobsStore = create<JobsState>((set, get) => {
  /** Sync the in-memory jobs map back to localStorage. */
  const persist = () => {
    const { jobs, order } = get();
    const out: PersistedJob[] = [];
    for (const id of order) {
      const j = jobs[id];
      if (!j) continue;
      // Stop persisting once the job is terminal — the linger window is
      // visual only and we don't want stale rows to come back on refresh.
      const status = j.snapshot?.status;
      if (status === "done" || status === "error") continue;
      out.push({
        jobId: j.id,
        label: j.label,
        kind: j.kind,
        sessionId: j.sessionId,
        dismissed: j.dismissed,
      });
    }
    writePersisted(out);
  };

  /** Open the SSE stream + wire callbacks. Shared by track() and rehydrate(). */
  const subscribe = (job: TrackedJob): void => {
    subscribeJob(job.id, {
      onSnapshot: (snap) => {
        set((s) => {
          const cur = s.jobs[job.id];
          if (!cur) return {};
          const next: TrackedJob = { ...cur, snapshot: snap };
          if (snap.status !== "running" && cur.lingerUntil == null) {
            next.lingerUntil = Date.now() + LINGER_MS;
            if (snap.status === "done") {
              void runOnDone(cur, snap);
            } else if (snap.status === "error") {
              void runOnError(cur);
            }
            window.setTimeout(() => {
              const s2 = useJobsStore.getState();
              const live = s2.jobs[job.id];
              if (live && live.snapshot?.status !== "running") {
                // Hide the toast, keep the row: the list is where a finished
                // job is still findable. Trim the oldest finished ones so it
                // stays a recent history rather than a log.
                s2.dismiss(job.id);
                s2.trimFinished();
              }
            }, LINGER_MS + 100);
          }
          return { jobs: { ...s.jobs, [job.id]: next } };
        });
        persist();
      },
      onError: () => {
        // Most likely the backend has GC'd the job (refresh after long
        // delay). Silently drop — no UI for an error we can't recover from.
        useJobsStore.getState().dismiss(job.id);
      },
    });
  };

  return {
    jobs: {},
    order: [],

    track: ({ jobId, label, kind, sessionId }) => {
      if (get().jobs[jobId]) return;
      const tracked: TrackedJob = {
        id: jobId,
        label,
        kind,
        sessionId,
        snapshot: null,
        lingerUntil: null,
        dismissed: false,
        startedAt: Date.now(),
      };
      set((s) => ({
        jobs: { ...s.jobs, [jobId]: tracked },
        order: [...s.order, jobId],
      }));
      persist();
      subscribe(tracked);
    },

    dismiss: (jobId) => {
      set((s) => {
        const cur = s.jobs[jobId];
        if (!cur || cur.dismissed) return {};
        return { jobs: { ...s.jobs, [jobId]: { ...cur, dismissed: true } } };
      });
      persist();
    },

    forget: (jobId) => {
      set((s) => {
        if (!s.jobs[jobId]) return {};
        const rest = { ...s.jobs };
        delete rest[jobId];
        return { jobs: rest, order: s.order.filter((id) => id !== jobId) };
      });
      persist();
    },

    trimFinished: () => {
      set((s) => {
        const finished = s.order.filter((id) => {
          const st = s.jobs[id]?.snapshot?.status;
          return st === "done" || st === "error";
        });
        if (finished.length <= KEEP_FINISHED) return {};
        const drop = new Set(finished.slice(0, finished.length - KEEP_FINISHED));
        const order = s.order.filter((id) => !drop.has(id));
        const jobs: Record<string, TrackedJob> = {};
        for (const id of order) jobs[id] = s.jobs[id];
        return { jobs, order };
      });
    },

    clearFinished: () => {
      set((s) => {
        const keep = s.order.filter((id) => {
          const st = s.jobs[id]?.snapshot?.status;
          return st !== "done" && st !== "error";
        });
        const jobs: Record<string, TrackedJob> = {};
        for (const id of keep) jobs[id] = s.jobs[id];
        return { jobs, order: keep };
      });
      persist();
    },

    hydrateFromStorage: () => {
      const persisted = readPersisted();
      if (persisted.length === 0) return;
      for (const p of persisted) {
        if (get().jobs[p.jobId]) continue;
        const tracked: TrackedJob = {
          id: p.jobId,
          label: p.label,
          kind: p.kind,
          sessionId: p.sessionId,
          snapshot: null,
          lingerUntil: null,
          dismissed: Boolean(p.dismissed),
          startedAt: Date.now(),
        };
        set((s) => ({
          jobs: { ...s.jobs, [p.jobId]: tracked },
          order: [...s.order, p.jobId],
        }));
        subscribe(tracked);
      }
    },

    syncFromSessions: (sessions) => {
      for (const s of sessions) {
        // Queued counts: the job exists and has a progress stream from the
        // moment it is created, it just hasn't taken its pass slot yet.
        if (!isPassInFlight(s.status) || !s.jobId || get().jobs[s.jobId]) continue;
        // The label is the session title; the overlay words it per kind
        // (this runs outside React, so no `t()` here).
        get().track({ jobId: s.jobId, label: s.title, kind: "refine", sessionId: s.id });
      }
    },
  };
});

// Whenever the session list is refreshed (after a recording stops, on
// mount, after any job), pick up the passes the engine is running.
useSessionStore.subscribe((state, prev) => {
  if (state.pastSessions !== prev.pastSessions) {
    useJobsStore.getState().syncFromSessions(state.pastSessions);
  }
});
