// Renders the engine's compute reasons.
//
// The engine reports *codes* and facts — "driver_too_old", {driver: "471.11"} —
// and never a sentence to display (see engine/wrenote/platform/base.py). This
// is where they become words, so a new language is a locale file rather than an
// engine change. `detail` / `note` on the payload are the engine's English
// rendering: a fallback for a code we don't have a message for yet.
import type { TFunction } from "@/i18n";
import type { AcceleratorNote, RuntimeOption } from "@/lib/compute";

export function hardwareText(t: TFunction, note: AcceleratorNote | null | undefined): string {
  if (!note) return "";
  const key = `compute.reason.${note.code}`;
  const text = t(key, note.params);
  return text === key ? note.detail : text;
}

export function optionText(t: TFunction, option: RuntimeOption): string {
  const key = `compute.note.${option.note_code}`;
  const text = t(key, { mb: option.download_mb ?? 0 });
  const rendered = text === key ? option.note : text;
  // Why anyone would take a heavier build is a judgement, not a fact the engine
  // knows — so it lives here, per variant, and only when there is a choice.
  const tradeoffKey = `compute.tradeoff.${option.variant}`;
  const tradeoff = t(tradeoffKey);
  if (option.note_code === "download" && tradeoff !== tradeoffKey) {
    return `${tradeoff} · ${rendered}`;
  }
  return rendered;
}
