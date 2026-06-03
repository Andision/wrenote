import { useEffect, useState } from "react";
import { motion } from "motion/react";
import { ArrowRight, Loader2, Mic, RefreshCw, ShieldCheck, UploadCloud } from "lucide-react";

import {
  LanguageSelect,
  SOURCE_LANGUAGES,
  TARGET_LANGUAGES,
} from "@/components/LanguageSelect";
import { UploadDialog } from "@/components/UploadDialog";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { type CaptureTargets, listCaptureTargets } from "@/lib/capture";
import { useSessionStore } from "@/store/sessionStore";

interface PreFlightProps {
  onStart: () => void;
}

/**
 * Centered launch zone shown when no transcript exists yet. Hosts the
 * language selectors (shared via `layoutId="lang-strip"` with the TopBar's
 * compact version, so they magic-move when recording begins) and the
 * primary Record CTA.
 */
export function PreFlight({ onStart }: PreFlightProps) {
  const settings = useSessionStore((s) => s.settings);
  const updateSettings = useSessionStore((s) => s.updateSettings);
  const connection = useSessionStore((s) => s.connection);

  const isBusy = connection === "connecting";
  const isRecording = connection === "recording";

  const [uploadOpen, setUploadOpen] = useState(false);

  // Screen/window capture targets — fetched only while "Record screen" is on.
  // On macOS the list is empty until Screen-Recording permission is granted, so
  // we expose a Refresh button (the first real recording triggers the prompt).
  const [targets, setTargets] = useState<CaptureTargets>({ displays: [], windows: [] });
  const [loadingTargets, setLoadingTargets] = useState(false);

  const refreshTargets = () => {
    setLoadingTargets(true);
    void listCaptureTargets().then((t) => {
      setTargets(t);
      setLoadingTargets(false);
    });
  };

  useEffect(() => {
    if (!settings.captureScreen) return;
    refreshTargets();
  }, [settings.captureScreen]);

  const targetValue = settings.captureTarget
    ? `${settings.captureTarget.type}:${settings.captureTarget.id}`
    : "";

  const onPickTarget = (val: string) => {
    if (!val) {
      updateSettings({ captureTarget: null });
      return;
    }
    const [type, idStr] = val.split(":");
    const id = Number(idStr);
    const pool = type === "display" ? targets.displays : targets.windows;
    updateSettings({ captureTarget: pool.find((x) => x.id === id) ?? null });
  };

  return (
    <motion.div
      key="preflight"
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, scale: 0.97, y: -8 }}
      transition={{ duration: 0.24, ease: [0.22, 0.61, 0.36, 1] }}
      className="absolute inset-0 flex flex-col items-center justify-center gap-8 overflow-auto px-6 text-center"
    >
      {/* Mic mark with a slow breathing glow — establishes "this is the moment" */}
      <motion.div
        layoutId="preflight-mark"
        className="relative flex size-24 items-center justify-center rounded-3xl bg-gradient-to-br from-brand-500/15 to-brand-500/0 ring-1 ring-inset ring-brand-500/25"
      >
        <Mic className="size-10 text-brand-600 dark:text-brand-400" />
        <motion.span
          aria-hidden
          className="pointer-events-none absolute inset-0 rounded-3xl bg-brand-500/15"
          animate={{ scale: [1, 1.18, 1], opacity: [0.4, 0, 0.4] }}
          transition={{ duration: 2.4, repeat: Infinity, ease: "easeOut" }}
        />
      </motion.div>

      <div className="space-y-2">
        <h1 className="text-2xl font-semibold tracking-tight text-foreground">
          Ready to translate
        </h1>
        <p className="text-[14px] leading-relaxed text-muted-foreground">
          Pick the languages, hit record, start talking.
        </p>
      </div>

      {/* Shared lang strip — same `layoutId` as the TopBar's compact version,
          so motion animates it across when the pre-flight unmounts. The
          target-half collapses when translate is off (transcribe-only). */}
      <motion.div
        layout
        layoutId="lang-strip"
        className="flex items-center gap-3 rounded-2xl border border-border/60 bg-card/60 px-4 py-3 shadow-sm backdrop-blur-sm"
      >
        <span className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
          {settings.translateEnabled ? "From" : "Language"}
        </span>
        <LanguageSelect
          value={settings.srcLang}
          options={SOURCE_LANGUAGES}
          onChange={(v) => updateSettings({ srcLang: v })}
          disabled={isRecording || isBusy}
          ariaLabel="Source language"
        />
        {settings.translateEnabled && (
          <>
            <ArrowRight className="size-4 text-muted-foreground/60" />
            <span className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
              to
            </span>
            <LanguageSelect
              value={settings.tgtLang}
              options={TARGET_LANGUAGES}
              onChange={(v) => updateSettings({ tgtLang: v })}
              disabled={isRecording || isBusy}
              ariaLabel="Target language"
            />
          </>
        )}
      </motion.div>

      {/* Mode toggle: translate (default) vs transcribe-only. Sits just
          under the lang strip so it reads as "tune the row above". */}
      <div className="flex items-center gap-3 text-[13px] text-muted-foreground">
        <Switch
          id="translate-toggle"
          checked={settings.translateEnabled}
          onCheckedChange={(v) => updateSettings({ translateEnabled: v })}
          disabled={isRecording || isBusy}
        />
        <label htmlFor="translate-toggle" className="cursor-pointer">
          {settings.translateEnabled
            ? "Translate as I speak"
            : "Transcribe only · no translation"}
        </label>
      </div>

      {/* Screen/window picker — only when "Record screen" is enabled (Settings).
          Lists displays + windows from the backend; "Full screen" = no target. */}
      {settings.captureScreen && (
        <div className="flex items-center gap-2 text-[13px] text-muted-foreground">
          <span className="font-medium">Record</span>
          <select
            value={targetValue}
            onChange={(e) => onPickTarget(e.target.value)}
            disabled={isRecording || isBusy}
            className="max-w-[18rem] truncate rounded-md border border-border bg-card px-2 py-1.5 text-[13px] text-foreground shadow-sm focus:outline-none focus:ring-2 focus:ring-brand-500/40 disabled:opacity-50"
            aria-label="Screen or window to record"
          >
            <option value="">Full screen</option>
            {targets.displays.length > 0 && (
              <optgroup label="Displays">
                {targets.displays.map((d) => (
                  <option key={`display:${d.id}`} value={`display:${d.id}`}>
                    {d.title}
                  </option>
                ))}
              </optgroup>
            )}
            {targets.windows.length > 0 && (
              <optgroup label="Windows">
                {targets.windows.map((w) => (
                  <option key={`window:${w.id}`} value={`window:${w.id}`}>
                    {w.app ? `${w.app} — ${w.title}` : w.title}
                  </option>
                ))}
              </optgroup>
            )}
          </select>
          <button
            type="button"
            onClick={refreshTargets}
            disabled={loadingTargets || isRecording || isBusy}
            data-tip="Refresh the window list (grant Screen Recording first)"
            className="inline-flex size-7 items-center justify-center rounded-md text-muted-foreground hover:bg-accent hover:text-foreground disabled:opacity-50"
            aria-label="Refresh capture targets"
          >
            <RefreshCw className={`size-3.5 ${loadingTargets ? "animate-spin" : ""}`} />
          </button>
        </div>
      )}

      {/* Vertical stack — Start recording is the primary path, Upload is the
          alternative. "or" between them reads as a deliberate fork. */}
      <div className="flex flex-col items-center gap-3">
        <motion.div
          whileHover={isBusy ? undefined : { scale: 1.025 }}
          whileTap={isBusy ? undefined : { scale: 0.97 }}
        >
          <Button
            size="lg"
            onClick={onStart}
            disabled={isBusy || isRecording}
            className="h-12 gap-2 rounded-full bg-brand-600 px-7 text-[15px] font-semibold text-white shadow-md shadow-brand-500/20 hover:bg-brand-700 dark:bg-brand-500 dark:hover:bg-brand-600"
          >
            {isBusy ? (
              <motion.span
                animate={{ rotate: 360 }}
                transition={{ duration: 0.9, repeat: Infinity, ease: "linear" }}
                className="inline-flex"
              >
                <Loader2 className="size-5" />
              </motion.span>
            ) : (
              <Mic className="size-5" />
            )}
            {isBusy ? "Starting" : "Start recording"}
          </Button>
        </motion.div>

        <span className="text-[11px] uppercase tracking-wider text-muted-foreground/70">
          or
        </span>

        <motion.div
          whileHover={isBusy ? undefined : { scale: 1.04 }}
          whileTap={isBusy ? undefined : { scale: 0.96 }}
        >
          <Button
            variant="ghost"
            onClick={() => setUploadOpen(true)}
            disabled={isBusy || isRecording}
            className="h-10 gap-2 rounded-full px-5 text-[13.5px] text-muted-foreground hover:text-foreground"
          >
            <UploadCloud className="size-4" />
            Upload audio / video
          </Button>
        </motion.div>
      </div>

      <UploadDialog open={uploadOpen} onClose={() => setUploadOpen(false)} />

      <div className="flex items-center gap-1.5 text-[12px] text-muted-foreground">
        <ShieldCheck className="size-3.5" />
        <span>Running locally · no audio leaves your device</span>
      </div>
    </motion.div>
  );
}
