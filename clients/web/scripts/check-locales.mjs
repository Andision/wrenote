#!/usr/bin/env node
// Locale sanity check — run by `npm run check:locales`.
//
// Three failures worth catching before a user sees them:
//   1. a `t("key")` in the source with no message anywhere    → renders the key
//   2. a locale missing keys that `en` has                    → silent English
//   3. a message nothing references                           → dead weight
//
// Keys built at runtime (`t(`theme.${mode}`)`) can't be found by reading the
// source, so their prefixes are declared below. Keep that list short: a prefix
// is a hole in check 3.
import { readFileSync, readdirSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const SRC = join(here, "..", "src");
const LOCALES = join(SRC, "i18n", "locales");
const FALLBACK = "en";

/** Prefixes whose keys are composed at runtime; exempt from the unused check. */
const DYNAMIC_PREFIXES = [
  "theme.", // theme.${mode}
  "models.kind.", // models.kind.${kind}
  "models.reason.", // models.reason.${code}, from the engine
  "models.blocked.", // models.blocked.${code}, from the engine
  "models.note.", // models.note.${note_code}, from the catalogue
  "settings.cat.", // settings.cat.${id}
  "compute.selection.", // compute.selection.${reason}, from the engine
  "compute.reason.", // compute.reason.${code}, from the engine
  "compute.note.", // compute.note.${note_code}, from the engine
  "compute.tradeoff.", // compute.tradeoff.${variant}
  "update.error.", // update.error.${code}, from the engine
  "session.refuse.", // session.refuse.${code}, from the engine
  "lang.", // LanguageSelect resolves "lang.*" labels
  "export.format.", // the FORMATS table holds keys
];

// Test files are skipped: their `t("plain")` fixtures are made-up keys, not
// app strings, and counting them would demand messages for them.
const isTest = (name) => /\.test\.tsx?$/.test(name);

function walk(dir) {
  return readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const p = join(dir, e.name);
    if (e.isDirectory()) return e.name === "test" ? [] : walk(p);
    return /\.tsx?$/.test(e.name) && !isTest(e.name) ? [p] : [];
  });
}

function flatten(obj, prefix = "", out = {}) {
  for (const [k, v] of Object.entries(obj)) {
    if (k === "$meta") continue;
    const key = prefix ? `${prefix}.${k}` : k;
    if (typeof v === "string") out[key] = v;
    else if (v && typeof v === "object") flatten(v, key, out);
  }
  return out;
}

// `t("…")` where t is the whole identifier — not the tail of `createElement(`.
const CALL = /(?<![A-Za-z0-9_$.])t\(\s*"([^"]+)"/g;

const used = new Set();
for (const file of walk(SRC)) {
  const text = readFileSync(file, "utf8");
  for (const m of text.matchAll(CALL)) used.add(m[1]);
}

const locales = {};
for (const f of readdirSync(LOCALES).filter((f) => f.endsWith(".json"))) {
  locales[f.replace(/\.json$/, "")] = flatten(JSON.parse(readFileSync(join(LOCALES, f), "utf8")));
}

const base = locales[FALLBACK];
if (!base) {
  console.error(`no ${FALLBACK}.json in ${LOCALES}`);
  process.exit(1);
}

// `t("x", {count})` looks up x_one / x_other (Intl.PluralRules); the source
// only ever mentions the base, so treat a base and its plural forms as one key.
const PLURAL_FORMS = ["zero", "one", "two", "few", "many", "other"];
const pluralBase = (k) => {
  const cut = k.lastIndexOf("_");
  return cut > 0 && PLURAL_FORMS.includes(k.slice(cut + 1)) ? k.slice(0, cut) : k;
};
const defined = (messages, key) =>
  key in messages || PLURAL_FORMS.some((f) => `${key}_${f}` in messages);

const problems = [];
const isDynamic = (k) => DYNAMIC_PREFIXES.some((p) => k.startsWith(p));

for (const key of [...used].sort()) {
  if (!defined(base, key)) problems.push(`missing in ${FALLBACK}: ${key}`);
}
for (const [tag, messages] of Object.entries(locales)) {
  if (tag === FALLBACK) continue;
  for (const key of Object.keys(base)) {
    // Plural categories differ by language: zh has only "other", en needs
    // "one" too. Requiring the same forms everywhere would be wrong, so the
    // check is that the *key* is covered, in whatever forms that language uses.
    if (!(key in messages) && !defined(messages, pluralBase(key))) {
      problems.push(`missing in ${tag}: ${pluralBase(key)}`);
    }
  }
  for (const key of Object.keys(messages)) {
    if (!defined(base, pluralBase(key))) {
      problems.push(`not in ${FALLBACK} (stale?): ${tag}: ${key}`);
    }
  }
}
for (const key of Object.keys(base)) {
  const base_ = pluralBase(key);
  if (!used.has(key) && !used.has(base_) && !isDynamic(key)) {
    problems.push(`unused: ${key}`);
  }
}

const tags = Object.keys(locales).sort();
if (problems.length) {
  for (const p of problems) console.error("  " + p);
  console.error(`\n${problems.length} problem(s) across ${tags.join(", ")}`);
  process.exit(1);
}
console.log(
  `locales ok — ${Object.keys(base).length} keys × ${tags.length} languages (${tags.join(", ")})`,
);
