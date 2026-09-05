// The pass the engine starts on its own after a recording stops is a job this
// client never asked for. The session list is how it learns the job id; this
// holds that a `processing` session gets followed exactly once, and that
// nothing else does.
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/jobs", () => ({
  subscribeJob: vi.fn(() => () => {}),
  formatEta: () => "",
}));

const { useJobsStore } = await import("@/store/jobsStore");
const { useSessionStore } = await import("@/store/sessionStore");
const { subscribeJob } = await import("@/lib/jobs");
import type { SessionMeta } from "@/types";

const meta = (over: Partial<SessionMeta>): SessionMeta => ({
  id: "s1",
  title: "Standup",
  createdAt: "2026-01-01T00:00:00Z",
  durationS: 60,
  srcLang: "en",
  tgtLang: "zh",
  groupId: null,
  status: "ready",
  statusDetail: null,
  refinedAt: null,
  jobId: null,
  ...over,
});

beforeEach(() => {
  useJobsStore.setState({ jobs: {}, order: [] });
  vi.mocked(subscribeJob).mockClear();
});

describe("syncFromSessions", () => {
  it("follows a processing session's job, once", () => {
    const list = [meta({ status: "processing", jobId: "job-1" })];
    useJobsStore.getState().syncFromSessions(list);
    useJobsStore.getState().syncFromSessions(list);
    const { jobs, order } = useJobsStore.getState();
    expect(order).toEqual(["job-1"]);
    expect(jobs["job-1"]).toMatchObject({ kind: "refine", sessionId: "s1", label: "Standup" });
    expect(subscribeJob).toHaveBeenCalledTimes(1);
  });

  it("ignores sessions that are not processing, or have no job to follow", () => {
    useJobsStore.getState().syncFromSessions([
      meta({ status: "ready", jobId: "job-x" }),
      meta({ id: "s2", status: "failed" }),
      meta({ id: "s3", status: "processing", jobId: null }),
    ]);
    expect(useJobsStore.getState().order).toEqual([]);
  });

  it("runs whenever the session list is refreshed", () => {
    useSessionStore.setState({
      pastSessions: [meta({ status: "processing", jobId: "job-2" })],
    });
    expect(useJobsStore.getState().order).toEqual(["job-2"]);
  });
});
