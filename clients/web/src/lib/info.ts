// GET /v1/info: what this engine process actually loaded.
//
// The config here is the merged one — defaults, then the repo YAML, then
// ~/.wrenote/config.yaml, then env — which is not the same as any file on
// disk, so reading it back from the engine is the only way to know. Shown in
// Settings → Developer, and the first thing worth pasting into a bug report.
import { API_BASE as BASE } from "./api";

export interface AppInfo {
  version: string;
  paths: Record<string, string>;
  config: Record<string, unknown>;
  static_dir_exists: boolean;
}

export async function getAppInfo(): Promise<AppInfo> {
  const res = await fetch(`${BASE}/info`);
  if (!res.ok) throw new Error(`info failed (${res.status})`);
  return (await res.json()) as AppInfo;
}
