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
  "settings.cat.", // settings.cat.${id}
  "compute.selection.", // compute.selection.${reason}, from the engine
  "compute.reason.", // compute.reason.${code}, from the engine
  "compute.note.", // compute.note.${note_code}, from the engine
  "compute.tradeoff.", // compute.tradeoff.${variant}
  "lang.", // LanguageSelect resolves "lang.*" labels
  "export.format.", // the FORMATS table holds keys
];

function walk(dir) {
  return readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const p = join(dir, e.name);
    if (e.isDirectory()) return walk(p);
    return /\.tsx?$/.test(e.name) ? [p] : [];
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

const problems = [];
const isDynamic = (k) => DYNAMIC_PREFIXES.some((p) => k.startsWith(p));

for (const key of [...used].sort()) {
  if (!(key in base)) problems.push(`missing in ${FALLBACK}: ${key}`);
}
for (const [tag, messages] of Object.entries(locales)) {
  if (tag === FALLBACK) continue;
  for (const key of Object.keys(base)) {
    if (!(key in messages)) problems.push(`missing in ${tag}: ${key}`);
  }
  for (const key of Object.keys(messages)) {
    if (!(key in base)) problems.push(`not in ${FALLBACK} (stale?): ${tag}: ${key}`);
  }
}
for (const key of Object.keys(base)) {
  if (!used.has(key) && !isDynamic(key)) problems.push(`unused: ${key}`);
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
