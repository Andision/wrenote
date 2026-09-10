// Renders the engine's model reasons, as lib/computeText.ts does for runtimes.
//
// The engine ranks models and says why in codes ("gpu_headroom", {gpu}); the
// wording is the client's, so a new language is a locale file rather than an
// engine change.
import type { TFunction } from "@/i18n";
import type { KindOptions, ModelOption } from "@/lib/models";

/** Why this kind was ranked the way it was; "" when the engine gave no reason. */
export function kindReason(t: TFunction, kind: KindOptions): string {
  const key = `models.reason.${kind.reason_code}`;
  const text = t(key, kind.reason_params);
  return text === key ? "" : text;
}

/** What a model is for, or what stops this machine running it. */
export function modelNote(t: TFunction, option: ModelOption): string {
  if (!option.fits) {
    const key = `models.blocked.${option.blocked_code}`;
    const text = t(key, option.blocked_params);
    if (text !== key) return text;
  }
  const key = `models.note.${option.note_code}`;
  const text = t(key);
  return text === key ? "" : text;
}

/** One tag on a model row. `tone` says whether it reads as a problem. */
export interface ModelTag {
  key: string;
  label: string;
  tone: "neutral" | "blocked";
}

/**
 * The two things worth comparing at a glance, as tags.
 *
 * A paragraph per model was the previous answer and it served nobody: someone
 * who knows the models reads the name, and someone who doesn't is not helped
 * by prose. What both can use is where a model sits against the others (its
 * tier) and what it will cost the machine (its memory floor) — and the second
 * one turns into the reason it can't be chosen when the machine is short.
 * The sentence is still there, as the row's tooltip.
 */
export function modelTags(t: TFunction, option: ModelOption): ModelTag[] {
  const tags: ModelTag[] = [
    { key: "tier", label: t(`models.tier.${option.tier}`), tone: "neutral" },
  ];
  if (option.ram_mb > 0) {
    tags.push({
      key: "ram",
      label: t("models.ramTag", { gb: gbLabel(option.ram_mb) }),
      tone: option.fits ? "neutral" : "blocked",
    });
  }
  return tags;
}

/** 2048 → "2", 6144 → "6", 3072 → "3" — whole GB where it divides, else one
 *  decimal. A model's floor is always a round number of GB in practice. */
function gbLabel(mb: number): string {
  const gb = mb / 1024;
  return Number.isInteger(gb) ? String(gb) : gb.toFixed(1);
}
