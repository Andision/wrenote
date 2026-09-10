// `data-tip` is our own tooltip layer's attribute (see components/Tooltip.tsx),
// not an accessibility one. A control whose only content is an icon therefore
// had no accessible name at all — a screen reader read "button", 34 times over.
//
// `title=` would have given one, which is exactly why the app stopped using it:
// the native tooltip has no delay control, no placement and no styling. So the
// name has to be stated, and the only sensible source is the same text the
// tooltip shows. Spreading it from here keeps the two from drifting: a new icon
// button copied from a neighbour gets both or neither.
//
// Only for icon-only controls. On anything with visible text, `aria-label`
// *replaces* that text for a screen reader, which turns a correct name into a
// worse one — a model row labelled with its description instead of its name.

/** The tooltip and the accessible name of an icon-only control. */
export function iconTip(text: string): {
  "data-tip": string;
  "aria-label": string;
} {
  return { "data-tip": text, "aria-label": text };
}
