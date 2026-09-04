// UI localization.
//
// Adding a language is meant to be one action: drop `<tag>.json` into
// `./locales/`. Vite's import.meta.glob picks it up at build time, the picker
// in Settings lists it from its own `$meta.name`, and no component changes.
// That is the whole design constraint — locales are data, not code.
//
// Human-facing text lives *here*, never in the engine. The engine reports codes
// and facts ("driver_too_old", {driver: "471.11"}); the client turns them into
// a sentence. Otherwise a second translation system grows server-side and the
// two drift.
//
// Deliberately small: message lookup with `{placeholder}` interpolation and
// Intl plural selection. No namespaces, no lazy loading — the whole UI is a few
// hundred strings, and a missing-key bug is worth catching at `npm run
// check:locales` rather than at runtime in a language nobody on the team reads.

export type Messages = Record<string, string>;

interface LocaleFile {
  /** Not a message: the language's own name, for the picker. */
  $meta?: { name?: string };
  [key: string]: unknown;
}

/** The language every other locale falls back to, key by key. */
export const FALLBACK_LOCALE = "en";
const files = import.meta.glob<LocaleFile>("./locales/*.json", { eager: true });

function flatten(obj: unknown, prefix = "", out: Messages = {}): Messages {
  if (typeof obj !== "object" || obj === null) return out;
  for (const [k, v] of Object.entries(obj as Record<string, unknown>)) {
    const key = prefix ? `${prefix}.${k}` : k;
    if (typeof v === "string") out[key] = v;
    else if (typeof v === "object" && v !== null) flatten(v, key, out);
  }
  return out;
}

export interface Locale {
  tag: string; // BCP 47, e.g. "zh-CN"
  name: string; // endonym, e.g. "中文"
  messages: Messages;
}

/** Every locale that shipped, by tag. Built at module load from the JSON files. */
export const LOCALES: Record<string, Locale> = Object.fromEntries(
  Object.entries(files).map(([path, mod]) => {
    const tag = path.replace(/^.*\/(.+)\.json$/, "$1");
    const { $meta, ...rest } = mod;
    return [tag, { tag, name: $meta?.name ?? tag, messages: flatten(rest) }];
  }),
);

export const LOCALE_LIST: Locale[] = Object.values(LOCALES).sort((a, b) =>
  a.tag === FALLBACK_LOCALE ? -1 : b.tag === FALLBACK_LOCALE ? 1 : a.tag.localeCompare(b.tag),
);

/**
 * Best shipped locale for a BCP 47 tag: exact match, then the base language
 * ("zh-TW" → "zh"), then any regional variant of it ("zh" → "zh-CN").
 */
export function resolveLocale(tag: string | undefined): string | undefined {
  if (!tag) return undefined;
  if (LOCALES[tag]) return tag;
  const base = tag.split("-")[0].toLowerCase();
  if (LOCALES[base]) return base;
  return Object.keys(LOCALES).find((t) => t.split("-")[0].toLowerCase() === base);
}

/** What the browser (i.e. the OS) asks for, among what we ship. */
export function detectLocale(): string {
  for (const tag of navigator.languages ?? [navigator.language]) {
    const hit = resolveLocale(tag);
    if (hit) return hit;
  }
  return FALLBACK_LOCALE;
}

export type LocalePreference = "auto" | string;

export type TParams = Record<string, string | number>;

/**
 * Look `key` up, interpolate `{placeholders}`, and pick a plural form when
 * `params.count` is present (`key_one` / `key_other`, per Intl.PluralRules).
 * A missing key renders as the key itself — visible in review, never a crash.
 */
export function translate(
  messages: Messages,
  fallback: Messages,
  locale: string,
  key: string,
  params?: TParams,
): string {
  let lookup = key;
  if (params && typeof params.count === "number") {
    const form = new Intl.PluralRules(locale).select(params.count);
    if (`${key}_${form}` in messages || `${key}_${form}` in fallback) lookup = `${key}_${form}`;
    else if (`${key}_other` in messages || `${key}_other` in fallback) lookup = `${key}_other`;
  }
  const template = messages[lookup] ?? fallback[lookup] ?? key;
  if (!params) return template;
  return template.replace(/\{(\w+)\}/g, (whole, name: string) =>
    name in params ? String(params[name]) : whole,
  );
}

export type TFunction = (key: string, params?: TParams) => string;
