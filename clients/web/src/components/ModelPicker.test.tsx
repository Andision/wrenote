// The model picker, shared by the wizard and Settings.
//
// Two behaviours that a wrong render turns into a support ticket: a model the
// machine can't run must be visible *and* unselectable (offering it produces a
// confusing failure later; hiding it looks like a missing feature), and the
// size shown has to distinguish "you'll download this" from "already here".
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ModelPicker } from "@/components/ModelPicker";
import { I18nProvider } from "@/i18n/provider";
import type { KindOptions, ModelOption } from "@/lib/models";

const option = (over: Partial<ModelOption> = {}): ModelOption => ({
  id: "m",
  kind: "stt",
  tier: "medium",
  name: "Whisper small",
  note_code: "stt_balanced",
  size_mb: 181,
  download_mb: 181,
  installed: false,
  fits: true,
  recommended: false,
  selected: false,
  blocked_code: "",
  blocked_params: {},
  ...over,
});

const show = (options: ModelOption[], onPick = vi.fn()) => {
  const kind: KindOptions = {
    kind: "stt",
    reason_code: "ample_ram",
    reason_params: { ram: "16 GB" },
    options,
  };
  render(
    <I18nProvider>
      <ModelPicker kind={kind} onPick={onPick} />
    </I18nProvider>,
  );
  return onPick;
};

describe("ModelPicker", () => {
  it("picks a model when its row is clicked", () => {
    const onPick = show([option({ id: "small", name: "Whisper base" })]);
    fireEvent.click(screen.getByText("Whisper base"));
    expect(onPick).toHaveBeenCalledWith("small");
  });

  it("shows a model the machine can't run, but refuses to select it", () => {
    const onPick = show([
      option({ id: "large", name: "Whisper large", fits: false,
               blocked_code: "needs_ram", blocked_params: { need: "16 GB" } }),
    ]);
    expect(screen.getByText("Needs about 16 GB of memory")).toBeTruthy();
    fireEvent.click(screen.getByText("Whisper large"));
    expect(onPick).not.toHaveBeenCalled();
  });

  it("never marks an unusable model as recommended", () => {
    // The engine shouldn't send this, but showing "recommended" on a row that
    // can't be clicked would be the worst of both.
    show([option({ fits: false, recommended: true, blocked_code: "needs_ram" })]);
    expect(screen.queryByText("recommended")).toBeNull();
  });

  it("distinguishes a download from a file already on disk", () => {
    show([
      option({ id: "a", name: "On disk", installed: true, download_mb: null }),
      option({ id: "b", name: "To fetch", size_mb: 181 }),
    ]);
    expect(screen.getByText("on disk")).toBeTruthy();
    expect(screen.getByText("181 MB")).toBeTruthy();
  });

  it("describes what each model is for", () => {
    show([option({ note_code: "stt_best" })]);
    expect(
      screen.getByText("Most accurate, especially with accents and mixed languages"),
    ).toBeTruthy();
  });

  it("marks the recommendation and the current selection separately", () => {
    show([
      option({ id: "a", name: "Recommended one", recommended: true }),
      option({ id: "b", name: "Chosen one", selected: true }),
    ]);
    expect(screen.getByText("recommended")).toBeTruthy();
    // Selection is styling, not a label — assert on the element that carries it.
    const chosen = screen.getByText("Chosen one").closest("button");
    expect(chosen?.className).toContain("border-brand-500");
  });
});
