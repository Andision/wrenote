// The settings rail: what a user meets first, and what they have to ask for.
//
// Line-splitting thresholds and the inference runtime are legitimate
// settings — a power user tuning them is not debugging — but they are not
// the first thing anyone should meet. So: a disclosure, not developer mode,
// and no warning dialog in front of it. People learn to dismiss those, and
// dismissing one undoes nothing; the reset in each panel does.
import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/update", async (orig) => ({
  ...(await orig<typeof import("@/lib/update")>()),
  getUpdateStatus: vi.fn().mockRejectedValue(new Error("offline")),
}));
vi.mock("@/lib/models", async (orig) => ({
  ...(await orig<typeof import("@/lib/models")>()),
  getModelStatus: vi.fn().mockRejectedValue(new Error("offline")),
}));
vi.mock("@/lib/compute", async (orig) => ({
  ...(await orig<typeof import("@/lib/compute")>()),
  getComputeStatus: vi.fn().mockRejectedValue(new Error("offline")),
}));
vi.mock("@/lib/glossary", async (orig) => ({
  ...(await orig<typeof import("@/lib/glossary")>()),
  getGlossary: vi.fn().mockResolvedValue([]),
}));

const { SettingsDrawer } = await import("@/components/SettingsDrawer");
const { I18nProvider } = await import("@/i18n/provider");
const { useSessionStore } = await import("@/store/sessionStore");
const { setDevMode } = await import("@/lib/devMode");

const show = () => render(<I18nProvider><SettingsDrawer /></I18nProvider>);
/** The rail entries are buttons; the panel header repeats the active one as
 *  a heading, so every rail assertion goes through the role. */
const rail = (name: string) => screen.getByRole("button", { name });
const noRail = (name: string) => screen.queryByRole("button", { name });

describe("SettingsDrawer", () => {
  beforeEach(() => {
    setDevMode(false);
    useSessionStore.setState({ settingsOpen: true });
    useSessionStore.getState().resetSettings(["minSilenceMs", "partialIntervalMs"]);
  });

  it("shows only the everyday categories until Advanced is opened", () => {
    show();
    for (const name of ["General", "Recording", "Glossary", "Models", "About"]) {
      expect(rail(name)).toBeTruthy();
    }
    expect(noRail("Tuning")).toBeNull();
    expect(noRail("Compute")).toBeNull();

    fireEvent.click(rail("Advanced"));
    for (const name of ["Tuning", "Compute", "Engines"]) {
      expect(rail(name)).toBeTruthy();
    }
  });

  it("opens an advanced panel with its caution and a way back", () => {
    show();
    fireEvent.click(rail("Advanced"));
    fireEvent.click(rail("Tuning"));
    expect(screen.getByText(/how the live transcript is cut into lines/i)).toBeTruthy();
    expect(rail("Reset to defaults")).toBeTruthy();
  });

  it("resets the panel's own settings and leaves the rest alone", () => {
    useSessionStore.getState().updateSettings({
      minSilenceMs: 1500,
      partialIntervalMs: 1500,
      refineAfterStop: false,
    });
    show();
    fireEvent.click(rail("Advanced"));
    fireEvent.click(rail("Tuning"));
    fireEvent.click(rail("Reset to defaults"));

    const s = useSessionStore.getState().settings;
    expect(s.minSilenceMs).toBe(800);
    expect(s.partialIntervalMs).toBe(800);
    expect(s.refineAfterStop).toBe(false); // Recording's; not this panel's
  });

  it("marks the one experimental setting, without a section for it", () => {
    // An "Experimental" category becomes a graveyard nobody empties.
    show();
    fireEvent.click(rail("Recording"));
    expect(screen.getByText("Experimental")).toBeTruthy();
    expect(noRail("Experimental")).toBeNull();
  });

  it("keeps Advanced open while you are inside it", () => {
    show();
    fireEvent.click(rail("Advanced"));
    fireEvent.click(rail("Engines"));
    // Collapsing while the open panel is one of the hidden ones would leave
    // the rail showing nothing selected.
    fireEvent.click(rail("Advanced"));
    expect(rail("Engines")).toBeTruthy();
  });

  it("puts Developer inside Advanced, and only with developer mode on", () => {
    show();
    fireEvent.click(rail("Advanced"));
    expect(noRail("Developer")).toBeNull();
    act(() => setDevMode(true));
    expect(rail("Developer")).toBeTruthy();
  });
});
