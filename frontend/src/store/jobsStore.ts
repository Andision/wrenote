// Tracks long-running backend jobs (upload, diarize). The ProgressOverlay
// component reads from here. State persists to localStorage so a page
// refresh during a long-running job picks the progress UI back up
// automatically — backend jobs survive disconnect (registry caps at 64).
import { create } from "zustand";

import { subscribeJob, type JobSnapshot } from "@/lib/jobs";
import { useSessionStore } from "@/store/sessionStore";

const STORAGE_KEY = "interpreter.activeJobs";
const LINGER_MS = 4000;

/** Kind tells us how to rebuild onDone after a refresh. */
type JobKind = "upload" | "diarize" | "translate";

/** What we persist — enough to re-track + reconstruct the completion side-effect. */
interface PersistedJob {
  jobId: string;
  label: string;
  kind: JobKind;
  /** Session this job is operating on; needed by both kinds' onDone. */
  sessionId: string;
}

export interface TrackedJob {
  id: string;
  label: string;
  kind: JobKind;
  sessionId: string;
  snapshot: JobSnapshot | null;
  /** Brief "we just finished" state — overlay holds the success frame
   * for a moment before removing the row. */
  lingerUntil: number | null;
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
  dismiss: (jobId: string) => void;
  /** Called once at app mount: re-track every persisted job. */
  hydrateFromStorage: () => void;
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
            }
            window.setTimeout(() => {
              const live = useJobsStore.getState().jobs[job.id];
              if (live && live.snapshot?.status !== "running") {
                useJobsStore.getState().dismiss(job.id);
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
        if (!s.jobs[jobId]) return {};
        const { [jobId]: _, ...rest } = s.jobs;
        return {
          jobs: rest,
          order: s.order.filter((id) => id !== jobId),
        };
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
        };
        set((s) => ({
          jobs: { ...s.jobs, [p.jobId]: tracked },
          order: [...s.order, p.jobId],
        }));
        subscribe(tracked);
      }
    },
  };
});
