import { useEffect, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import {
  Cpu,
  Monitor,
  Moon,
  Scissors,
  SlidersHorizontal,
  Sun,
  X,
  Zap,
  type LucideIcon,
} from "lucide-react";
import { useTheme } from "next-themes";

import { Label } from "@/components/ui/label";
import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";
import { listAudioInputs } from "@/lib/devices";
import { useSessionStore } from "@/store/sessionStore";

type CategoryId = "general" | "segmentation" | "realtime" | "engines";

const CATEGORIES: { id: CategoryId; label: string; icon: LucideIcon }[] = [
  { id: "general", label: "General", icon: SlidersHorizontal },
  { id: "segmentation", label: "Segmentation", icon: Scissors },
  { id: "realtime", label: "Real-time", icon: Zap },
  { id: "engines", label: "Engines", icon: Cpu },
];

/**
 * Settings modal — a centered floating panel with a category rail on the
 * left and the selected category's controls on the right (ChatGPT-style).
 * Esc or a backdrop click closes it.
 */
export function SettingsDrawer() {
  const open = useSessionStore((s) => s.settingsOpen);
  const settings = useSessionStore((s) => s.settings);
  const updateSettings = useSessionStore((s) => s.updateSettings);
  const connection = useSessionStore((s) => s.connection);
  const ready = useSessionStore((s) => s.ready);
  const sessionInProgress =
    connection === "recording" || connection === "stopping";

  const [cat, setCat] = useState<CategoryId>("general");
  const close = () => useSessionStore.getState().toggleSettings(false);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  const activeLabel = CATEGORIES.find((c) => c.id === cat)?.label;

  return (
    <AnimatePresence>
      {open && (
        <div className="fixed inset-0 z-[110] flex items-center justify-center p-4">
          <motion.div
            className="absolute inset-0 bg-background/60 backdrop-blur-sm"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.15 }}
            onClick={close}
          />
          <motion.div
            role="dialog"
            aria-modal="true"
            className="relative z-10 flex h-[72vh] max-h-[600px] w-full max-w-3xl overflow-hidden rounded-2xl border border-border bg-card shadow-2xl"
            initial={{ opacity: 0, scale: 0.97, y: 8 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.97, y: 8 }}
            transition={{ duration: 0.18, ease: "easeOut" }}
          >
            {/* Category rail */}
            <nav className="flex w-48 shrink-0 flex-col border-r border-border bg-muted/30 p-3">
              <div className="px-2 pb-3 text-[15px] font-semibold tracking-tight text-foreground">
                Settings
              </div>
              <div className="flex flex-col gap-0.5">
                {CATEGORIES.map((c) => {
                  const Icon = c.icon;
                  const active = c.id === cat;
                  return (
                    <button
                      key={c.id}
                      onClick={() => setCat(c.id)}
                      className={`flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-[13px] transition-colors ${
                        active
                          ? "bg-accent font-medium text-foreground"
                          : "text-muted-foreground hover:bg-accent/50 hover:text-foreground"
                      }`}
                    >
                      <Icon className="size-4 shrink-0" />
                      {c.label}
                    </button>
                  );
                })}
              </div>
            </nav>

            {/* Content */}
            <div className="flex min-w-0 flex-1 flex-col">
              <header className="flex h-14 shrink-0 items-center justify-between border-b border-border px-5">
                <h2 className="text-sm font-semibold text-foreground">{activeLabel}</h2>
                <button
                  onClick={close}
                  data-tip="Close (Esc)"
                  className="rounded-md p-1 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
                >
                  <X className="size-4" />
                </button>
              </header>

              <div className="min-h-0 flex-1 overflow-y-auto px-5 py-5">
                {cat === "general" && (
                  <div className="space-y-5">
                    <ThemeField />
                    <MicDeviceField />
                    <ToggleField
                      label="Record system audio"
                      checked={settings.captureSystemAudio}
                      hint="Also capture your computer's audio output (e.g. the far side of an online meeting) and mix it into the transcription. macOS asks for Screen Recording permission the first time."
                      onChange={(v) => updateSettings({ captureSystemAudio: v })}
                    />
                    <ToggleField
                      label="Record screen"
                      checked={settings.captureScreen}
                      hint="Record the full screen while you transcribe; saved as an MP4 (with the audio) when you stop. macOS asks for Screen Recording permission the first time."
                      onChange={(v) => updateSettings({ captureScreen: v })}
                    />
                    <ToggleField
                      label="Speaker identification (live)"
                      checked={settings.speakerEnabled}
                      hint="Experimental — unreliable mid-call. The post-process Identify speakers button is much better."
                      onChange={(v) => updateSettings({ speakerEnabled: v })}
                    />
                    <ToggleField
                      label="Continuous playback"
                      checked={settings.playbackMode === "continuous"}
                      hint="When you hit play on a segment, keep playing past its boundary. Off = pause when the segment ends."
                      onChange={(v) =>
                        updateSettings({ playbackMode: v ? "continuous" : "single" })
                      }
                    />
                    <ToggleField
                      label="Show level meters"
                      checked={settings.showLevelMeters}
                      hint="Tiny mic + speaker volume bars in the bottom status bar. Turn off if you find them distracting."
                      onChange={(v) => updateSettings({ showLevelMeters: v })}
                    />
                  </div>
                )}

                {cat === "segmentation" && (
                  <div className="space-y-5">
                    {sessionInProgress && <NextSessionNote />}
                    <RangeField
                      label="Min silence"
                      value={settings.minSilenceMs}
                      min={200}
                      max={2000}
                      step={50}
                      unit="ms"
                      hint="How long the speaker must pause before closing a segment. ~800ms suits conversation."
                      onChange={(v) => updateSettings({ minSilenceMs: v })}
                    />
                    <RangeField
                      label="Max segment"
                      value={settings.maxSegmentMs}
                      min={5000}
                      max={29000}
                      step={1000}
                      unit="ms"
                      hint="Hard cap. Whisper's context limit is 30s; leave a buffer."
                      onChange={(v) => updateSettings({ maxSegmentMs: v })}
                    />
                  </div>
                )}

                {cat === "realtime" && (
                  <div className="space-y-5">
                    {sessionInProgress && <NextSessionNote />}
                    <RangeField
                      label="Partial interval"
                      value={settings.partialIntervalMs}
                      min={300}
                      max={2000}
                      step={100}
                      unit="ms"
                      hint="How often partial transcripts are emitted. Lower = snappier but more GPU."
                      onChange={(v) => updateSettings({ partialIntervalMs: v })}
                    />
                    <ToggleField
                      label="Translate partials"
                      checked={settings.translatePartials}
                      hint="Stream translations of partial transcripts (a beat after the text)."
                      onChange={(v) => updateSettings({ translatePartials: v })}
                    />
                  </div>
                )}

                {cat === "engines" && (
                  <div className="space-y-4">
                    {ready ? (
                      <div className="space-y-2 rounded-lg border border-border/60 bg-muted/30 p-3">
                        <BackendRow label="STT" info={ready.stt} />
                        <BackendRow label="MT" info={ready.translator} />
                        <BackendRow label="VAD" info={ready.vad} />
                        {ready.speaker && (
                          <BackendRow label="Speaker" info={ready.speaker} />
                        )}
                      </div>
                    ) : (
                      <p className="rounded-lg border border-dashed border-border/60 px-3 py-8 text-center text-[12px] text-muted-foreground">
                        Start a session to see the active backends.
                      </p>
                    )}
                  </div>
                )}
              </div>
            </div>
          </motion.div>
        </div>
      )}
    </AnimatePresence>
  );
}

