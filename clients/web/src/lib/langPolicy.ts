// A session's languages (engine core/lang.py LanguagePolicy): a main
// language, plus the others that may come up. With a main language pinned,
// the engine detects only among these, and a secondary one has to be
// heard with confidence before it replaces the main one for a segment.

/** What "also spoken" should be when the main language changes: the
 *  translation target, if it is a different language — a Chinese meeting
 *  translated to English is one where English will be spoken. */
export function defaultSecondaryLangs(mainLang: string, tgtLang: string): string[] {
  if (mainLang === "auto" || !tgtLang || tgtLang === mainLang) return [];
  return [tgtLang];
}

/** Toggle `lang` in the list, never letting the main language in. */
export function toggleSecondary(list: string[], lang: string, mainLang: string): string[] {
  if (lang === mainLang) return list.filter((l) => l !== mainLang);
  return list.includes(lang) ? list.filter((l) => l !== lang) : [...list, lang];
}
