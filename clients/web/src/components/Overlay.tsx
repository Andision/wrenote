// Floating subtitle overlay — the "desktop lyrics" window.
//
// Rendered instead of the full App when the page is loaded with #overlay
// (see main.tsx). The desktop shell hosts it in a frameless transparent
// always-on-top window; live text + mic level arrive over a BroadcastChannel
// from the main window (overlayBridge.ts). The whole pill is draggable except
// the control buttons, which stay visible (a drag region swallows hover, so
// reveal-on-hover can't work here).
//
// Two forms, toggled in-place (the choice persists across opens):
//   • "full"    — translucent pill with the latest original + translation
//   • "compact" — a tiny bar: recording dot + live mic level only
import { useEffect, useState } from "react";
import { Minimize2, Maximize2, X } from "lucide-react";

import { startWindowDrag } from "@/lib/desktop";
import {
  connectOverlayListener,
  type OverlayStateMsg,
} from "@/lib/overlayBridge";

// `-webkit-app-region` is Electron-only (Tauri drags via startWindowDrag below),
// so it lives in inline styles that TypeScript's CSSProperties doesn't know about.
const DRAG: React.CSSProperties = { WebkitAppRegion: "drag" } as React.CSSProperties;
const NO_DRAG: React.CSSProperties = { WebkitAppRegion: "no-drag" } as React.CSSProperties;

type OverlayMode = "full" | "compact";

// Window size per mode. Kept in sync with the shells' initial overlay size
// (shells/electron/main.js and shells/tauri overlay.rs open at the "full" size).
const SIZES: Record<OverlayMode, { w: number; h: number }> = {
  full: { w: 760, h: 184 },
  compact: { w: 240, h: 64 },
};

const MODE_KEY = "wrenote.overlayMode";

function loadMode(): OverlayMode {
  try {
    return window.localStorage.getItem(MODE_KEY) === "compact" ? "compact" : "full";
  } catch {
    return "full";
  }
}

export function Overlay() {
  const [state, setState] = useState<OverlayStateMsg | null>(null);
  const [mode, setMode] = useState<OverlayMode>(loadMode);

  // The SPA's stylesheet paints html/body with the theme background; the
  // overlay window must stay transparent so only the pill is visible.
  useEffect(() => {
    document.documentElement.style.background = "transparent";
    document.body.style.background = "transparent";
    return connectOverlayListener(setState);
  }, []);

  // Drive the host window to match the active form (also fixes the size on
  // first paint, since the shell opens every overlay at the "full" size).
  useEffect(() => {
    const { w, h } = SIZES[mode];
    void window.wrenoteDesktop?.resizeOverlay(w, h);
    try {
      window.localStorage.setItem(MODE_KEY, mode);
    } catch {
      // non-critical
    }
  }, [mode]);

  const close = () => {
    if (window.wrenoteDesktop) void window.wrenoteDesktop.closeOverlay();
    else window.close();
  };

  // Controls are always visible (low-key, brightening on hover): the rest of
  // the pill is an `-webkit-app-region: drag` surface, and a drag region
  // swallows hover events — so "show on hover anywhere" can't work there.
  // Each button is `no-drag`, so its own :hover still fires.
  const controls = (
    <div className="flex shrink-0 items-center gap-1 opacity-70" style={NO_DRAG}>
      <button
        onClick={() => setMode((m) => (m === "full" ? "compact" : "full"))}
        aria-label={mode === "full" ? "Compact bar" : "Expand subtitles"}
        className="flex size-5 items-center justify-center rounded-full bg-white/15 text-white/80 hover:bg-white/30 hover:text-white"
      >
        {mode === "full" ? (
          <Minimize2 className="size-3" />
        ) : (
          <Maximize2 className="size-3" />
        )}
      </button>
      <button
        onClick={close}
        aria-label="Close overlay"
        className="flex size-5 items-center justify-center rounded-full bg-white/15 text-white/80 hover:bg-white/30 hover:text-white"
      >
        <X className="size-3" />
      </button>
    </div>
  );

  return (
    <div
      className="flex h-screen w-screen select-none items-end justify-center p-2"
      style={DRAG}
      // Tauri has no CSS drag region; start a native drag on mousedown anywhere
      // except the control buttons (same reachable area as the Electron region).
      onMouseDown={(e) => {
        if (e.button !== 0 || (e.target as HTMLElement).closest("button")) return;
        startWindowDrag();
      }}
    >
      {mode === "compact" ? (
        <CompactBar state={state} controls={controls} />
      ) : (
        <FullPill state={state} controls={controls} />
      )}
    </div>
  );
}

