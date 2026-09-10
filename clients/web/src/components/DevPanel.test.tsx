// The developer panel: the two things it exists to make reachable.
//
// Deleting a model and re-running the first-run flow were both terminal work
// before this — `rm ~/.wrenote/models/*.bin`, then restart — which is why the
// setup screens were the least-looked-at part of the app.
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("sonner", () => ({
  toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }),
}));
vi.mock("@/lib/models", async (orig) => ({
  ...(await orig<typeof import("@/lib/models")>()),
  getModelStatus: vi.fn(),
  deleteModel: vi.fn(),
}));
vi.mock("@/lib/info", () => ({ getAppInfo: vi.fn() }));

const models = await import("@/lib/models");
const info = await import("@/lib/info");
const { toast } = await import("sonner");
const { DevPanel } = await import("@/components/DevPanel");
const { I18nProvider } = await import("@/i18n/provider");
const { useSessionStore } = await import("@/store/sessionStore");

const option = (over: Partial<import("@/lib/models").ModelOption> = {}) => ({
  id: "whisper-base-q5", kind: "stt" as const, tier: "small" as const,
  name: "Whisper base (Q5)", note_code: "stt_fast_rough", size_mb: 57,
  ram_mb: 2048, download_mb: null, installed: true, fits: true,
  recommended: false, selected: false, blocked_code: "", blocked_params: {},
  ...over,
});

const show = () =>
  render(
    <I18nProvider>
      <DevPanel />
    </I18nProvider>,
  );

describe("DevPanel", () => {
  beforeEach(() => {
    useSessionStore.setState({ setupRequest: null });
    vi.mocked(info.getAppInfo).mockResolvedValue({
      version: "0.1.0",
      paths: { data_dir: "/home/u/.wrenote", db_path: "/home/u/.wrenote/data.db" },
      config: { stt: { backend: "whisper_cpp" } },
      static_dir_exists: true,
    });
    vi.mocked(models.getModelStatus).mockResolvedValue({
      models: [], all_present: true, selected: {},
      features: { translator: true, chat: true, speaker: true },
      options: [
        { kind: "stt", reason_code: "", reason_params: {}, options: [option()] },
        // The same file backs two slots; it should be offered once.
        { kind: "stt_offline", reason_code: "", reason_params: {}, options: [option()] },
        { kind: "chat", reason_code: "", reason_params: {}, options: [
          option({ id: "qwen3-4b-instruct-q4", kind: "chat", name: "Qwen3 4B", installed: false }),
        ] },
      ],
    });
    vi.mocked(models.deleteModel).mockResolvedValue({
      model: "whisper-base-q5", removed: ["ggml-base-q5.bin"], failed: [],
      freed_mb: 57, slots: ["stt", "stt_offline"],
    });
  });

  it("lists what is on disk, once each, and not what isn't", async () => {
    show();
    await waitFor(() =>
      expect(screen.getAllByText("Whisper base (Q5)")).toHaveLength(1),
    );
    expect(screen.queryByText("Qwen3 4B")).toBeNull();
  });

  it("deletes a model and says how much that freed", async () => {
    show();
    fireEvent.click(await screen.findByRole("button", { name: "Delete the files" }));
    await waitFor(() =>
      expect(models.deleteModel).toHaveBeenCalledWith("whisper-base-q5"),
    );
    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith("Deleted · 57 MB freed"),
    );
  });

  it("reports a delete the OS refused rather than claiming success", async () => {
    // Windows will not unlink a file a loaded model still holds open.
    vi.mocked(models.deleteModel).mockResolvedValue({
      model: "whisper-base-q5", removed: [],
      failed: [{ filename: "ggml-base-q5.bin", error: "in use" }],
      freed_mb: 0, slots: [],
    });
    show();
    fireEvent.click(await screen.findByRole("button", { name: "Delete the files" }));
    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith("Delete failed: in use"),
    );
    expect(toast.success).not.toHaveBeenCalled();
  });

  it("re-opens the first-run flow without deleting anything", async () => {
    show();
    fireEvent.click(await screen.findByRole("button", { name: "Run it" }));
    expect(useSessionStore.getState().setupRequest).toEqual({ focus: null });
    expect(models.deleteModel).not.toHaveBeenCalled();
  });

  it("shows the merged config, which no file on disk contains", async () => {
    show();
    await screen.findByText(/"whisper_cpp"/);
    expect(screen.getByTitle("/home/u/.wrenote/data.db")).toBeTruthy();
  });
});
