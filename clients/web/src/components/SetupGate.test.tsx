// First-run setup: what it asks, and — more importantly — what it doesn't.
//
// The rule worth holding is that the runtime step only appears when there is a
// real choice. It is a judgement the client makes from the engine's data, so
// nothing else catches it getting it wrong: a Mac user or an offline user would
// just meet a pointless screen with one option and a Continue button.
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SetupGate } from "@/components/SetupGate";
import { I18nProvider } from "@/i18n/provider";
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
  ...over,
});

const kindOptions = (over: Partial<KindOptions> = {}): KindOptions => ({
  kind: "stt",
  reason_code: "ample_ram",
  reason_params: { ram: "16 GB" },
  options: [
    { id: "small", kind: "stt", tier: "small", name: "Whisper base", note_code: "stt_fast_rough",
      size_mb: 57, download_mb: 57, installed: false, fits: true, recommended: false,
      selected: false, blocked_code: "", blocked_params: {} },
    { id: "large", kind: "stt", tier: "large", name: "Whisper large", note_code: "stt_best",
      size_mb: 548, download_mb: 548, installed: false, fits: true, recommended: true,
      selected: true, blocked_code: "", blocked_params: {} },
  ],
  ...over,
});

const setup = () => render(<I18nProvider><SetupGate /></I18nProvider>);

beforeEach(() => {
  vi.mocked(models.getModelStatus).mockResolvedValue(modelStatus());
  vi.mocked(compute.getComputeStatus).mockResolvedValue(computeStatus([runtimeOption()]));
});

describe("SetupGate", () => {
  it("stays out of the way once the models are present", async () => {
    vi.mocked(models.getModelStatus).mockResolvedValue(
      modelStatus({ all_present: true, models: [] }),
    );
    const { container } = setup();
    await waitFor(() => expect(models.getModelStatus).toHaveBeenCalled());
    expect(container.innerHTML).toBe(""); // no jest-dom: a plain check reads the same
  });

  it("asks about the runtime when an accelerator can actually be installed", async () => {
    setup();
    expect(await screen.findByText("Choose how Wrenote runs")).toBeTruthy();
    expect(screen.getByText("Step 1 of 2")).toBeTruthy();
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
    expect(await screen.findByText("Set up Wrenote")).toBeTruthy();
    expect(screen.queryByText("Choose how Wrenote runs")).toBeNull();
    // …and with only one step, it doesn't pretend there were two.
    expect(screen.queryByText(/Step \d of 2/)).toBeNull();
  });

  it("skips the runtime step when the pack index is unreachable", async () => {
    // Offline: the accelerator is usable but nothing can be fetched for it.
    vi.mocked(compute.getComputeStatus).mockResolvedValue(
      computeStatus([runtimeOption({ note_code: "unpublished", download_mb: null })]),
    );
    setup();
    expect(await screen.findByText("Set up Wrenote")).toBeTruthy();
  });

  it("carries on to the models when the compute probe fails outright", async () => {
    vi.mocked(compute.getComputeStatus).mockRejectedValue(new Error("engine not ready"));
    setup();
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
    expect(await screen.findByText("Speech recognition")).toBeTruthy();
    expect(screen.getByText("Whisper large")).toBeTruthy();
    expect(screen.getByText("16 GB RAM — the best models fit")).toBeTruthy();
    expect(screen.queryByText("Translation")).toBeNull();
  });

  it("shows the total download and the models it covers", async () => {
    vi.mocked(compute.getComputeStatus).mockResolvedValue(computeStatus([]));
    setup();
    expect(await screen.findByText("Download 0.6 GB")).toBeTruthy();
    expect(screen.getByTitle("whisper.bin")).toBeTruthy();
  });
});