function StatusDot({ connection }: { connection?: OverlayStateMsg["connection"] }) {
  return (
    <span
      aria-hidden
      className={`size-2 shrink-0 rounded-full ${
        connection === "recording"
          ? "animate-pulse bg-red-500"
          : connection === "paused"
            ? "bg-amber-400"
            : "bg-white/30"
      }`}
    />
  );
}

function FullPill({
  state,
  controls,
}: {
  state: OverlayStateMsg | null;
  controls: React.ReactNode;
}) {
  const recording =
    state?.connection === "recording" || state?.connection === "paused";
  const lines = state?.lines ?? [];
  const current = lines[lines.length - 1];
  const previous = lines.length > 1 ? lines[0] : undefined;

  return (
    <div className="flex w-full max-w-3xl items-start gap-3 rounded-2xl bg-black/65 px-5 py-3.5 text-white shadow-lg backdrop-blur-md">
      <span className="mt-2">
        <StatusDot connection={state?.connection} />
      </span>
      <div className="min-w-0 flex-1">
        {!recording && !current ? (
          <p className="truncate py-1 text-[14px] text-white/50">
            Waiting for recording…
          </p>
        ) : (
          <>
            {previous && (
              <p className="truncate text-[12.5px] leading-5 text-white/40">
                {previous.trans || previous.orig}
              </p>
            )}
            {current && (
              <>
                <p
                  className={`line-clamp-2 text-[15px] leading-6 ${
                    current.origStatus === "partial"
                      ? "text-white/65"
                      : "text-white/85"
                  }`}
                >
                  {current.orig || "…"}
                </p>
                {current.trans && (
                  <p
                    className={`line-clamp-2 text-[17px] font-medium leading-7 ${
                      current.transStatus === "partial"
                        ? "text-white/75"
                        : "text-white"
                    }`}
                  >
                    {current.trans}
                  </p>
                )}
              </>
            )}
            {recording && !current && (
              <p className="truncate py-1 text-[14px] text-white/50">
                Listening…
              </p>
            )}
          </>
        )}
      </div>
      {controls}
    </div>
  );
}

function CompactBar({
  state,
  controls,
}: {
  state: OverlayStateMsg | null;
  controls: React.ReactNode;
}) {
  const isLive = state?.connection === "recording";
  // Same mapping as the StatusBar meter: 0–0.25 RMS → 0–100%.
  const pct = Math.min(100, (state?.micLevel ?? 0) * 400);

  return (
    <div className="flex w-full items-center gap-2.5 rounded-full bg-black/65 px-3.5 py-2 text-white shadow-lg backdrop-blur-md">
      {/* Recording status: pulsing red dot (live) / amber (paused) / grey. */}
      <StatusDot connection={state?.connection} />
      {/* Mic level meter — flexes to fill the rest of the bar. */}
      <div className="relative h-1.5 min-w-0 flex-1 overflow-hidden rounded-full bg-white/15">
        <div
          className={`absolute inset-y-0 left-0 rounded-full transition-[width] duration-100 ${
            isLive ? "bg-red-400" : "bg-white/30"
          }`}
          style={{ width: `${pct.toFixed(1)}%` }}
        />
      </div>
      {controls}
    </div>
  );
}
