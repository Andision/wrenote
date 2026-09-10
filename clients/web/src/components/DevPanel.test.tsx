// The developer panel: re-entering the first-run flow, which was terminal
// work before this (`rm ~/.wrenote/models/*.bin`, then restart) and is why
// the setup screens were the least-looked-at part of the app. Deleting a
// model lives in Settings → Models now, next to the model it removes.
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("sonner", () => ({
  toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }),
}));
vi.mock("@/lib/info", () => ({ getAppInfo: vi.fn() }));

const info = await import("@/lib/info");
const { DevPanel } = await import("@/components/DevPanel");
const { I18nProvider } = await import("@/i18n/provider");
const { useSessionStore } = await import("@/store/sessionStore");

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
  });




  it("re-opens the first-run flow without deleting anything", async () => {
    show();
    fireEvent.click(await screen.findByRole("button", { name: "Run it" }));
    expect(useSessionStore.getState().setupRequest).toEqual({ focus: null });
  });

  it("shows the merged config, which no file on disk contains", async () => {
    show();
    await screen.findByText(/"whisper_cpp"/);
    expect(screen.getByTitle("/home/u/.wrenote/data.db")).toBeTruthy();
  });
});
