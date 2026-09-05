// The search results: title hits, lines grouped by session with the query
// marked, and a click that opens the session at that line.
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/search", async (orig) => ({
  ...(await orig<typeof import("@/lib/search")>()),
  search: vi.fn(),
}));

const searchLib = await import("@/lib/search");
const { SearchResults } = await import("@/components/SearchResults");
const { I18nProvider } = await import("@/i18n/provider");
const { useSessionStore } = await import("@/store/sessionStore");

const hit = (over: Partial<import("@/lib/search").SegmentHit>) => ({
  sessionId: "s1", sessionTitle: "Budget review", sessionCreatedAt: "2026-01-01T00:00:00Z",
  segmentId: "a", ord: 0, startedAt: 65, speaker: "Alice",
  origText: "cut the marketing budget", transText: "削减市场预算", ...over,
});

beforeEach(() => {
  vi.mocked(searchLib.search).mockReset();
  vi.useFakeTimers();
});

describe("SearchResults", () => {
  it("groups lines by session, marks the query, and opens the session at the line", async () => {
    vi.mocked(searchLib.search).mockResolvedValue({
      query: "budget",
      segments: [hit({}), hit({ segmentId: "b", ord: 1, startedAt: 70, origText: "budget again" })],
      sessions: [],
    });
    const openSessionAt = vi.fn().mockResolvedValue(undefined);
    useSessionStore.setState({ openSessionAt });
    render(<I18nProvider><SearchResults query="budget" locked={false} /></I18nProvider>);
    await vi.advanceTimersByTimeAsync(250);
    vi.useRealTimers();
    expect(await screen.findByText("1:05 · Alice")).toBeTruthy();
    expect(screen.getAllByText("budget", { selector: "mark" }).length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText("Budget review")).toBeTruthy();
    fireEvent.click(screen.getByText("1:05 · Alice").closest("button")!);
    expect(openSessionAt).toHaveBeenCalledWith("s1", "a");
  });

  it("says when nothing matches", async () => {
    vi.mocked(searchLib.search).mockResolvedValue({ query: "zzz", segments: [], sessions: [] });
    render(<I18nProvider><SearchResults query="zzz" locked={false} /></I18nProvider>);
    await vi.advanceTimersByTimeAsync(250);
    vi.useRealTimers();
    expect((await screen.findByText(/nothing matches/i)).textContent).toContain("zzz");
  });
});
