// The message lookup every string in the app goes through.
//
// `npm run check:locales` already proves the *keys* line up; these prove the
// lookup itself — interpolation, plural selection, and the fallback chain that
// decides what a user sees when a translation is missing.
import { describe, expect, it } from "vitest";

import {
  FALLBACK_LOCALE,
  LOCALES,
  LOCALE_LIST,
  detectLocale,
  resolveLocale,
  translate,
  type Messages,
} from "./messages";

const en: Messages = {
  plain: "Settings",
  named: "Step {current} of {total}",
  repeated: "{a} and {a}",
  files_one: "1 file",
  files_other: "{count} files",
  onlyOther_other: "{count} things",
};
const zh: Messages = { plain: "设置", files_other: "{count} 个文件" };

const t = (key: string, params?: Record<string, string | number>, locale = "en") =>
  translate(locale === "en" ? en : zh, en, locale, key, params);

describe("translate", () => {
  it("returns the message for a key", () => {
    expect(t("plain")).toBe("Settings");
  });

  it("interpolates named placeholders, including repeats", () => {
    expect(t("named", { current: 1, total: 2 })).toBe("Step 1 of 2");
    expect(t("repeated", { a: "x" })).toBe("x and x");
  });

  it("leaves a placeholder alone when no value is given", () => {
    // Better a visible {total} in review than an empty gap in the sentence.
    expect(t("named", { current: 1 })).toBe("Step 1 of {total}");
  });

  it("renders a missing key as the key", () => {
    // Visible in review and in a screenshot; never a crash or a blank.
    expect(t("nope.missing")).toBe("nope.missing");
  });

  it("falls back to English for a key the locale lacks", () => {
    expect(t("named", { current: 1, total: 2 }, "zh-CN")).toBe("Step 1 of 2");
    expect(t("plain", undefined, "zh-CN")).toBe("设置");
  });

  describe("plurals", () => {
    it("picks the language's category", () => {
      expect(t("files", { count: 1 })).toBe("1 file");
      expect(t("files", { count: 3 })).toBe("3 files");
    });

    it("uses _other for languages with a single form", () => {
      // zh has no "one" category — Intl.PluralRules says "other" for 1.
      expect(t("files", { count: 1 }, "zh-CN")).toBe("1 个文件");
    });

    it("falls back to _other when the exact category is absent", () => {
      expect(t("onlyOther", { count: 1 })).toBe("1 things");
    });

    it("ignores plural forms when count is absent", () => {
      expect(t("files")).toBe("files"); // no bare `files` key exists
    });
  });
});

describe("resolveLocale", () => {
  it("prefers an exact match", () => {
    expect(resolveLocale("zh-CN")).toBe("zh-CN");
    expect(resolveLocale("en")).toBe("en");
  });

  it("falls back to a regional variant of the same language", () => {
    // We ship no zh-TW; Simplified beats English for a Traditional reader.
    expect(resolveLocale("zh-TW")).toBe("zh-CN");
    expect(resolveLocale("zh-Hant-HK")).toBe("zh-CN");
  });

  it("is undefined for a language we don't ship", () => {
    expect(resolveLocale("fr-FR")).toBeUndefined();
    expect(resolveLocale(undefined)).toBeUndefined();
  });
});

describe("detectLocale", () => {
  const withLanguages = (languages: string[]) => {
    Object.defineProperty(navigator, "languages", { value: languages, configurable: true });
  };

  it("takes the first shipped language the OS asks for", () => {
    withLanguages(["zh-CN", "en-US"]);
    expect(detectLocale()).toBe("zh-CN");
  });

  it("skips languages we don't ship", () => {
    withLanguages(["fr-FR", "de-DE", "zh-CN"]);
    expect(detectLocale()).toBe("zh-CN");
  });

  it("falls back to English when nothing matches", () => {
    withLanguages(["fr-FR"]);
    expect(detectLocale()).toBe(FALLBACK_LOCALE);
  });
});

describe("the shipped locales", () => {
  it("loads every JSON file in ./locales", () => {
    expect(Object.keys(LOCALES).sort()).toEqual(["en", "zh-CN"]);
  });

  it("gives each locale a name to show in the picker", () => {
    // The picker is data-driven, so a file without $meta.name would list its tag.
    expect(LOCALES["zh-CN"].name).toBe("简体中文");
    expect(LOCALES.en.name).toBe("English");
  });

  it("lists English first, then the rest alphabetically", () => {
    expect(LOCALE_LIST[0].tag).toBe(FALLBACK_LOCALE);
  });

  it("flattens nested JSON into dotted keys", () => {
    expect(LOCALES.en.messages["settings.cat.general"]).toBe("General");
  });

  it("keeps $meta out of the messages", () => {
    expect(Object.keys(LOCALES.en.messages).some((k) => k.startsWith("$meta"))).toBe(false);
  });
});
