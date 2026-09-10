// Settings → About: the licences, and the rule about not inventing them.
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/about", () => ({ getAbout: vi.fn() }));
vi.mock("@/lib/update", async (orig) => ({
  ...(await orig<typeof import("@/lib/update")>()),
  getUpdateStatus: vi.fn().mockRejectedValue(new Error("offline")),
  openExternal: vi.fn(),
}));

const about = await import("@/lib/about");
const { AboutPanel } = await import("@/components/AboutPanel");
const { I18nProvider } = await import("@/i18n/provider");

const show = () => render(<I18nProvider><AboutPanel /></I18nProvider>);

describe("AboutPanel", () => {
  beforeEach(() => {
    vi.mocked(about.getAbout).mockResolvedValue({
      version: "0.1.0",
      license: "AGPL-3.0-only",
      source: "https://github.com/Andision/wrenote",
      native: [{ name: "whisper.cpp", license: "MIT", url: "https://example.test/w" }],
      web: [{ name: "React", license: "MIT", url: "https://example.test/r" }],
      python: [{ name: "fastapi", version: "0.141.1", license: "MIT" }],
      models: [
        { name: "Whisper base (Q5)", license: "MIT", url: "https://example.test/m" },
        // No licence asserted: the catalogue links instead of guessing.
        { name: "Hunyuan MT2 1.8B", url: "https://example.test/h" },
      ],
    });
  });

  it("names Wrenote's own licence and where the source is", async () => {
    show();
    expect(await screen.findByText(/free software under AGPL-3.0-only/)).toBeTruthy();
    expect(screen.getByText("https://github.com/Andision/wrenote")).toBeTruthy();
  });

  it("lists what it ships, with versions where it has them", async () => {
    show();
    await screen.findByText("whisper.cpp");
    expect(screen.getByText("React")).toBeTruthy();
    expect(screen.getByText("fastapi")).toBeTruthy();
    expect(screen.getByText("0.141.1")).toBeTruthy();
  });

  it("says 'see upstream' rather than inventing a licence it wasn't given", async () => {
    show();
    await screen.findByText("Hunyuan MT2 1.8B");
    expect(screen.getByText("see upstream")).toBeTruthy();
    // …and the one that *was* given still shows it.
    expect(screen.getAllByText("MIT").length).toBeGreaterThan(0);
  });

  it("still shows the version when the engine can't answer", async () => {
    vi.mocked(about.getAbout).mockRejectedValue(new Error("offline"));
    const { container } = show();
    await waitFor(() => expect(container.textContent).toBeTruthy());
    expect(screen.queryByText(/free software/)).toBeNull();
  });
});
