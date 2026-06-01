// The golden angle spreads sequential hues as far apart as possible, so even
// a long list of speakers stays visually separable — no two adjacent indices
// land on near-identical colors.
const GOLDEN_ANGLE = 137.508;

/** Computable palette entry: color for the i-th distinct speaker. */
export function colorForSpeakerIndex(index: number): string {
  const hue = (index * GOLDEN_ANGLE) % 360;
  return `hsl(${hue.toFixed(1)} 60% 50%)`;
}

// A curated set of distinct, pleasant colors offered when the user wants to
// recolor a speaker by hand (overriding the computed palette).
export const SPEAKER_SWATCHES = [
  "#e11d48", // rose
  "#ea580c", // orange
  "#d97706", // amber
  "#65a30d", // lime
  "#059669", // emerald
  "#0891b2", // cyan
  "#2563eb", // blue
  "#7c3aed", // violet
  "#c026d3", // fuchsia
  "#64748b", // slate
];

/**
 * Build a stable label→color map from segments in display order. Colors are
 * assigned by order of first appearance so the same speaker always keeps the
 * same color across the transcript and the timeline. "unknown"/unlabeled
 * segments are skipped (rendered neutral).
 */
export function buildSpeakerPalette(
  labels: (string | null | undefined)[],
): Map<string, string> {
  const map = new Map<string, string>();
  let index = 0;
  for (const label of labels) {
    if (!label || label === "unknown") continue;
    if (!map.has(label)) {
      map.set(label, colorForSpeakerIndex(index));
      index += 1;
    }
  }
  return map;
}

// Stable, distinct color for a single speaker label without palette context
// (e.g. one-off chips). Hashes the label to an index so it stays consistent.
export function speakerColor(label: string | null | undefined): string | null {
  if (!label || label === "unknown") return null;
  const m = /^Speaker (\d+)$/.exec(label);
  if (m) return colorForSpeakerIndex(parseInt(m[1], 10) - 1);
  let h = 0;
  for (let i = 0; i < label.length; i++) {
    h = (h * 31 + label.charCodeAt(i)) >>> 0;
  }
  return colorForSpeakerIndex(h % 997);
}

export function formatRelativeTime(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds));
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m.toString().padStart(2, "0")}:${s.toString().padStart(2, "0")}`;
}
