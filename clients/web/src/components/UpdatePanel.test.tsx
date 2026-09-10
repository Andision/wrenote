// Settings → General's update section.
//
// The engine decides whether an update exists; what this has to get right is
// the rendering of that decision: the running version is always shown, a
// newer one gets a Download that opens the right URL, a failure code becomes
// words, and the automatic-check switch persists through the API.
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { UpdatePanel } from "@/components/UpdatePanel";
import { I18nProvider } from "@/i18n/provider";
import { TAPS_BEFORE_HINT, TAPS_REQUIRED, setDevMode } from "@/lib/devMode";
import type { UpdateStatus } from "@/lib/update";

vi.mock("sonner", () => ({
  toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }),
}));
vi.mock("@/lib/update", async (orig) => ({
  ...(await orig<typeof import("@/lib/update")>()),
  getUpdateStatus: vi.fn(),
  checkForUpdate: vi.fn(),
  setUpdateCheck: vi.fn(),
  openExternal: vi.fn(),
}));

const update = await import("@/lib/update");
const { toast } = await import("sonner");

/** Read the persisted flag directly — the hook is covered in devMode.test. */
const devModeIsOn = () => localStorage.getItem("wrenote.devMode") === "1";

const status = (over: Partial<UpdateStatus> = {}): UpdateStatus => ({
  current: "0.1.0",
  enabled: true,
  platform: "darwin-aarch64",
  index_url: "https://example.test/latest.json",
  checked_at: "2026-09-05T08:00:00+00:00",
  latest: "0.1.0",
  available: false,
  download_url: null,
  release_url: null,
  notes: "",
  published_at: null,
  error: null,
  ...over,
});

const show = () =>
  render(
    <I18nProvider>
      <UpdatePanel />
    </I18nProvider>,
  );

describe("UpdatePanel", () => {
  beforeEach(() => {
    setDevMode(false);
    vi.mocked(update.getUpdateStatus).mockResolvedValue(status());
    vi.mocked(update.setUpdateCheck).mockResolvedValue();
  });

  it("shows the running version and that it is current", async () => {
    show();
    expect(await screen.findByText("Wrenote 0.1.0")).toBeTruthy();
    expect(screen.getByText(/You're on the latest version/)).toBeTruthy();
    expect(screen.queryByText("Download")).toBeNull();
  });

  it("checks on demand and offers this machine's installer", async () => {
    vi.mocked(update.checkForUpdate).mockResolvedValue(
      status({
        latest: "0.2.0",
        available: true,
        download_url: "https://x/Wrenote_0.2.0_aarch64.dmg",
        release_url: "https://github.com/Andision/wrenote/releases/tag/v0.2.0",
      }),
    );
    show();
    fireEvent.click(await screen.findByText("Check for updates"));
    expect(await screen.findByText("Wrenote 0.2.0 is available")).toBeTruthy();
    fireEvent.click(screen.getByText("Download"));
    expect(update.openExternal).toHaveBeenCalledWith("https://x/Wrenote_0.2.0_aarch64.dmg");
    fireEvent.click(screen.getByText("What's new"));
    expect(update.openExternal).toHaveBeenLastCalledWith(
      "https://github.com/Andision/wrenote/releases/tag/v0.2.0",
    );
  });

  it("falls back to the release page when no installer is published for this machine", async () => {
    vi.mocked(update.getUpdateStatus).mockResolvedValue(
      status({ latest: "0.2.0", available: true, release_url: "https://r/v0.2.0" }),
    );
    show();
    fireEvent.click(await screen.findByText("Download"));
    expect(update.openExternal).toHaveBeenCalledWith("https://r/v0.2.0");
    expect(screen.queryByText("What's new")).toBeNull();
  });

  it("renders a failure code as words", async () => {
    vi.mocked(update.getUpdateStatus).mockResolvedValue(
      status({ latest: null, checked_at: "2026-09-05T08:00:00+00:00", error: "unreachable" }),
    );
    show();
    expect(await screen.findByText(/Couldn't reach the release index/)).toBeTruthy();
  });

  it("opens developer mode on the fifth tap of the version, not before", async () => {
    // The version line is the only affordance for the developer tools; if it
    // fired on fewer taps, an ordinary double-click would trip it.
    show();
    const version = await screen.findByText("Wrenote 0.1.0");
    for (let i = 0; i < TAPS_REQUIRED - 1; i++) {
      fireEvent.click(version);
      expect(devModeIsOn()).toBe(false);
    }
    fireEvent.click(version);
    expect(devModeIsOn()).toBe(true);
  });

  it("says how many taps are left, but only near the end", async () => {
    show();
    const version = await screen.findByText("Wrenote 0.1.0");
    for (let i = 0; i < TAPS_REQUIRED - TAPS_BEFORE_HINT - 1; i++) {
      fireEvent.click(version);
    }
    expect(toast).not.toHaveBeenCalled();
    fireEvent.click(version);
    expect(toast).toHaveBeenCalledWith("2 more taps for developer mode");
  });

  it("says when automatic checks are off, and persists the switch", async () => {
    vi.mocked(update.getUpdateStatus).mockResolvedValue(
      status({ enabled: false, checked_at: null, latest: null }),
    );
    show();
    expect(await screen.findByText("Automatic checks are off.")).toBeTruthy();
    fireEvent.click(screen.getByRole("switch"));
    await waitFor(() => expect(update.setUpdateCheck).toHaveBeenCalledWith(true));
  });
});
