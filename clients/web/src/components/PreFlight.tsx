import { useEffect, useState } from "react";
import { motion } from "motion/react";
import { Activity, ArrowRight, Loader2, Mic, Monitor, RefreshCw, ShieldCheck, UploadCloud, Volume2 } from "lucide-react";

import {
  LanguageSelect,
  SOURCE_LANGUAGES,
  TARGET_LANGUAGES,
} from "@/components/LanguageSelect";
import { UploadDialog } from "@/components/UploadDialog";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { type CaptureTargets, listCaptureTargets } from "@/lib/capture";
import { useMicPreview } from "@/hooks/useMicPreview";
import { cn } from "@/lib/utils";
import { useSessionStore } from "@/store/sessionStore";
import { useT } from "@/i18n";

interface PreFlightProps {
  onStart: () => void;
}

/** Compact live mic-level bar for the PreFlight capture row. `level` is RMS
 *  (~0–0.1 while talking); sqrt-scaled so quiet speech is still visible. */
function MicMeter({ level }: { level: number }) {
  const pct = Math.min(1, Math.sqrt(level) * 2.2);
  return (
    <div className="h-1.5 w-14 shrink-0 overflow-hidden rounded-full bg-border" aria-hidden>
      <div
        className="h-full rounded-full bg-brand-500 transition-[width] duration-75"
        style={{ width: `${pct * 100}%` }}
      />
    </div>
  );
}

/** Pill toggle for an extra capture source (system audio / screen). Horizontal
 *  and compact so several sit on one row instead of stacking. */
function SourceToggle({
  icon: Icon,
  label,
  active,
  onClick,
  disabled,
}: {
  icon: typeof Mic;
  label: string;
  active: boolean;
  onClick: () => void;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-pressed={active}
      className={cn(
        "inline-flex shrink-0 items-center gap-1.5 rounded-full border px-3 py-1.5 text-[12.5px] font-medium transition-colors disabled:opacity-50",
        active
          ? "border-brand-500/40 bg-brand-500/15 text-brand-700 dark:text-brand-300"
          : "border-border bg-card text-muted-foreground hover:bg-accent hover:text-foreground",
      )}
    >
      <Icon className="size-3.5" />
      {label}
    </button>
  );
}

/**
 * Centered launch zone shown when no transcript exists yet. Hosts the
 * language selectors (shared via `layoutId="lang-strip"` with the TopBar's
 * compact version, so they magic-move when recording begins) and the
 * primary Record CTA.
 */
