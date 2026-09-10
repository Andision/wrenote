// What the user sees while the engine re-transcribes their recording, and
// after that fails: the strip above the transcript, and the sidebar badge.
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/jobs", () => ({
  subscribeJob: vi.fn(() => () => {}),
  formatEta: () => "",
}));

const { ProcessingBanner, StatusBadge } = await import("@/components/SessionStatus");
const { I18nProvider } = await import("@/i18n/provider");
const { useJobsStore } = await import("@/store/jobsStore");
const { useSessionStore } = await import("@/store/sessionStore");
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
  useSessionStore.setState({ sessionId: "s1", pastSessions: [] });
});

describe("ProcessingBanner", () => {
  it("shows nothing for a ready session", () => {
    useSessionStore.setState({ pastSessions: [meta({})] });
    const { container } = render(<I18nProvider><ProcessingBanner onRetry={() => {}} /></I18nProvider>);
    expect(container.innerHTML).toBe("");
  });

  it("says a pass is queued, without pretending to have progress", () => {
    // A queued pass on 0% for as long as the recording ahead takes is what
    // made this look stuck, so `pending` gets words and no bar.
    useSessionStore.setState({ pastSessions: [meta({ status: "pending", jobId: "j1" })] });
    render(<I18nProvider><ProcessingBanner onRetry={() => {}} /></I18nProvider>);
    const text = screen.getByRole("status").textContent ?? "";
    expect(text).toMatch(/queued for transcription/i);
    expect(text).not.toMatch(/%/);
  });

  it("badges a queued session differently from a running one", () => {
    const queued = render(<I18nProvider><StatusBadge status="pending" /></I18nProvider>);
    expect(queued.container.textContent).toBe("Queued");
    queued.unmount();
    const running = render(<I18nProvider><StatusBadge status="processing" /></I18nProvider>);
    expect(running.container.textContent).toBe("Processing");
  });

  it("shows the pass in progress with its progress", () => {
    useSessionStore.setState({ pastSessions: [meta({ status: "processing", jobId: "j1" })] });
    useJobsStore.setState({
      order: ["j1"],
      jobs: {
        j1: {
          id: "j1", label: "Standup", kind: "refine", sessionId: "s1", lingerUntil: null,
          dismissed: false, startedAt: 0,
          snapshot: {
            id: "j1", kind: "refine", status: "running", phase: "transcribe", phase_idx: 1,
            phase_count: 5, fraction: 0.42, elapsed_s: 3, eta_s: 4, log: [], error: null, result: null,
          },
        },
      },
    });
    render(<I18nProvider><ProcessingBanner onRetry={() => {}} /></I18nProvider>);
    const text = screen.getByRole("status").textContent ?? "";
    expect(text).toMatch(/transcribing the full recording/i);
    expect(text).toContain("42%");
  });

  it("shows the failure, its reason, and a retry that asks for the pass again", () => {
    useSessionStore.setState({
      pastSessions: [meta({ status: "failed", statusDetail: "interrupted" })],
    });
    const onRetry = vi.fn();
    render(<I18nProvider><ProcessingBanner onRetry={onRetry} /></I18nProvider>);
    expect(screen.getByRole("alert").textContent).toContain("interrupted");
    fireEvent.click(screen.getByRole("button", { name: /retry/i }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });
});

describe("StatusBadge", () => {
  it("marks only the states worth a word", () => {
    const { container, rerender } = render(<I18nProvider><StatusBadge status="ready" /></I18nProvider>);
    expect(container.innerHTML).toBe("");
    rerender(<I18nProvider><StatusBadge status="processing" /></I18nProvider>);
    expect(container.textContent).toMatch(/processing/i);
    rerender(<I18nProvider><StatusBadge status="failed" /></I18nProvider>);
    expect(container.textContent).toMatch(/failed/i);
  });
});
