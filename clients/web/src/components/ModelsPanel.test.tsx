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
  saveEndpoint: vi.fn(),
  testEndpoint: vi.fn(),
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
  models: [], all_present: true, selected: {}, remote: [], endpoints: {},
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

// A slot is answered either by a file this machine downloaded or by something
// at a URL. The switch that decides which lives in the same section as the
// models, because it is the same question — and the consequence of choosing
// "somewhere else" has to be visible at the moment of choosing.
describe("ModelsPanel endpoints", () => {
  const endpoint = (over: Partial<import("@/lib/models").EndpointStatus> = {}) => ({
    active: false, base_url: "", model: "", has_api_key: false,
    api_key_env: "", timeout_s: 120, configured: false, local: false, ...over,
  });

  const withEndpoint = (chat: Partial<import("@/lib/models").EndpointStatus>) => ({
    models: [], all_present: true, selected: {}, remote: [],
    features: { translator: true, chat: true, speaker: true },
    endpoints: { chat: endpoint(chat) },
    options: [{ kind: "chat" as const, reason_code: "", reason_params: {}, options: [option()] }],
  });

  beforeEach(() => {
    vi.mocked(models.getModelStatus).mockResolvedValue(withEndpoint({}));
    vi.mocked(models.saveEndpoint).mockResolvedValue({
      applies: "now", endpoint: endpoint({ configured: true, active: true }), remote: [],
    });
  });

  it("offers the endpoint row on a slot that can have one", async () => {
    show();
    expect(await screen.findByText("Your own model service")).toBeTruthy();
    expect(screen.getByText(/uses a downloaded model/)).toBeTruthy();
  });

  it("does not offer it on a slot that cannot", async () => {
    vi.mocked(models.getModelStatus).mockResolvedValue({
      ...withEndpoint({}),
      endpoints: {},
      options: [{ kind: "stt" as const, reason_code: "", reason_params: {}, options: [option()] }],
    });
    show();
    await screen.findByText("Whisper base (Q5)");
    expect(screen.queryByText("Your own model service")).toBeNull();
  });

  it("saves what was typed, and leaves the unseen key alone", async () => {
    show();
    fireEvent.click(await screen.findByText("Your own model service"));
    fireEvent.change(screen.getByPlaceholderText("http://127.0.0.1:8080/v1"), {
      target: { value: "  http://127.0.0.1:8080/v1  " },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => {
      expect(vi.mocked(models.saveEndpoint)).toHaveBeenCalled();
    });
    const [kind, patch] = vi.mocked(models.saveEndpoint).mock.calls[0];
    expect(kind).toBe("chat");
    expect(patch.base_url).toBe("http://127.0.0.1:8080/v1"); // trimmed
    // The form never saw the key, so it must not send one — an omitted field
    // is what tells the engine to keep what it has.
    expect("api_key" in patch).toBe(false);
  });

  it("warns that text leaves the machine, and not when it doesn't", async () => {
    vi.mocked(models.getModelStatus).mockResolvedValue(
      withEndpoint({ configured: true, base_url: "https://api.example.com/v1", local: false }),
    );
    show();
    fireEvent.click(await screen.findByText("Your own model service"));
    expect(screen.getByText(/transcript text sent to this service/)).toBeTruthy();
  });

  it("says nothing alarming about a model server on this machine", async () => {
    vi.mocked(models.getModelStatus).mockResolvedValue(
      withEndpoint({ configured: true, base_url: "http://127.0.0.1:8080/v1", local: true }),
    );
    show();
    fireEvent.click(await screen.findByText("Your own model service"));
    expect(screen.queryByText(/transcript text sent to this service/)).toBeNull();
  });

  it("won't test an endpoint that has unsaved edits", async () => {
    vi.mocked(models.getModelStatus).mockResolvedValue(
      withEndpoint({ configured: true, base_url: "http://127.0.0.1:8080/v1", local: true }),
    );
    show();
    fireEvent.click(await screen.findByText("Your own model service"));
    const test = screen.getByRole("button", { name: /Test connection/ });
    expect(test.hasAttribute("disabled")).toBe(false);
    fireEvent.change(screen.getByDisplayValue("http://127.0.0.1:8080/v1"), {
      target: { value: "http://127.0.0.1:9999/v1" },
    });
    // Testing the stored value while showing a different one would report a
    // pass about something the user isn't looking at.
    expect(test.hasAttribute("disabled")).toBe(true);
  });

  it("opens the form instead of failing when switched on with no address", async () => {
    show();
    await screen.findByText("Your own model service");
    fireEvent.click(screen.getByRole("switch", { name: /Use the model service/ }));
    expect(screen.getByPlaceholderText("http://127.0.0.1:8080/v1")).toBeTruthy();
    expect(vi.mocked(models.saveEndpoint)).not.toHaveBeenCalled();
  });
});
