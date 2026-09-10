// Saving an export used to be silent: a blob download that in a WebView goes
// somewhere the app can neither choose nor name. The engine writes the file
// now; what this holds is that the user is told which file and where.
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("sonner", () => ({
  toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }),
}));
vi.mock("@/lib/export", async (orig) => ({
  ...(await orig<typeof import("@/lib/export")>()),
  fetchExport: vi.fn(),
  saveExport: vi.fn(),
  revealSaved: vi.fn(),
}));
vi.mock("@/lib/minutes", async (orig) => ({
  ...(await orig<typeof import("@/lib/minutes")>()),
  getMinutes: vi.fn(),
}));

const exportLib = await import("@/lib/export");
const minutes = await import("@/lib/minutes");
const { toast } = await import("sonner");
const { ExportMenu } = await import("@/components/ExportMenu");
const { I18nProvider } = await import("@/i18n/provider");

const show = () =>
  render(
    <I18nProvider>
      <ExportMenu sessionId="s1" hasTranslations={false} />
    </I18nProvider>,
  );

const open = () => fireEvent.click(screen.getByRole("button", { name: /Export transcript/ }));

describe("ExportMenu", () => {
  beforeEach(() => {
    vi.mocked(minutes.getMinutes).mockResolvedValue({ minutes: [] } as never);
    vi.mocked(exportLib.saveExport).mockResolvedValue({
      path: "/home/u/.wrenote/exports/Standup.md",
      filename: "Standup.md",
      dir: "/home/u/.wrenote/exports",
      bytes: 1234,
    });
  });

  it("names the file it saved and where it went", async () => {
    show();
    open();
    fireEvent.click(screen.getByText("Markdown (.md)"));

    await waitFor(() => expect(exportLib.saveExport).toHaveBeenCalled());
    expect(vi.mocked(exportLib.saveExport).mock.calls[0].slice(0, 3)).toEqual([
      // `original` is the default when the session has no translations.
      "s1", "md", "original",
    ]);
    await waitFor(() => expect(toast.success).toHaveBeenCalled());
    const [message, opts] = vi.mocked(toast.success).mock.calls[0] as [
      string,
      { description: string; action: { label: string; onClick: () => void } },
    ];
    expect(message).toBe("Saved Standup.md");
    expect(opts.description).toBe("/home/u/.wrenote/exports");

    // …and the toast can open the folder, for the shells that can.
    opts.action.onClick();
    expect(exportLib.revealSaved).toHaveBeenCalled();
  });

  it("reports a failure rather than looking like it worked", async () => {
    vi.mocked(exportLib.saveExport).mockRejectedValue(new Error("disk full"));
    show();
    open();
    fireEvent.click(screen.getByText("Subtitles (.srt)"));
    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    expect(toast.success).not.toHaveBeenCalled();
  });
});
