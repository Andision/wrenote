// Holds the active locale and hands the rest of the app its `t`.
import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";

import { I18nContext } from "./context";
import {
  FALLBACK_LOCALE,
  LOCALES,
  detectLocale,
  resolveLocale,
  translate,
  type LocalePreference,
  type TFunction,
} from "./messages";

const STORAGE_KEY = "wrenote.locale";

function readPreference(): LocalePreference {
  try {
    return localStorage.getItem(STORAGE_KEY) || "auto";
  } catch {
    return "auto"; // private mode / storage blocked
  }
}

export function I18nProvider({ children }: { children: ReactNode }) {
  const [preference, setPreferenceState] = useState<LocalePreference>(readPreference);
  const locale = useMemo(
    () => (preference === "auto" ? detectLocale() : (resolveLocale(preference) ?? FALLBACK_LOCALE)),
    [preference],
  );

  const setPreference = useCallback((p: LocalePreference) => {
    setPreferenceState(p);
    try {
      localStorage.setItem(STORAGE_KEY, p);
    } catch {
      /* not fatal: the choice just won't survive a restart */
    }
  }, []);

  useEffect(() => {
    // Screen readers, hyphenation and CJK font selection all key off this.
    document.documentElement.lang = locale;
  }, [locale]);

  const t = useCallback<TFunction>(
    (key, params) =>
      translate(
        LOCALES[locale]?.messages ?? {},
        LOCALES[FALLBACK_LOCALE]?.messages ?? {},
        locale,
        key,
        params,
      ),
    [locale],
  );

  const value = useMemo(
    () => ({ locale, preference, setPreference, t }),
    [locale, preference, setPreference, t],
  );
  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}
