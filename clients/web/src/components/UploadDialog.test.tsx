// The dialog's fields used to be reset by an effect watching `open`. The body
// is now mounted only while open, so a fresh open starts from fresh state —
// and the reset can no longer race the close animation.
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("@/lib/upload", () => ({ startUpload: vi.fn() }));

const { UploadDialog } = await import("@/components/UploadDialog");
const { I18nProvider } = await import("@/i18n/provider");

const show = (open: boolean) => {
  const view = render(
    <I18nProvider>
      <UploadDialog open={open} onClose={() => {}} />
    </I18nProvider>,
  );
  const reopen = (next: boolean) =>
    view.rerender(
      <I18nProvider>
        <UploadDialog open={next} onClose={() => {}} />
      </I18nProvider>,
    );
  return { ...view, reopen };
};

const titleField = () => screen.getByPlaceholderText(/Untitled|Upload/);

describe("UploadDialog", () => {
  it("renders nothing while closed", () => {
    const { container } = show(false);
    expect(container.innerHTML).toBe("");
  });

  it("starts each open from a clean form", async () => {
    const { reopen } = show(true);
    const field = titleField();
    fireEvent.change(field, { target: { value: "Q3 planning" } });
    expect((titleField() as HTMLInputElement).value).toBe("Q3 planning");

    reopen(false);
    // The body leaves with the exit animation; only then is its state gone.
    await waitFor(() => expect(screen.queryByText("Transcribe from file")).toBeNull());
    reopen(true);

    expect((titleField() as HTMLInputElement).value).toBe("");
  });
});
