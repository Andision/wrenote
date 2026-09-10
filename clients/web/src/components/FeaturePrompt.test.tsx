// A declined feature keeps its buttons; this is what they do instead.
//
// The point of the dialog is discoverability: hiding the chat and minutes
// buttons for a user who said no at setup means nothing in the app ever
// mentions that it can do those things. So the click has to land somewhere,
// and that somewhere has to lead back to the setup flow.
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/models", async (orig) => ({
  ...(await orig<typeof import("@/lib/models")>()),
  getModelStatus: vi.fn(),
}));

const models = await import("@/lib/models");
const { FeaturePrompt } = await import("@/components/FeaturePrompt");
const { I18nProvider } = await import("@/i18n/provider");
const { useSessionStore } = await import("@/store/sessionStore");

const show = () =>
  render(
    <I18nProvider>
      <FeaturePrompt />
    </I18nProvider>,
  );

describe("FeaturePrompt", () => {
  beforeEach(() => {
    useSessionStore.setState({
      features: { translator: true, chat: true, speaker: true },
      featurePrompt: null,
      setupRequest: null,
    });
    vi.mocked(models.getModelStatus).mockResolvedValue({
      models: [],
      all_present: true,
      selected: {},
      features: { translator: true, chat: true, speaker: true },
      options: [
        {
          kind: "chat",
          reason_code: "",
          reason_params: {},
          options: [
            {
              id: "qwen3-4b-instruct-q4", kind: "chat", tier: "medium",
              name: "Qwen3 4B", note_code: "chat_default", size_mb: 2497, ram_mb: 8192,
              download_mb: 2497, installed: false, fits: true,
              recommended: true, selected: true,
              blocked_code: "", blocked_params: {},
            },
          ],
        },
      ],
    });
  });

  it("stays out of the way until something asks for a feature that is off", () => {
    const { container } = show();
    expect(container.innerHTML).toBe("");
  });

  it("requireFeature passes a feature that is on, and raises no dialog", () => {
    show();
    expect(useSessionStore.getState().requireFeature("chat")).toBe(true);
    expect(useSessionStore.getState().featurePrompt).toBeNull();
  });

  it("names the feature and what turning it on costs", async () => {
    useSessionStore.setState({ features: { translator: true, chat: false, speaker: true } });
    show();
    expect(useSessionStore.getState().requireFeature("chat")).toBe(false);

    expect(await screen.findByText("Meeting minutes & chat isn't set up yet")).toBeTruthy();
    await waitFor(() =>
      expect(screen.getByText(/Turning it on downloads about 2497 MB/)).toBeTruthy(),
    );
  });

  it("Not now just closes it — clicking the button again is the ask", async () => {
    useSessionStore.setState({ features: { translator: true, chat: false, speaker: true } });
    show();
    useSessionStore.getState().requireFeature("chat");
    fireEvent.click(await screen.findByRole("button", { name: "Not now" }));
    expect(useSessionStore.getState().featurePrompt).toBeNull();
    expect(useSessionStore.getState().setupRequest).toBeNull();
  });

  it("Turn it on re-enters the setup flow focused on that feature", async () => {
    useSessionStore.setState({ features: { translator: true, chat: false, speaker: true } });
    show();
    useSessionStore.getState().requireFeature("chat");
    fireEvent.click(await screen.findByRole("button", { name: "Turn it on" }));
    expect(useSessionStore.getState().setupRequest).toEqual({ focus: "chat" });
    expect(useSessionStore.getState().featurePrompt).toBeNull();
  });
});
