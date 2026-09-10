// The capture-source row: what a recording is made of.
//
// The microphone used to be compulsory — not just in the UI: the whole live
// pipeline was clocked by mic frames, so "record the meeting, not me" was
// not a checkbox away. It is now, and the rule the UI has to hold is that a
// recording always has at least one source.
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/hooks/useMicPreview", () => ({
  useMicPreview: vi.fn(() => ({ devices: [], level: 0 })),
}));
vi.mock("@/lib/capture", async (orig) => ({
  ...(await orig<typeof import("@/lib/capture")>()),
  listCaptureTargets: vi.fn().mockResolvedValue({ displays: [], windows: [] }),
}));

const { PreFlight } = await import("@/components/PreFlight");
const { I18nProvider } = await import("@/i18n/provider");
const { useSessionStore } = await import("@/store/sessionStore");

const show = () =>
  render(<I18nProvider><PreFlight onStart={vi.fn()} /></I18nProvider>);

const toggle = (name: string) => screen.getByRole("button", { name });
const settings = () => useSessionStore.getState().settings;

describe("PreFlight capture sources", () => {
  beforeEach(() => {
    useSessionStore.getState().updateSettings({
      captureMic: true,
      captureSystemAudio: false,
      captureScreen: false,
    });
  });

  it("won't let you turn the mic off when it is the only source", () => {
    show();
    expect(toggle(/Microphone/)).toHaveProperty("disabled", true);
    fireEvent.click(toggle(/Microphone/));
    expect(settings().captureMic).toBe(true);
  });

  it("lets you record the meeting and not yourself", () => {
    show();
    fireEvent.click(toggle(/System audio/));
    expect(toggle(/Microphone/)).toHaveProperty("disabled", false);

    fireEvent.click(toggle(/Microphone/));
    expect(settings().captureMic).toBe(false);
    expect(toggle(/Microphone/).getAttribute("aria-pressed")).toBe("false");
  });

  it("turning system audio back off brings the mic back, not silence", () => {
    show();
    fireEvent.click(toggle(/System audio/));
    fireEvent.click(toggle(/Microphone/));
    expect(settings().captureMic).toBe(false);

    fireEvent.click(toggle(/System audio/));
    expect(settings().captureSystemAudio).toBe(false);
    expect(settings().captureMic).toBe(true);
  });

  it("hides the mic's level test while the mic is off", () => {
    show();
    fireEvent.click(toggle(/System audio/));
    fireEvent.click(toggle(/Microphone/));
    expect(toggle(/Test/)).toHaveProperty("disabled", true);
  });
});