const THEME_OPTIONS = [
  { id: "light", label: "Light", icon: Sun },
  { id: "dark", label: "Dark", icon: Moon },
  { id: "system", label: "System", icon: Monitor },
] as const;

/**
 * Appearance picker — Light / Dark / Follow system. Lives in Settings now
 * (used to be a top-bar icon). next-themes resolves the active theme only
 * after mount, so we default the highlight to "system" until then.
 */
function ThemeField() {
  const { theme, setTheme } = useTheme();
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  const current =
    mounted && ["light", "dark", "system"].includes(theme ?? "")
      ? (theme as string)
      : "system";

  return (
    <div className="flex items-center justify-between gap-3">
      <div className="space-y-0.5">
        <Label className="text-xs text-foreground">Appearance</Label>
        <p className="text-[11px] leading-snug text-muted-foreground">
          Light, dark, or follow your system.
        </p>
      </div>
      <div className="inline-flex shrink-0 items-center gap-0.5 rounded-lg border border-border bg-muted/40 p-0.5">
        {THEME_OPTIONS.map((o) => {
          const Icon = o.icon;
          const active = current === o.id;
          return (
            <button
              key={o.id}
              onClick={() => setTheme(o.id)}
              data-tip={o.label}
              className={`inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-[11px] font-medium transition-colors ${
                active
                  ? "bg-card text-foreground shadow-sm"
                  : "text-muted-foreground hover:text-foreground"
              }`}
            >
              <Icon className="size-3.5" />
              {o.label}
            </button>
          );
        })}
      </div>
    </div>
  );
}

