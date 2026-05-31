// Golden-angle palette: stable distinct color for an arbitrary speaker label.
export function speakerColor(label: string | null | undefined): string | null {
  if (!label || label === "unknown") return null;
  const m = /^Speaker (\d+)$/.exec(label);
  if (!m) return null;
  const n = parseInt(m[1], 10);
  const hue = (n * 137.508) % 360;
  return `hsl(${hue}, 65%, 45%)`;
}

export function formatRelativeTime(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds));
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m.toString().padStart(2, "0")}:${s.toString().padStart(2, "0")}`;
}
