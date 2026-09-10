// First-run setup: what it asks, and — more importantly — what it doesn't.
//
// Two rules worth holding. The features step comes first, because what the
// user wants decides what there is to download. And the runtime step only
// appears when there is a real choice — a judgement the client makes from the
// engine's data, so nothing else catches it getting it wrong: a Mac user or an
// offline user would just meet a pointless screen with one Continue button.
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SetupGate } from "@/components/SetupGate";
import { I18nProvider } from "@/i18n/provider";
import { useSessionStore } from "@/store/sessionStore";
import type { ComputeStatus, RuntimeOption } from "@/lib/compute";
import type { KindOptions, ModelStatus } from "@/lib/models";

vi.mock("@/lib/compute", async (orig) => ({
  ...(await orig<typeof import("@/lib/compute")>()),
  getComputeStatus: vi.fn(),
  installRuntime: vi.fn(),
  selectAccelerator: vi.fn(),
}));
vi.mock("@/lib/models", async (orig) => ({
  ...(await orig<typeof import("@/lib/models")>()),
  getModelStatus: vi.fn(),
  startModelDownload: vi.fn(),
  selectModel: vi.fn(),
  setFeatures: vi.fn(),
}));

const compute = await import("@/lib/compute");
const models = await import("@/lib/models");

const runtimeOption = (over: Partial<RuntimeOption> = {}): RuntimeOption => ({
  variant: "vulkan",
  usable: true,
  installed: false,
  builtin: false,
  recommended: true,
  accelerated: true,
  note_code: "download",
  note: "36 MB download",
  download_mb: 36,
  hardware: null,
  ...over,
});

const computeStatus = (options: RuntimeOption[]): ComputeStatus =>
  ({ options, can_switch_without_restart: true }) as ComputeStatus;

const modelStatus = (over: Partial<ModelStatus> = {}): ModelStatus => ({
  models: [
    { key: "stt", filename: "whisper.bin", present: false, size: 574041195, downloaded: 0,
      model_id: "whisper-large-v3-turbo-q5", model_name: "Whisper large-v3 turbo (Q5)" },
  ],
  all_present: false,
  options: [],
  selected: {},
  features: { translator: true, chat: true, speaker: true },
  ...over,
});

const kindOptions = (over: Partial<KindOptions> = {}): KindOptions => ({
  kind: "stt",
  reason_code: "ample_ram",
  reason_params: { ram: "16 GB" },
  options: [
    { id: "small", kind: "stt", tier: "small", name: "Whisper base", note_code: "stt_fast_rough",
      size_mb: 57, ram_mb: 2048, download_mb: 57, installed: false, fits: true,
      recommended: false, selected: false, blocked_code: "", blocked_params: {} },
    { id: "large", kind: "stt", tier: "large", name: "Whisper large", note_code: "stt_best",
      size_mb: 548, ram_mb: 6144, download_mb: 548, installed: false, fits: true,
      recommended: true, selected: true, blocked_code: "", blocked_params: {} },
  ],
  ...over,
});

const setup = () => render(<I18nProvider><SetupGate /></I18nProvider>);

beforeEach(() => {
  useSessionStore.setState({ setupRequest: null, featurePrompt: null });
  vi.mocked(models.getModelStatus).mockResolvedValue(modelStatus());
  vi.mocked(models.setFeatures).mockResolvedValue({
    translator: true, chat: true, speaker: true,
  });
  vi.mocked(compute.getComputeStatus).mockResolvedValue(computeStatus([runtimeOption()]));
});

/** Every run opens on the features step; most tests are about what follows. */
const pastFeatures = async () => {
  await screen.findByText("What should Wrenote do?");
  fireEvent.click(screen.getByRole("button", { name: "Continue" }));
};

