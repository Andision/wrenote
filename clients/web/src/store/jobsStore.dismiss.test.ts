// Dismissing a job hides its toast; it does not forget the job.
//
// It used to delete it, and `syncFromSessions` — which is how the client
// learns about the passes the engine starts by itself — put it straight back
// the next time the session list was refreshed, i.e. on the next session
// switch. So a dismissed toast reappeared, over and over.
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/jobs", () => ({
  subscribeJob: vi.fn(() => () => {}),
  formatEta: () => "",
}));

const { useJobsStore } = await import("@/store/jobsStore");
const { useSessionStore } = await import("@/store/sessionStore");

const meta = (over: Partial<import("@/types").SessionMeta> = {}) => ({
  id: "s1", title: "Standup", createdAt: "2026-01-01T00:00:00Z", durationS: 60,
  srcLang: "en", tgtLang: "zh", groupId: null, status: "processing" as const,
  statusDetail: null, refinedAt: null, jobId: "j1", ...over,
});

const snap = (status: "running" | "done" = "running") => ({
  id: "j1", kind: "refine", status, phase: "transcribe", phase_idx: 1,
  phase_count: 5, fraction: 0.4, elapsed_s: 1, eta_s: 2, log: [], error: null,
  result: status === "done" ? {} : null,
});

describe("dismissing a job", () => {
  beforeEach(() => {
    localStorage.clear();
    useJobsStore.setState({ jobs: {}, order: [] });
  });

  it("keeps it in the list, so it is still findable", () => {
    useJobsStore.getState().track({
      jobId: "j1", label: "Standup", kind: "refine", sessionId: "s1",
    });
    useJobsStore.getState().dismiss("j1");
    const s = useJobsStore.getState();
    expect(s.order).toEqual(["j1"]);
    expect(s.jobs.j1.dismissed).toBe(true);
  });

  it("survives the session-list refresh that used to resurrect it", () => {
    useJobsStore.getState().syncFromSessions([meta()]);
    expect(useJobsStore.getState().order).toEqual(["j1"]);
    useJobsStore.getState().dismiss("j1");

    // What switching sessions does: the list is refetched and re-synced.
    useJobsStore.getState().syncFromSessions([meta()]);
    useJobsStore.getState().syncFromSessions([meta()]);
    expect(useJobsStore.getState().jobs.j1.dismissed).toBe(true);
  });

  it("persists, so a refresh doesn't re-raise it either", () => {
    useJobsStore.getState().track({
      jobId: "j1", label: "Standup", kind: "refine", sessionId: "s1",
    });
    useJobsStore.getState().dismiss("j1");

    useJobsStore.setState({ jobs: {}, order: [] });
    useJobsStore.getState().hydrateFromStorage();
    expect(useJobsStore.getState().jobs.j1.dismissed).toBe(true);
  });

  it("follows a queued pass too — the job exists before it starts", () => {
    useJobsStore.getState().syncFromSessions([meta({ status: "pending" })]);
    expect(useJobsStore.getState().order).toEqual(["j1"]);
  });

  it("forget drops it outright; clearFinished drops only what is done", () => {
    const store = useJobsStore.getState();
    store.track({ jobId: "j1", label: "a", kind: "refine", sessionId: "s1" });
    store.track({ jobId: "j2", label: "b", kind: "refine", sessionId: "s2" });
    useJobsStore.setState((s) => ({
      jobs: { ...s.jobs, j2: { ...s.jobs.j2, snapshot: snap("done") } },
    }));

    useJobsStore.getState().clearFinished();
    expect(useJobsStore.getState().order).toEqual(["j1"]);

    useJobsStore.getState().forget("j1");
    expect(useJobsStore.getState().order).toEqual([]);
  });
});

describe("syncFromSessions", () => {
  beforeEach(() => {
    localStorage.clear();
    useJobsStore.setState({ jobs: {}, order: [] });
    useSessionStore.setState({ pastSessions: [] });
  });

  it("ignores a session with no job and one that is ready", () => {
    useJobsStore.getState().syncFromSessions([
      meta({ id: "a", jobId: null }),
      meta({ id: "b", status: "ready" }),
    ]);
    expect(useJobsStore.getState().order).toEqual([]);
  });
});
