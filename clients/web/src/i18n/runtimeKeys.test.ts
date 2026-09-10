// Keys the UI composes at render (`t(`settings.cat.${id}`)`) are invisible to
// `npm run check:locales`, which finds keys by scanning for `t("literal")`.
// Two bugs shipped through that hole, and both are held here:
//
//   * `settings.cat.models` had no message and the rail read "settings.cat.models";
//   * the whole `export.*` section never loaded, because `import.meta.glob`
//     hands back a module namespace and `export` is a reserved word, so the
//     JSON's top-level `"export"` key had no named export to be spread from.
//     The loader reads the default export now — this checks it stayed that way.
import { describe, expect, it } from "vitest";

import { SETTINGS_CATEGORIES } from "@/components/settingsCategories";
import { FALLBACK_LOCALE, LOCALE_LIST, LOCALES } from "@/i18n";

/** Closed sets the client itself owns, each named by where it is declared. */
const RUNTIME_KEYS: string[] = [
  // components/settingsCategories.ts
  ...SETTINGS_CATEGORIES.map((c) => `settings.cat.${c.id}`),
  // components/ThemeToggle.tsx — ORDER
  ...["light", "dark", "system"].map((m) => `theme.${m}`),
  // components/ExportMenu.tsx — FORMATS
  ...["md", "txt", "srt", "vtt"].map((f) => `export.format.${f}`),
  // engine/models.yaml — every `kind:` a catalogue slot can have
  ...["stt", "stt_offline", "translator", "chat", "speaker"].map(
    (k) => `models.kind.${k}`,
  ),
];

describe("runtime-composed message keys", () => {
  it.each(LOCALE_LIST.map((l) => l.tag))("%s defines every one", (tag) => {
    const missing = RUNTIME_KEYS.filter((k) => !(k in LOCALES[tag].messages));
    expect(missing).toEqual([]);
  });

  it("loads every top-level section, reserved words included", () => {
    // `export` is the one that bit us; assert the section, not just one key.
    const messages = LOCALES[FALLBACK_LOCALE].messages;
    expect(messages["export.tooltip"]).toBe("Export transcript");
    expect(Object.keys(messages).filter((k) => k.startsWith("export."))).not.toEqual([]);
    // …and nothing leaked in under the module namespace's `default`.
    expect(Object.keys(messages).filter((k) => k.startsWith("default."))).toEqual([]);
  });
});