describe("SetupGate", () => {
  it("stays out of the way once the models are present", async () => {
    vi.mocked(models.getModelStatus).mockResolvedValue(
      modelStatus({ all_present: true, models: [] }),
    );
    const { container } = setup();
    await waitFor(() => expect(models.getModelStatus).toHaveBeenCalled());
    expect(container.innerHTML).toBe(""); // no jest-dom: a plain check reads the same
  });

  it("opens on the features step and sends what was left on", async () => {
    setup();
    await screen.findByText("What should Wrenote do?");
    expect(screen.getByText("Step 1 of 3")).toBeTruthy();
    // Transcription is shown but not a switch.
    expect(screen.getByLabelText("Transcription").getAttribute("data-disabled")).not.toBeNull();

    fireEvent.click(screen.getByLabelText("Meeting minutes & chat"));
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    await waitFor(() =>
      expect(models.setFeatures).toHaveBeenCalledWith({
        translator: true, chat: false, speaker: true,
      }),
    );
  });

  it("asks about the runtime when an accelerator can actually be installed", async () => {
    setup();
    await pastFeatures();
    expect(await screen.findByText("Choose how Wrenote runs")).toBeTruthy();
    expect(screen.getByText("Step 2 of 3")).toBeTruthy();
    expect(screen.getByText("recommended")).toBeTruthy();
  });

  it("skips straight to the models when nothing is installable", async () => {
    // A Mac: Metal is built into the bundle, so there is nothing to choose.
    vi.mocked(compute.getComputeStatus).mockResolvedValue(
      computeStatus([
        runtimeOption({ variant: "metal", builtin: true, installed: true, download_mb: null }),
      ]),
    );
    setup();
    await pastFeatures();
    expect(await screen.findByText("Set up Wrenote")).toBeTruthy();
    expect(screen.queryByText("Choose how Wrenote runs")).toBeNull();
    // …and with the runtime step gone, it doesn't pretend there were three.
    expect(screen.getByText("Step 2 of 2")).toBeTruthy();
  });

  it("skips the runtime step when the pack index is unreachable", async () => {
    // Offline: the accelerator is usable but nothing can be fetched for it.
    vi.mocked(compute.getComputeStatus).mockResolvedValue(
      computeStatus([runtimeOption({ note_code: "unpublished", download_mb: null })]),
    );
    setup();
    await pastFeatures();
    expect(await screen.findByText("Set up Wrenote")).toBeTruthy();
  });

  it("carries on to the models when the compute probe fails outright", async () => {
    vi.mocked(compute.getComputeStatus).mockRejectedValue(new Error("engine not ready"));
    setup();
    await pastFeatures();
    // A broken hardware probe must not block setting the app up.
    expect(await screen.findByText("Set up Wrenote")).toBeTruthy();
  });

  it("offers model choices for kinds that have more than one", async () => {
    vi.mocked(compute.getComputeStatus).mockResolvedValue(computeStatus([]));
    vi.mocked(models.getModelStatus).mockResolvedValue(
      modelStatus({
        options: [
          kindOptions(),
          // One option is not a decision; it should not get a section.
          kindOptions({ kind: "translator", options: [kindOptions().options[0]] }),
        ],
      }),
    );
    setup();
    await pastFeatures();
    expect(await screen.findByText("Speech recognition (live)")).toBeTruthy();
    expect(screen.getByText("Whisper large")).toBeTruthy();
    expect(screen.getByText("16 GB RAM — the best models fit")).toBeTruthy();
    expect(screen.queryByText("Translation")).toBeNull();
  });

  it("comes back when asked, even though everything is present", async () => {
    // `openSetup` is what the "turn it on" button does; the flow has to
    // reopen for a returning user, with that feature already switched on.
    vi.mocked(models.getModelStatus).mockResolvedValue(
      modelStatus({ all_present: true, models: [], features: {
        translator: true, chat: false, speaker: true,
      } }),
    );
    setup();
    await waitFor(() => expect(models.getModelStatus).toHaveBeenCalled());
    expect(screen.queryByText("What should Wrenote do?")).toBeNull();

    useSessionStore.getState().openSetup("chat");
    await screen.findByText("What should Wrenote do?");
    expect(screen.getByLabelText("Meeting minutes & chat")).toHaveProperty(
      "ariaChecked", "true",
    );
  });

  it("shows the total download and the models it covers", async () => {
    vi.mocked(compute.getComputeStatus).mockResolvedValue(computeStatus([]));
    setup();
    await pastFeatures();
    expect(await screen.findByText("Download 0.6 GB")).toBeTruthy();
    expect(screen.getByTitle("whisper.bin")).toBeTruthy();
  });
});
