// Single source of truth for where the engine's HTTP API and WebSocket live.
//
// The SPA is served by the engine itself, so everything is same-origin and
// works under the desktop shell's dynamic port. In dev it loads from Vite
// (:5173), which proxies `/v1` and `/health` to the uvicorn dev server — see
// vite.config.ts. The contract is versioned: bump API_VERSION together with
// engine/contract/ when a breaking change ships.
export const API_VERSION = "v1";

const ORIGIN =
  typeof window !== "undefined" ? window.location.origin : "http://localhost:8000";

/** Base for every HTTP resource, e.g. `${API_BASE}/sessions`. */
export const API_BASE = `${ORIGIN}/${API_VERSION}`;

/** The desktop build gates the API with a per-launch token cookie (see
 * engine auth.py). Browsers send it on same-origin requests automatically;
 * the WebSocket handshake also gets it explicitly as `?token=` so we don't
 * depend on WKWebView/WebView2 cookie behaviour. Empty in plain dev. */
export function loopbackToken(): string {
  if (typeof document === "undefined") return "";
  const m = document.cookie.match(/(?:^|;\s*)wrenote_token=([^;]+)/);
  return m ? decodeURIComponent(m[1]) : "";
}

/** Same-origin WebSocket URL for the live-session endpoint. */
export function wsUrl(): string {
  if (typeof window === "undefined") return `ws://localhost:8000/${API_VERSION}/ws`;
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  const token = loopbackToken();
  const query = token ? `?token=${encodeURIComponent(token)}` : "";
  return `${proto}://${window.location.host}/${API_VERSION}/ws${query}`;
}
