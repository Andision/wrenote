// The editor persists on blur. It used to read the rows through a ref written
// during render; the handler closes over the rows of the render that produced
// it, which is the state the user just typed into — this holds that.
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/glossary", async (orig) => ({
  ...(await orig<typeof import("@/lib/glossary")>()),
  getGlossary: vi.fn(),
  saveGlossary: vi.fn(),
}));

const glossary = await import("@/lib/glossary");
const { GlossaryEditor } = await import("@/components/GlossaryEditor");
const { I18nProvider } = await import("@/i18n/provider");

const show = () =>
  render(
    <I18nProvider>
      <GlossaryEditor />
    </I18nProvider>,
  );

describe("GlossaryEditor", () => {
  beforeEach(() => {
    vi.mocked(glossary.getGlossary).mockResolvedValue([
      { id: "1", term: "Kubernetes", translation: "" },
    ]);
    vi.mocked(glossary.saveGlossary).mockResolvedValue(undefined);
  });

  it("saves what the user just typed when the field loses focus", async () => {
    show();
    const term = await screen.findByDisplayValue("Kubernetes");

    fireEvent.change(term, { target: { value: "Kubelet" } });
    fireEvent.blur(term);

    await waitFor(() =>
      expect(glossary.saveGlossary).toHaveBeenCalledWith([
        { id: "1", term: "Kubelet", translation: "" },
      ]),
    );
  });

  it("drops blank rows on the way out", async () => {
    show();
    await screen.findByDisplayValue("Kubernetes");

    fireEvent.click(screen.getByText("Add term"));
    const blank = screen.getAllByPlaceholderText(/^Term/)[1];
    fireEvent.blur(blank);

    await waitFor(() =>
      expect(glossary.saveGlossary).toHaveBeenCalledWith([
        { id: "1", term: "Kubernetes", translation: "" },
      ]),
    );
  });
});