function NextSessionNote() {
  return (
    <p className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-700 dark:border-amber-900/50 dark:bg-amber-950/30 dark:text-amber-300">
      Some changes only take effect on the next session.
    </p>
  );
}

function BackendRow({
  label,
  info,
}: {
  label: string;
  info: { name?: string; model?: string; device?: string } | null | undefined;
}) {
  if (!info) return null;
  return (
    <div className="flex items-baseline justify-between gap-3 text-[11.5px]">
      <span className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
        {label}
      </span>
      <div className="min-w-0 flex-1 text-right">
        <div className="truncate font-medium text-foreground">{info.name ?? "—"}</div>
        <div className="truncate text-[10.5px] text-muted-foreground">
          {info.model ?? "—"} · {info.device ?? "—"}
        </div>
      </div>
    </div>
  );
}

function RangeField({
  label,
  value,
  min,
  max,
  step,
  unit,
  hint,
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  unit: string;
  hint?: string;
  onChange: (n: number) => void;
}) {
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <Label className="text-xs text-foreground">{label}</Label>
        <span className="font-mono text-[11px] text-muted-foreground">
          {value} {unit}
        </span>
      </div>
      <Slider
        min={min}
        max={max}
        step={step}
        value={[value]}
        onValueChange={(v) => onChange(Array.isArray(v) ? v[0] : v)}
      />
      {hint && <p className="text-[11px] leading-snug text-muted-foreground">{hint}</p>}
    </div>
  );
}

function MicDeviceField() {
  const micDeviceId = useSessionStore((s) => s.settings.micDeviceId);
  const updateSettings = useSessionStore((s) => s.updateSettings);
  const [devices, setDevices] = useState<MediaDeviceInfo[]>([]);

  useEffect(() => {
    const refresh = () => {
      void listAudioInputs().then(setDevices);
    };
    refresh();
    navigator.mediaDevices?.addEventListener?.("devicechange", refresh);
    return () => navigator.mediaDevices?.removeEventListener?.("devicechange", refresh);
  }, []);

  return (
    <div className="flex items-start justify-between gap-3">
      <div className="space-y-0.5">
        <Label className="text-xs text-foreground">Microphone</Label>
        <p className="text-[11px] leading-snug text-muted-foreground">
          Input device for recording. Names appear after you grant mic access.
        </p>
      </div>
      <select
        value={micDeviceId}
        onChange={(e) => updateSettings({ micDeviceId: e.target.value })}
        className="max-w-[180px] shrink-0 rounded-md border border-border bg-background px-2 py-1 text-xs text-foreground outline-none focus:ring-1 focus:ring-ring"
      >
        <option value="">System default</option>
        {devices.map((d) => (
          <option key={d.deviceId} value={d.deviceId}>
            {d.label || `Microphone (${d.deviceId.slice(0, 6)}…)`}
          </option>
        ))}
      </select>
    </div>
  );
}

function ToggleField({
  label,
  checked,
  hint,
  onChange,
}: {
  label: string;
  checked: boolean;
  hint?: string;
  onChange: (b: boolean) => void;
}) {
  return (
    <div className="flex items-start justify-between gap-3">
      <div className="space-y-0.5">
        <Label className="text-xs text-foreground">{label}</Label>
        {hint && <p className="text-[11px] leading-snug text-muted-foreground">{hint}</p>}
      </div>
      <Switch checked={checked} onCheckedChange={onChange} />
    </div>
  );
}
