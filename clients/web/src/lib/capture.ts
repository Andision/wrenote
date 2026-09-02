import { API_BASE as BASE } from "./api";
// Capture-target enumeration for the PreFlight screen/window picker.
// Same-origin: the SPA is served by the backend, so the auth cookie rides along.

export interface CaptureTarget {
  type: "window" | "display";
  id: number;
  title: string;
  app?: string;
  width?: number;
  height?: number;
}

export interface CaptureTargets {
  displays: CaptureTarget[];
  windows: CaptureTarget[];
}

/**
 * List windows + displays the user can record. Returns empty lists when capture
 * is unsupported or the OS Screen-Recording permission hasn't been granted yet
 * (in which case the UI just offers full-screen / the user grants + refreshes).
 */
export async function listCaptureTargets(): Promise<CaptureTargets> {
  try {
    const res = await fetch(`${BASE}/capture/targets`);
    if (!res.ok) return { displays: [], windows: [] };
    return (await res.json()) as CaptureTargets;
  } catch {
    return { displays: [], windows: [] };
  }
}
