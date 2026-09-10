import { API_BASE as BASE } from "./api";
// Transcript export. The formatting lives in core/export.py, so both paths
// here go through the engine: `fetchExport` for copy-to-clipboard, and
// `saveExport` to write the file.
//
// Saving used to be a blob download (`<a download>` on an object URL). In a
// WebView that lands somewhere the app can neither choose nor name — so the
// user got a file and no way to tell whether, or where. The engine runs on
// the same machine, so it writes the file and answers with the absolute path;
// `data.exports_dir` is the "choose where" half, and the toast names both.

export type ExportFormat = "md" | "txt" | "srt" | "vtt";
export type ExportContent = "original" | "translation" | "both";

export async function fetchExport(
  sessionId: string,
  fmt: ExportFormat,
  content: ExportContent,
  /** Put this language's minutes before the transcript (md / txt only). */
  minutesLang?: string,
): Promise<string> {
  const minutes =
    minutesLang && (fmt === "md" || fmt === "txt")
      ? `&minutes=${encodeURIComponent(minutesLang)}`
      : "";
  const res = await fetch(
    `${BASE}/sessions/${encodeURIComponent(sessionId)}/export?fmt=${fmt}&content=${content}${minutes}`,
  );
  if (!res.ok) throw new Error(`export failed (${res.status})`);
  return res.text();
}

/** Where a saved export went. `dir` is `data.exports_dir`, resolved. */
export interface SavedFile {
  path: string;
  filename: string;
  dir: string;
  bytes: number;
}

export async function saveExport(
  sessionId: string,
  fmt: ExportFormat,
  content: ExportContent,
  minutesLang?: string,
): Promise<SavedFile> {
  const res = await fetch(
    `${BASE}/sessions/${encodeURIComponent(sessionId)}/export/save`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        fmt,
        content,
        minutes: minutesLang && (fmt === "md" || fmt === "txt") ? minutesLang : "",
      }),
    },
  );
  if (!res.ok) throw new Error(`save failed (${res.status})`);
  return (await res.json()) as SavedFile;
}

export async function saveMinutes(sessionId: string, lang: string): Promise<SavedFile> {
  const res = await fetch(
    `${BASE}/sessions/${encodeURIComponent(sessionId)}/minutes/save?lang=${encodeURIComponent(lang)}`,
    { method: "POST" },
  );
  if (!res.ok) throw new Error(`save failed (${res.status})`);
  return (await res.json()) as SavedFile;
}

/**
 * Show a saved file in the OS file manager.
 *
 * Through the engine, not the shell: a page may not navigate to `file://`
 * — the browser blocks it silently — and Tauri's opener refuses the scheme
 * too, so the button did nothing at all. The engine is a local process and
 * can just ask the desktop; it only accepts paths in directories it writes.
 */
export async function revealSaved(file: SavedFile): Promise<void> {
  return revealPath(file.path);
}

/** Show a path — a file or a folder — in the OS file manager. */
export async function revealPath(path: string): Promise<void> {
  const res = await fetch(`${BASE}/reveal`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ path }),
  });
  if (!res.ok) throw new Error(`reveal failed (${res.status})`);
}

