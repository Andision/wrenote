import { API_BASE as BASE } from "./api";
// Transcript export — fetch the backend-rendered text, then copy or save it
// client-side (one source of truth: the formatting lives in core/export.py).

export type ExportFormat = "md" | "txt" | "srt" | "vtt";
export type ExportContent = "original" | "translation" | "both";

export async function fetchExport(
  sessionId: string,
  fmt: ExportFormat,
  content: ExportContent,
): Promise<string> {
  const res = await fetch(
    `${BASE}/sessions/${encodeURIComponent(sessionId)}/export?fmt=${fmt}&content=${content}`,
  );
  if (!res.ok) throw new Error(`export failed (${res.status})`);
  return res.text();
}

const MIME: Record<ExportFormat, string> = {
  md: "text/markdown",
  txt: "text/plain",
  srt: "application/x-subrip",
  vtt: "text/vtt",
};

/** Trigger a client-side file download of `text`. */
export function downloadText(baseName: string, fmt: ExportFormat, text: string): void {
  const safe = (baseName || "transcript").replace(/[/\\?%*:|"<>]/g, "_").slice(0, 80);
  const blob = new Blob([text], { type: MIME[fmt] });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${safe}.${fmt}`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
