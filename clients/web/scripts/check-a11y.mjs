#!/usr/bin/env node
// One accessibility invariant, checked: an interactive element whose only
// content is an icon has an accessible name.
//
// `data-tip` drives this app's own tooltip layer and is not an aria attribute,
// so 34 icon-only buttons read as "button" to a screen reader. `lib/tooltip.ts`
// is the fix (`{...iconTip(text)}` sets both); this is what keeps the next one
// from shipping without it.
//
// Not eslint-plugin-jsx-a11y: it peers on eslint ^9 and this project is on 10.
// When that changes, `jsx-a11y/control-has-associated-label` supersedes this
// file — it is the same rule, maintained by people who do only that.
//
// Deliberately a regex reader rather than a parser: it has one question to
// answer about one attribute shape. It errs toward reporting — a false
// positive is a line to look at, a false negative is a shipped defect — and
// anything it gets wrong can be silenced with an explicit `aria-label`.
import { readFileSync, readdirSync } from "node:fs";
import { join, dirname, relative } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const SRC = join(here, "..", "src");

/** Elements that are interactive, so a user can land on them and needs a name. */
const INTERACTIVE = new Set(["button", "Button", "a"]);

function walk(dir) {
  return readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const p = join(dir, e.name);
    if (e.isDirectory()) return e.name === "test" ? [] : walk(p);
    return /\.tsx$/.test(e.name) && !/\.test\.tsx$/.test(e.name) ? [p] : [];
  });
}

/** The `{…}` expression starting at `from`, and where it ends. */
function braced(src, from) {
  const open = src.indexOf("{", from);
  let depth = 0;
  for (let i = open; i < src.length; i++) {
    if (src[i] === "{") depth++;
    else if (src[i] === "}" && --depth === 0) return { text: src.slice(open + 1, i), end: i + 1 };
  }
  return null;
}

/** Whether an element's children include anything a screen reader would read
 *  as text. Icons are self-closing elements; a conditional between two of them
 *  is still icon-only, which is why the conditions are stripped first. */
function hasVisibleText(inner) {
  let s = inner.replace(/\{\/\*[\s\S]*?\*\/\}/g, ""); // {/* comments */}
  s = s.replace(/<[A-Za-z][\w.]*(?:\{[^{}]*\}|[^<>])*?\/>/g, ""); // <Icon … />
  // `cond ?` and `cond &&` choose between children; the condition is not text.
  s = s.replace(/[A-Za-z_$][\w$.?[\]"'`]*\s*(\?|&&)/g, "$1");
  s = s.replace(/[\s{}()?:&|!]/g, "");
  return s.length > 0;
}

const problems = [];
for (const file of walk(SRC)) {
  const src = readFileSync(file, "utf8");
  for (const m of src.matchAll(/data-tip=/g)) {
    const openAt = src.lastIndexOf("<", m.index);
    const name = /^<([A-Za-z][\w.]*)/.exec(src.slice(openAt))?.[1];
    if (!name || !INTERACTIVE.has(name)) continue;

    // The open tag ends at the first `>` outside any braced expression.
    let depth = 0;
    let close = m.index;
    for (; close < src.length; close++) {
      const c = src[close];
      if (c === "{") depth++;
      else if (c === "}") depth--;
      else if (c === ">" && depth === 0) break;
    }
    const tag = src.slice(openAt, close + 1);
    if (/aria-label[=\s]/.test(tag) || /\biconTip\(/.test(tag)) continue;
    if (/aria-labelledby[=\s]/.test(tag)) continue;

    let inner = "";
    if (src[close - 1] !== "/") {
      const end = src.indexOf(`</${name}>`, close);
      inner = end === -1 ? "" : src.slice(close + 1, end);
    }
    const iconSized = /size="icon/.test(tag);
    if (iconSized || !hasVisibleText(inner)) {
      const line = src.slice(0, m.index).split("\n").length;
      problems.push(
        `${relative(join(here, ".."), file)}:${line}: <${name}> has a tooltip but no ` +
          `accessible name — wrap the text in iconTip() from @/lib/tooltip`,
      );
    }
  }
}

if (problems.length) {
  for (const p of problems) console.error("  " + p);
  console.error(`\n${problems.length} icon-only control(s) with no accessible name`);
  process.exit(1);
}
console.log("a11y ok — every icon-only control with a tooltip names itself");