export function PreFlight({ onStart }: PreFlightProps) {
  const t = useT();
  const settings = useSessionStore((s) => s.settings);
  const updateSettings = useSessionStore((s) => s.updateSettings);
  const connection = useSessionStore((s) => s.connection);

  // "connected" is the post-socket, pre-`ready` gap — still starting up, so the
  // controls stay locked (matches TopBar / Transcript's pre-roll handling).
  const isBusy = connection === "connecting" || connection === "connected";
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
    // Fetch inside an async IIFE so the effect body has no synchronous setState
    // (refreshTargets sets loading state up-front); avoids cascading renders.
    let cancelled = false;
    void (async () => {
      setLoadingTargets(true);
      const t = await listCaptureTargets();
      if (!cancelled) {
        setTargets(t);
        setLoadingTargets(false);
      }
    })();
    return () => {
      cancelled = true;
    };
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

  // Device list is always enumerated (cheap, no stream). The level meter is
  // opt-in: the user taps "Test" to open a preview stream, so we don't hold the
  // mic — or light the OS indicator — just by sitting on PreFlight. Auto-stops
  // when recording/connecting starts so it never fights useMicrophone.
  const [micTest, setMicTest] = useState(false);
  const previewEnabled = micTest && !isRecording && !isBusy;
  const { devices: micDevices, level: previewLevel } = useMicPreview(
    settings.micDeviceId,
    previewEnabled,
  );

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
          {settings.translateEnabled ? t("lang.from") : t("lang.language")}
        </span>
        <LanguageSelect
          value={settings.srcLang}
          options={SOURCE_LANGUAGES}
          onChange={(v) => updateSettings({ srcLang: v })}
          disabled={isRecording || isBusy}
          ariaLabel={t("lang.source")}
        />
        {settings.translateEnabled && (
          <>
            <ArrowRight className="size-4 text-muted-foreground/60" />
            <span className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
              {t("lang.to")}
            </span>
            <LanguageSelect
              value={settings.tgtLang}
              options={TARGET_LANGUAGES}
              onChange={(v) => updateSettings({ tgtLang: v })}
              disabled={isRecording || isBusy}
              ariaLabel={t("lang.target")}
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
          {settings.translateEnabled ? t("preflight.translateOn") : t("preflight.translateOff")}
        </label>
      </div>

      {/* Capture sources — per-session input selection. Model A: these edit the
          persisted settings directly, which double as the remembered defaults.
          Mic picker + level meter are live; the screen target list stays empty
          until macOS Screen-Recording permission (a signed build). */}
      <div className="flex w-full max-w-md flex-col gap-2.5 rounded-2xl border border-border/60 bg-card/40 px-4 py-3 text-left">
        {/* Microphone + live level — its own row (the select wants the width). */}
        <div className="flex items-center gap-2.5">
          <Mic className="size-4 shrink-0 text-muted-foreground" />
          <select
            value={settings.micDeviceId}
            onChange={(e) => updateSettings({ micDeviceId: e.target.value })}
            disabled={isRecording || isBusy}
            aria-label={t("preflight.microphone")}
            className="min-w-0 flex-1 truncate rounded-md border border-border bg-card px-2 py-1.5 text-[13px] text-foreground shadow-sm focus:outline-none focus:ring-2 focus:ring-brand-500/40 disabled:opacity-50"
          >
            <option value="">{t("preflight.defaultMic")}</option>
            {micDevices.map((d) => (
              <option key={d.deviceId} value={d.deviceId}>
                {d.label || t("preflight.unnamedMic", { id: d.deviceId.slice(0, 6) })}
              </option>
            ))}
          </select>
          <SourceToggle
            icon={Activity}
            label={t("preflight.test")}
            active={micTest}
            disabled={isRecording || isBusy}
            onClick={() => setMicTest((v) => !v)}
          />
          {micTest && <MicMeter level={previewLevel} />}
        </div>

        {/* Extra sources as horizontal pill toggles; the screen target picker
            rides on the same wrapping row when Screen is on. */}
        <div className="flex flex-wrap items-center gap-2">
          <SourceToggle
            icon={Volume2}
            label={t("preflight.systemAudio")}
            active={settings.captureSystemAudio}
            disabled={isRecording || isBusy}
            onClick={() => updateSettings({ captureSystemAudio: !settings.captureSystemAudio })}
          />
          <SourceToggle
            icon={Monitor}
            label={t("preflight.screen")}
            active={settings.captureScreen}
            disabled={isRecording || isBusy}
            onClick={() => updateSettings({ captureScreen: !settings.captureScreen })}
          />
          {settings.captureScreen && (
            <div className="flex min-w-[11rem] flex-1 items-center gap-1.5">
              <select
                value={targetValue}
                onChange={(e) => onPickTarget(e.target.value)}
                disabled={isRecording || isBusy}
                aria-label={t("preflight.captureTarget")}
                className="min-w-0 flex-1 truncate rounded-md border border-border bg-card px-2 py-1 text-[12.5px] text-foreground shadow-sm focus:outline-none focus:ring-2 focus:ring-brand-500/40 disabled:opacity-50"
              >
                <option value="">{t("preflight.fullScreen")}</option>
                {targets.displays.length > 0 && (
                  <optgroup label={t("preflight.displays")}>
                    {targets.displays.map((d) => (
                      <option key={`display:${d.id}`} value={`display:${d.id}`}>
                        {d.title}
                      </option>
                    ))}
                  </optgroup>
                )}
                {targets.windows.length > 0 && (
                  <optgroup label={t("preflight.windows")}>
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
                data-tip={t("preflight.refreshHint")}
                className="inline-flex size-7 shrink-0 items-center justify-center rounded-md text-muted-foreground hover:bg-accent hover:text-foreground disabled:opacity-50"
                aria-label={t("preflight.refresh")}
              >
                <RefreshCw className={`size-3.5 ${loadingTargets ? "animate-spin" : ""}`} />
              </button>
            </div>
          )}
        </div>
      </div>

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
            {isBusy ? t("preflight.starting") : t("preflight.start")}
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
        <span>{t("preflight.privacy")}</span>
      </div>
    </motion.div>
  );
}
