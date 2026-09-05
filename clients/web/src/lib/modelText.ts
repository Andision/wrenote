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
