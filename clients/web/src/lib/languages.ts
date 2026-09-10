// Language lists shared by the source/target dropdowns. Data, not components,
// so it lives outside LanguageSelect.tsx — a component file that also exports
// values is a file Fast Refresh has to reload whole.
export interface LanguageOption {
  value: string;
  label: string;
}

// Spoken-language names stay in their own script — an endonym is right in any
// UI language. Only "auto" is our word for something, so it carries a key.
export const TARGET_LANGUAGES: LanguageOption[] = [
  { value: "en", label: "English" },
  { value: "zh", label: "中文" },
  { value: "ja", label: "日本語" },
  { value: "ko", label: "한국어" },
  { value: "es", label: "Español" },
  { value: "fr", label: "Français" },
  { value: "de", label: "Deutsch" },
  { value: "ru", label: "Русский" },
];

export const AUTO_LANGUAGE_KEY = "lang.auto";

export const SOURCE_LANGUAGES: LanguageOption[] = [
  { value: "auto", label: AUTO_LANGUAGE_KEY },
  ...TARGET_LANGUAGES,
];
