import { API_BASE as BASE } from "./api";
// Global custom-vocabulary glossary (fed to STT + translation server-side).

export interface GlossaryEntry {
  id?: string;
  term: string;
  translation: string;
  note?: string;
}

export async function getGlossary(): Promise<GlossaryEntry[]> {
  try {
    const res = await fetch(`${BASE}/glossary`);
    if (!res.ok) return [];
    return ((await res.json()).glossary ?? []) as GlossaryEntry[];
  } catch {
    return [];
  }
}

/** Replace the whole glossary; fire-and-forget from the editor. */
export async function saveGlossary(entries: GlossaryEntry[]): Promise<void> {
  try {
    await fetch(`${BASE}/glossary`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ glossary: entries }),
    });
  } catch {
    /* best-effort; next open re-fetches the canonical list */
  }
}
