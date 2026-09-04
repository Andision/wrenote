// The locale context and its hooks. Kept apart from the provider component so
// neither file mixes components with plain exports (Fast Refresh needs that).
import { createContext, useContext } from "react";

import type { LocalePreference, TFunction } from "./messages";

export interface I18nValue {
  locale: string;
  preference: LocalePreference;
  setPreference: (p: LocalePreference) => void;
  t: TFunction;
}

export const I18nContext = createContext<I18nValue | null>(null);

export function useI18n(): I18nValue {
  const ctx = useContext(I18nContext);
  if (!ctx) throw new Error("useI18n must be used inside <I18nProvider>");
  return ctx;
}

/** The common case: `const t = useT()` then `t("settings.title")`. */
export function useT(): TFunction {
  return useI18n().t;
}
