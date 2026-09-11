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
  listCaptureTargets: vi.fn(),
}));

const capture = await import("@/lib/capture");
const { PreFlight } = await import("@/components/PreFlight");
const { I18nProvider } = await import("@/i18n/provider");
const { useSessionStore } = await import("@/store/sessionStore");

const show = () =>
  render(<I18nProvider><PreFlight onStart={vi.fn()} /></I18nProvider>);

const toggle = (name: string | RegExp) => screen.getByRole("button", { name });
const settings = () => useSessionStore.getState().settings;

describe("PreFlight capture sources", () => {
  beforeEach(() => {
    vi.mocked(capture.listCaptureTargets).mockResolvedValue({
      displays: [],
      windows: [],
      audio_scope: false,
    });
    useSessionStore.getState().updateSettings({
      captureMic: true,
      captureSystemAudio: false,
      captureScreen: false,
      audioApp: null,
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


describe("PreFlight audio source", () => {
  const zoom = {
    type: "window" as const, id: 1, title: "Zoom Meeting",
    app: "zoom.us", bundle: "us.zoom.xos", width: 900, height: 600,
  };
  const chrome = {
    type: "window" as const, id: 2, title: "YouTube", app: "Google Chrome",
    bundle: "com.google.Chrome", width: 1200, height: 800,
  };

  beforeEach(() => {
    useSessionStore.getState().updateSettings({
      captureMic: true,
      captureSystemAudio: false,
      captureScreen: false,
      audioApp: null,
    });
  });

  it("offers one entry per running app, once each", async () => {
    vi.mocked(capture.listCaptureTargets).mockResolvedValue({
      displays: [],
      // Two windows of the same app are one audio source.
      windows: [zoom, chrome, { ...zoom, id: 3, title: "Zoom Chat" }],
      audio_scope: true,
    });
    show();
    fireEvent.click(toggle(/System audio/));
    const select = await screen.findByLabelText("Which audio to capture");
    const options = Array.from(select.querySelectorAll("option")).map((o) => o.textContent);
    expect(options).toEqual(["All system audio", "Only zoom.us", "Only Google Chrome"]);
  });

  it("records the meeting and not the video in the other window", async () => {
    vi.mocked(capture.listCaptureTargets).mockResolvedValue({
      displays: [], windows: [zoom, chrome], audio_scope: true,
    });
    show();
    fireEvent.click(toggle(/System audio/));
    const select = await screen.findByLabelText("Which audio to capture");
    fireEvent.change(select, { target: { value: "us.zoom.xos" } });
    expect(settings().audioApp).toEqual({ id: "us.zoom.xos", label: "zoom.us" });
  });

  it("does not offer the choice where the platform cannot filter", async () => {
    vi.mocked(capture.listCaptureTargets).mockResolvedValue({
      displays: [], windows: [zoom], audio_scope: false,
    });
    show();
    fireEvent.click(toggle(/System audio/));
    await screen.findByText(/System audio/);
    // Offering it and then capturing the whole desktop would record more
    // than the user agreed to.
    expect(screen.queryByLabelText("Which audio to capture")).toBeNull();
  });

  it("says so when the chosen app has quit", async () => {
    vi.mocked(capture.listCaptureTargets).mockResolvedValue({
      displays: [], windows: [chrome], audio_scope: true,
    });
    useSessionStore.getState().updateSettings({
      audioApp: { id: "us.zoom.xos", label: "zoom.us" },
    });
    show();
    fireEvent.click(toggle(/System audio/));
    expect(await screen.findByText(/zoom.us isn't running/)).toBeTruthy();
  });
});

// The claim above the record button is the last thing a person reads before
// the microphone opens, so it has to be true of the engine that is actually
// running — including one configured to send transcript text elsewhere.
describe("PreFlight privacy line", () => {
  beforeEach(() => {
    vi.mocked(capture.listCaptureTargets).mockResolvedValue({
      displays: [], windows: [], audio_scope: false,
    });
    useSessionStore.setState({ remoteSlots: [] });
  });

  it("says everything is local when nothing is configured remotely", () => {
    show();
    expect(screen.getByText(/no audio leaves your device/)).toBeTruthy();
  });

  it("names the features whose text is sent away when one is remote", () => {
    useSessionStore.setState({ remoteSlots: ["chat", "translator"] });
    show();
    const line = screen.getByText(/goes to the model endpoint/);
    expect(line.textContent).toContain("Chat");
    expect(line.textContent).toContain("Translation");
    // Audio still never leaves: speech recognition stays in the engine.
    expect(line.textContent).toContain("Audio stays on your device");
    expect(screen.queryByText(/no audio leaves your device/)).toBeNull();
  });
});
