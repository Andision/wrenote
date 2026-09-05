// Desktop-shell bridge for the Tauri host.
//
// The web client talks to the shell through one tiny surface,
// `window.wrenoteDesktop` (declared in overlayBridge.ts): toggle / close /
// resize the floating subtitle overlay, and open a URL in the system browser.
// The Electron shell injects that object from its preload script. Tauri
// instead exposes `window.__TAURI__` (config `app.withGlobalTauri`), so here we
// map the same calls onto Tauri commands — components never need to know which
// shell is hosting them.
//
// In a plain browser neither global exists and this is a no-op.

interface TauriGlobal {
  core: { invoke: (cmd: string, args?: Record<string, unknown>) => Promise<unknown> };
  window?: { getCurrentWindow: () => { startDragging: () => Promise<void> } };
}

declare global {
  interface Window {
    __TAURI__?: TauriGlobal;
  }
}

function tauri(): TauriGlobal | undefined {
  return typeof window === "undefined" ? undefined : window.__TAURI__;
}

/** Install `window.wrenoteDesktop` when running under Tauri. Call once, before render. */
export function installDesktopBridge(): void {
  if (typeof window === "undefined" || window.wrenoteDesktop) return;
  const t = tauri();
  if (!t?.core) return;
  const invoke = (cmd: string, args?: Record<string, unknown>) =>
    t.core.invoke(cmd, args).then(() => undefined);
  window.wrenoteDesktop = {
    toggleOverlay: () => invoke("overlay_toggle"),
    closeOverlay: () => invoke("overlay_close"),
    resizeOverlay: (width, height) => invoke("overlay_resize", { width, height }),
    // tauri-plugin-opener; the capability scopes it to http(s) URLs.
    openExternal: (url) => invoke("plugin:opener|open_url", { url }),
  };
}

/**
 * Begin dragging the current window (Tauri only). Electron uses the
 * `-webkit-app-region: drag` CSS region instead, which Tauri's WebViews don't
 * honour; calling this from a mousedown handler gives the same behaviour.
 * Returns false when not under Tauri so the caller can fall through.
 */
export function startWindowDrag(): boolean {
  const t = tauri();
  if (!t?.window) return false;
  void t.window.getCurrentWindow().startDragging();
  return true;
}
