// The bottom-left task record: what it shows once a toast has been dismissed,
// and the way from a job to the session it belongs to.
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/jobs", () => ({
  subscribeJob: vi.fn(() => () => {}),
  formatEta: () => "about a minute",
}));

const { TaskList } = await import("@/components/TaskList");
const { I18nProvider } = await import("@/i18n/provider");
const { useJobsStore } = await import("@/store/jobsStore");
type TrackedJob = import("@/store/jobsStore").TrackedJob;
const { useSessionStore } = await import("@/store/sessionStore");

import type { JobSnapshot } from "@/lib/jobs";

const snap = (over: Partial<JobSnapshot> = {}): JobSnapshot => ({
  id: "j1", kind: "refine", status: "running", phase: "transcribe", phase_idx: 1,
  phase_count: 5, fraction: 0.4, elapsed_s: 1, eta_s: 60, log: [], error: null,
  result: null, ...over,
});

const job = (over: Partial<TrackedJob> = {}): TrackedJob => ({
  id: "j1", label: "Standup", kind: "refine", sessionId: "s1",
  snapshot: snap(), lingerUntil: null, dismissed: false, startedAt: 1, ...over,
});

const seed = (...jobs: TrackedJob[]) => {
  useJobsStore.setState({
    order: jobs.map((j) => j.id),
    jobs: Object.fromEntries(jobs.map((j) => [j.id, j])),
  });
};

const show = () => render(<I18nProvider><TaskList /></I18nProvider>);

describe("TaskList", () => {
  beforeEach(() => {
    useJobsStore.setState({ jobs: {}, order: [] });
    useSessionStore.setState({ sessionId: "s1" });
  });

  it("shows nothing when nothing has ever been tracked", () => {
    const { container } = show();
    expect(container.innerHTML).toBe("");
  });

  it("counts what is running, and still lists a dismissed job", () => {
    seed(job({ dismissed: true }));
    show();
    expect(screen.getByText("1 running")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Background tasks/ }));
    // The whole point: the toast is gone, the record is not.
    expect(screen.getByText("Transcribe recording: Standup")).toBeTruthy();
  });

  it("says so when everything has finished", () => {
    seed(job({ snapshot: snap({ status: "done", fraction: 1 }) }));
    show();
    expect(screen.getByText("No tasks running")).toBeTruthy();
  });

  it("offers the way to a job on another session, and not on this one", () => {
    const loadSession = vi.fn();
    useSessionStore.setState({ sessionId: "s1", loadSession });
    seed(job({ sessionId: "s2" }));
    show();
    fireEvent.click(screen.getByRole("button", { name: /Background tasks/ }));
    fireEvent.click(screen.getByRole("button", { name: "Go to this session" }));
    expect(loadSession).toHaveBeenCalledWith("s2");
  });

  it("has no jump button for the session you are already on", () => {
    seed(job({ sessionId: "s1" }));
    show();
    fireEvent.click(screen.getByRole("button", { name: /Background tasks/ }));
    expect(screen.queryByRole("button", { name: "Go to this session" })).toBeNull();
  });

  it("removes a finished row on request, but not a running one", () => {
    seed(
      job({ id: "j1", snapshot: snap({ status: "done" }) }),
      job({ id: "j2", label: "Other" }),
    );
    show();
    fireEvent.click(screen.getByRole("button", { name: /Background tasks/ }));
    const removes = screen.getAllByRole("button", { name: "Remove from the list" });
    expect(removes).toHaveLength(1); // only the finished one
    fireEvent.click(removes[0]);
    expect(useJobsStore.getState().order).toEqual(["j2"]);
  });
});
