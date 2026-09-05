import { describe, expect, it } from "vitest";

import { defaultSecondaryLangs, toggleSecondary } from "@/lib/langPolicy";

describe("defaultSecondaryLangs", () => {
  it("is the target language when it differs from the main one", () => {
    expect(defaultSecondaryLangs("zh", "en")).toEqual(["en"]);
    expect(defaultSecondaryLangs("en", "en")).toEqual([]);
    expect(defaultSecondaryLangs("auto", "zh")).toEqual([]);
  });
});

describe("toggleSecondary", () => {
  it("adds, removes, and never admits the main language", () => {
    expect(toggleSecondary([], "en", "zh")).toEqual(["en"]);
    expect(toggleSecondary(["en"], "en", "zh")).toEqual([]);
    expect(toggleSecondary(["en", "zh"], "zh", "zh")).toEqual(["en"]);
  });
});
