// Rendering the engine's reason codes into words.
//
// This is the seam the localization design rests on: the engine sends
// `("driver_too_old", {driver: "471.11"})` and the client owns the sentence. If
// a code loses its message the UI silently shows a bare key or an English
// string in a Chinese interface, which no other check would catch — the key
// exists, it's just the wrong one.
import { describe, expect, it } from "vitest";

import { LOCALES, translate, type TFunction } from "@/i18n";
import { hardwareText, optionText } from "@/lib/computeText";
import { kindReason, modelNote } from "@/lib/modelText";
import type { AcceleratorNote, RuntimeOption } from "@/lib/compute";
import type { KindOptions, ModelOption } from "@/lib/models";

const tFor =
  (locale: string): TFunction =>
  (key, params) =>
    translate(LOCALES[locale].messages, LOCALES.en.messages, locale, key, params);

const t = tFor("en");
const tZh = tFor("zh-CN");

const note = (over: Partial<AcceleratorNote> = {}): AcceleratorNote => ({
  variant: "cuda",
  usable: true,
  code: "gpu_ready_driver",
  params: { gpu: "NVIDIA GeForce RTX 4070", driver: "566.36" },
  detail: "NVIDIA GeForce RTX 4070 · driver 566.36",
  ...over,
});

const option = (over: Partial<RuntimeOption> = {}): RuntimeOption => ({
  variant: "vulkan",
  usable: true,
  installed: false,
  builtin: false,
  recommended: true,
  accelerated: true,
  note_code: "download",
  note: "36 MB download",
  download_mb: 36,
  hardware: note({ variant: "vulkan", code: "gpu_ready", params: { gpu: "RTX 4070" } }),
  ...over,
});

describe("hardwareText", () => {
  it("renders a code with its facts", () => {
    expect(hardwareText(t, note())).toBe("NVIDIA GeForce RTX 4070 · driver 566.36");
  });

  it("renders the same code in the active language", () => {
    expect(hardwareText(tZh, note())).toBe("NVIDIA GeForce RTX 4070 · 驱动 566.36");
  });

  it("explains a blocker with its numbers", () => {
    const blocked = note({
      usable: false,
      code: "driver_too_old",
      params: { gpu: "GTX 1060", driver: "471.11", min: "527" },
    });
    expect(hardwareText(t, blocked)).toContain("471.11");
    expect(hardwareText(t, blocked)).toContain("527");
    expect(hardwareText(tZh, blocked)).toContain("驱动 471.11");
  });

  it("falls back to the engine's English for a code we have no message for", () => {
    // A newer engine may know a reason this client doesn't. Showing its own
    // words beats showing "compute.reason.some_new_code".
    const unknown = note({ code: "invented_by_a_newer_engine", detail: "Something specific" });
    expect(hardwareText(t, unknown)).toBe("Something specific");
  });

  it("is empty when there is no note", () => {
    expect(hardwareText(t, null)).toBe("");
    expect(hardwareText(t, undefined)).toBe("");
  });
});

describe("optionText", () => {
  it("renders the download size", () => {
    expect(optionText(t, option())).toBe("36 MB download");
  });

  it("adds the tradeoff for a variant where the choice is one", () => {
    // Why anyone would take the 700 MB build is a judgement, not a fact the
    // engine states — so it lives in the locale, per variant.
    const cuda = option({ variant: "cuda", download_mb: 736, note: "736 MB download" });
    expect(optionText(t, cuda)).toBe("Faster on NVIDIA, much larger · 736 MB download");
    expect(optionText(tZh, cuda)).toContain("N 卡上更快");
  });

  it("does not add a tradeoff to an installed or built-in option", () => {
    const builtin = option({ variant: "cuda", note_code: "builtin", download_mb: null });
    expect(optionText(t, builtin)).toBe("Built into the app · nothing to download");
  });

  it("falls back to the engine's wording for an unknown note code", () => {
    const odd = option({ note_code: "brand_new", note: "Engine says this" });
    expect(optionText(t, odd)).toBe("Engine says this");
  });
});

const kind = (over: Partial<KindOptions> = {}): KindOptions => ({
  kind: "stt",
  reason_code: "ample_ram",
  reason_params: { ram: "16 GB" },
  options: [],
  ...over,
});

const model = (over: Partial<ModelOption> = {}): ModelOption => ({
  id: "whisper-small-q5",
  kind: "stt",
  tier: "medium",
  name: "Whisper small (Q5)",
  note_code: "stt_balanced",
  size_mb: 181,
  download_mb: 181,
  installed: false,
  fits: true,
  recommended: true,
  selected: false,
  blocked_code: "",
  blocked_params: {},
  ...over,
});

describe("kindReason", () => {
  it("renders the hardware verdict", () => {
    expect(kindReason(t, kind())).toBe("16 GB RAM — the best models fit");
    expect(kindReason(tZh, kind())).toContain("16 GB 内存");
  });

  it("renders the GPU verdict with the card's name", () => {
    const gpu = kind({ reason_code: "gpu_headroom", reason_params: { gpu: "RTX 4070" } });
    expect(kindReason(t, gpu)).toContain("RTX 4070");
  });

  it("is empty for a reason we have no message for", () => {
    // Rendered as a subtitle; a bare key there would look like a bug.
    expect(kindReason(t, kind({ reason_code: "from_the_future" }))).toBe("");
  });
});

describe("modelNote", () => {
  it("describes what a model is for", () => {
    expect(modelNote(t, model())).toBe("A good balance for most machines");
  });

  it("prefers the blocker when the machine can't run it", () => {
    const blocked = model({
      fits: false,
      blocked_code: "needs_ram",
      blocked_params: { need: "16 GB" },
    });
    expect(modelNote(t, blocked)).toBe("Needs about 16 GB of memory");
    expect(modelNote(tZh, blocked)).toBe("大约需要 16 GB 内存");
  });

  it("falls back to the description when a blocker has no message", () => {
    const blocked = model({ fits: false, blocked_code: "mystery" });
    expect(modelNote(t, blocked)).toBe("A good balance for most machines");
  });
});
