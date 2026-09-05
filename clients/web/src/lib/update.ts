import { API_BASE as BASE } from "./api";
// App updates. The engine reads the release index and says whether a newer
// Wrenote exists (engine/wrenote/core/update.py); this file fetches that
// verdict and opens the download. Comparing versions here would be a bug: the
// engine is the one place that knows what it is running.

export type UpdateError = "unreachable" | "bad_index" | "no_index";

export interface UpdateStatus {
  current: string;
  enabled: boolean; // the automatic check (Settings → General)
  platform: string; // the updater's key for this machine, e.g. "darwin-aarch64"
  index_url: string;
  checked_at: string | null; // null = never asked (automatic check off)
  latest: string | null;
  available: boolean; // latest is newer than current
  download_url: string | null; // this machine's installer, when published
  release_url: string | null; // the human page for the release
  notes: string;
  published_at: string | null;
  error: UpdateError | null; // a code; i18n renders it
}

async function check(res: Response, what: string): Promise<Response> {
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = (await res.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      /* not JSON */
    }
    throw new Error(`${what} failed (${res.status}): ${detail}`);
  }
  return res;
}

/** The engine's cached answer; no network while the automatic check is off. */
export async function getUpdateStatus(): Promise<UpdateStatus> {
  const res = await check(await fetch(`${BASE}/update`), "update status");
  return (await res.json()) as UpdateStatus;
}

/** The user pressing "check now": ignores the cache and the setting. */
export async function checkForUpdate(): Promise<UpdateStatus> {
  const res = await check(await fetch(`${BASE}/update/check`, { method: "POST" }), "update check");
  return (await res.json()) as UpdateStatus;
}

/** Persist the automatic-check switch (engine config, survives restarts). */
export async function setUpdateCheck(enabled: boolean): Promise<void> {
  await check(
    await fetch(`${BASE}/update/settings`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ check: enabled }),
    }),
    "update settings",
  );
}

/** Where "Download" goes: the installer for this machine, else the release page. */
export function downloadTarget(status: UpdateStatus): string | null {
  return status.download_url ?? status.release_url;
}

/**
 * Open a web URL in the system browser. Under a desktop shell this goes
 * through `window.wrenoteDesktop` — a WebView would otherwise navigate the app
 * itself away to GitHub; in a plain browser tab it is just a new tab.
 */
export function openExternal(url: string): void {
  if (typeof window === "undefined") return;
  const desktop = window.wrenoteDesktop;
  if (desktop?.openExternal) {
    void desktop.openExternal(url);
    return;
  }
  window.open(url, "_blank", "noopener,noreferrer");
}
