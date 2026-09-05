// The minutes panel: what it shows before minutes exist, the document once
// they do (with the stale notice), and that "Write minutes" asks the engine
// for the active language and follows the job.
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/jobs", () => ({
  subscribeJob: vi.fn(() => () => {}),
  formatEta: () => "",
}));
vi.mock("@/lib/minutes", async (orig) => ({
  ...(await orig<typeof import("@/lib/minutes")>()),
  getMinutes: vi.fn(),
  startMinutes: vi.fn(),
  fetchMinutesMarkdown: vi.fn(),
}));

const minutesLib = await import("@/lib/minutes");
const { MinutesBody } = await import("@/components/MinutesPanel");
const { I18nProvider } = await import("@/i18n/provider");
const { useJobsStore } = await import("@/store/jobsStore");
const { useSessionStore } = await import("@/store/sessionStore");
import type { Minutes } from "@/lib/minutes";
import type { SessionMeta } from "@/types";

const meta: SessionMeta = {
  id: "s1", title: "Planning", createdAt: "2026-01-01T00:00:00Z", durationS: 60,
  srcLang: "en", tgtLang: "zh", groupId: null, status: "ready", statusDetail: null,
  refinedAt: null, jobId: null,
};

const doc = (over: Partial<Minutes> = {}): Minutes => ({
  lang: "zh",
  content: {
    summary: "A short planning call.",
    key_points: ["Launch date"],
    decisions: ["Launch on March 3rd"],
    action_items: [{ text: "Write the release notes", owner: "Bob", due: null }],
    open_questions: [],
  },
  generatedAt: "2026-01-01T01:00:00Z",
  model: "qwen",
  stale: false,
  ...over,
});

const setup = () => render(<I18nProvider><MinutesBody sessionId="s1" /></I18nProvider>);

beforeEach(() => {
  useJobsStore.setState({ jobs: {}, order: [] });
  useSessionStore.setState({
    sessionId: "s1", sessionTitle: "Planning", connection: "disconnected",
    segmentOrder: ["a"], pastSessions: [meta],
  });
  vi.mocked(minutesLib.getMinutes).mockReset();
  vi.mocked(minutesLib.startMinutes).mockReset();
});

describe("MinutesBody", () => {
  it("offers to write minutes when there are none, for the target language", async () => {
    vi.mocked(minutesLib.getMinutes).mockResolvedValue({ minutes: [], jobId: null, jobLang: null });
    vi.mocked(minutesLib.startMinutes).mockResolvedValue({ jobId: "job-1" });
    setup();
    const button = await screen.findByRole("button", { name: /write minutes/i });
    // Both languages are on offer; the target is active.
    expect(screen.getByRole("button", { name: "zh" }).getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(button);
    await waitFor(() => expect(minutesLib.startMinutes).toHaveBeenCalledWith("s1", "zh"));
    expect(useJobsStore.getState().jobs["job-1"]).toMatchObject({ kind: "minutes", sessionId: "s1" });
  });

  it("shows the document, and says when it is out of date", async () => {
    vi.mocked(minutesLib.getMinutes).mockResolvedValue({
      minutes: [doc({ stale: true })], jobId: null, jobLang: null,
    });
    setup();
    expect(await screen.findByText("A short planning call.")).toBeTruthy();
    expect(screen.getByText("Launch on March 3rd")).toBeTruthy();
    expect(screen.getByText("Bob")).toBeTruthy();
    expect(screen.getByRole("note").textContent).toMatch(/transcript changed/i);
  });

  it("holds the button while the recording is still live", async () => {
    vi.mocked(minutesLib.getMinutes).mockResolvedValue({ minutes: [], jobId: null, jobLang: null });
    useSessionStore.setState({ connection: "recording" });
    setup();
    const button = await screen.findByRole("button", { name: /write minutes/i });
    expect((button as HTMLButtonElement).disabled).toBe(true);
  });
});
