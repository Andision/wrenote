// Settings → Models: choosing a model, switching a feature off, and — the
// reason this file exists — removing one you downloaded and don't want.
//
// Deleting was only in the developer panel, which is the wrong place for it:
// having three speech models on disk and wanting two is an ordinary thing,
// not a debugging one.
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("sonner", () => ({
  toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }),
}));
vi.mock("@/lib/jobs", () => ({
  subscribeJob: vi.fn(() => () => {}),
  formatEta: () => "",
}));
vi.mock("@/lib/confirm", () => ({ confirmDialog: vi.fn() }));
vi.mock("@/lib/models", async (orig) => ({
  ...(await orig<typeof import("@/lib/models")>()),
  getModelStatus: vi.fn(),
  deleteModel: vi.fn(),
  selectModel: vi.fn(),
  setFeatures: vi.fn(),
}));

const models = await import("@/lib/models");
const { confirmDialog } = await import("@/lib/confirm");
const { toast } = await import("sonner");
const { ModelsPanel } = await import("@/components/ModelsPanel");
const { I18nProvider } = await import("@/i18n/provider");

type Option = import("@/lib/models").ModelOption;

const option = (over: Partial<Option> = {}): Option => ({
  id: "whisper-base-q5", kind: "stt", tier: "small", name: "Whisper base (Q5)",
  note_code: "stt_fast_rough", size_mb: 57, ram_mb: 2048, download_mb: null,
  installed: true, fits: true, recommended: false, selected: false,
  blocked_code: "", blocked_params: {}, ...over,
});

const status = (options: Option[]) => ({
  models: [], all_present: true, selected: {},
  features: { translator: true, chat: true, speaker: true },
  options: [{ kind: "stt" as const, reason_code: "", reason_params: {}, options }],
});

const show = () => render(<I18nProvider><ModelsPanel /></I18nProvider>);

describe("ModelsPanel", () => {
  beforeEach(() => {
    vi.mocked(models.getModelStatus).mockResolvedValue(
      status([option(), option({ id: "whisper-small-q5", name: "Whisper small (Q5)", size_mb: 181 })]),
    );
    vi.mocked(models.deleteModel).mockResolvedValue({
      model: "whisper-base-q5", removed: ["ggml-base-q5.bin"], failed: [],
      freed_mb: 57, slots: ["stt"],
    });
  });

  it("offers a delete on each downloaded model, and not on one that isn't", async () => {
    vi.mocked(models.getModelStatus).mockResolvedValue(
      status([
        option(),
        option({ id: "whisper-large", name: "Whisper large", installed: false, download_mb: 548 }),
      ]),
    );
    show();
    await screen.findByText("Whisper base (Q5)");
    expect(screen.getAllByRole("button", { name: /Delete the downloaded files/ })).toHaveLength(1);
  });

  it("deletes an unused model without asking, and says what it freed", async () => {
    show();
    await screen.findByText("Whisper base (Q5)");
    fireEvent.click(screen.getAllByRole("button", { name: /Delete the downloaded files/ })[0]);

    await waitFor(() => expect(models.deleteModel).toHaveBeenCalledWith("whisper-base-q5"));
    expect(confirmDialog).not.toHaveBeenCalled();
    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith("Deleted Whisper base (Q5) · 57 MB freed"),
    );
  });

  it("asks before deleting the model a slot is using, and obeys a no", async () => {
    vi.mocked(models.getModelStatus).mockResolvedValue(status([option({ selected: true })]));
    vi.mocked(confirmDialog).mockResolvedValue(false);
    show();
    await screen.findByText("Whisper base (Q5)");
    fireEvent.click(screen.getByRole("button", { name: /Delete the downloaded files/ }));

    await waitFor(() => expect(confirmDialog).toHaveBeenCalled());
    expect(models.deleteModel).not.toHaveBeenCalled();
  });

  it("goes ahead on a yes", async () => {
    vi.mocked(models.getModelStatus).mockResolvedValue(status([option({ selected: true })]));
    vi.mocked(confirmDialog).mockResolvedValue(true);
    show();
    await screen.findByText("Whisper base (Q5)");
    fireEvent.click(screen.getByRole("button", { name: /Delete the downloaded files/ }));
    await waitFor(() => expect(models.deleteModel).toHaveBeenCalledWith("whisper-base-q5"));
  });

  it("reports a delete the OS refused rather than claiming success", async () => {
    vi.mocked(models.deleteModel).mockResolvedValue({
      model: "whisper-base-q5", removed: [],
      failed: [{ filename: "ggml-base-q5.bin", error: "in use" }],
      freed_mb: 0, slots: [],
    });
    show();
    await screen.findByText("Whisper base (Q5)");
    fireEvent.click(screen.getAllByRole("button", { name: /Delete the downloaded files/ })[0]);
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith("Delete failed: in use"));
    expect(toast.success).not.toHaveBeenCalled();
  });
});
