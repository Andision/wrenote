import { useEffect, useState, useSyncExternalStore } from "react";
import { AnimatePresence, motion } from "motion/react";
import {
  BookMarked,
  Boxes,
  Cpu,
  Gauge,
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

import { ComputePanel } from "@/components/ComputePanel";
import { GlossaryEditor } from "@/components/GlossaryEditor";
import { ModelsPanel } from "@/components/ModelsPanel";
import { UpdatePanel } from "@/components/UpdatePanel";
import { Label } from "@/components/ui/label";
import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";
import { useSessionStore } from "@/store/sessionStore";
import { LOCALE_LIST, useI18n, useT } from "@/i18n";

type CategoryId =
  | "general"
  | "segmentation"
  | "realtime"
  | "glossary"
  | "models"
  | "engines"
  | "compute";

// Labels are message keys; the rail resolves them at render.
const CATEGORIES: { id: CategoryId; icon: LucideIcon }[] = [
  { id: "general", icon: SlidersHorizontal },
  { id: "segmentation", icon: Scissors },
  { id: "realtime", icon: Zap },
  { id: "glossary", icon: BookMarked },
  { id: "models", icon: Boxes },
  { id: "engines", icon: Cpu },
  { id: "compute", icon: Gauge },
];

/**
 * Settings modal — a centered floating panel with a category rail on the
 * left and the selected category's controls on the right (ChatGPT-style).
 * Esc or a backdrop click closes it.
 */
export function SettingsDrawer() {
  const t = useT();
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

  const activeLabel = t(`settings.cat.${cat}`);

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
                {t("settings.title")}
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
                      {t(`settings.cat.${c.id}`)}
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
                  data-tip={t("common.closeEsc")}
                  className="rounded-md p-1 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
                >
                  <X className="size-4" />
                </button>
              </header>

              <div className="min-h-0 flex-1 overflow-y-auto px-5 py-5">
                {cat === "general" && (
                  <div className="space-y-5">
                    <LanguageField />
                    <ThemeField />
                    {/* Mic device, Record system audio, and Record screen moved
                        to the PreFlight "Capture sources" row — they're
                        per-recording input choices, not global preferences. */}
                    <ToggleField
                      label={t("settings.speakerLive")}
                      checked={settings.speakerEnabled}
                      hint={t("settings.speakerLiveHint")}
                      onChange={(v) => updateSettings({ speakerEnabled: v })}
                    />
                    <ToggleField
                      label={t("settings.continuousPlayback")}
                      checked={settings.playbackMode === "continuous"}
                      hint={t("settings.continuousPlaybackHint")}
                      onChange={(v) =>
                        updateSettings({ playbackMode: v ? "continuous" : "single" })
                      }
                    />
                    <ToggleField
                      label={t("settings.levelMeters")}
                      checked={settings.showLevelMeters}
                      hint={t("settings.levelMetersHint")}
                      onChange={(v) => updateSettings({ showLevelMeters: v })}
                    />
                    <div className="border-t border-border pt-5">
                      <UpdatePanel />
                    </div>
                  </div>
                )}

                {cat === "segmentation" && (
                  <div className="space-y-5">
                    {sessionInProgress && <NextSessionNote />}
                    <RangeField
                      label={t("settings.minSilence")}
                      value={settings.minSilenceMs}
                      min={200}
                      max={2000}
                      step={50}
                      unit="ms"
                      hint={t("settings.minSilenceHint")}
                      onChange={(v) => updateSettings({ minSilenceMs: v })}
                    />
                    <RangeField
                      label={t("settings.maxSegment")}
                      value={settings.maxSegmentMs}
                      min={5000}
                      max={29000}
                      step={1000}
                      unit="ms"
                      hint={t("settings.maxSegmentHint")}
                      onChange={(v) => updateSettings({ maxSegmentMs: v })}
                    />
                  </div>
                )}

                {cat === "realtime" && (
                  <div className="space-y-5">
                    {sessionInProgress && <NextSessionNote />}
                    <RangeField
                      label={t("settings.partialInterval")}
                      value={settings.partialIntervalMs}
                      min={300}
                      max={2000}
                      step={100}
                      unit="ms"
                      hint={t("settings.partialIntervalHint")}
                      onChange={(v) => updateSettings({ partialIntervalMs: v })}
                    />
                    <ToggleField
                      label={t("settings.translatePartials")}
                      checked={settings.translatePartials}
                      hint={t("settings.translatePartialsHint")}
                      onChange={(v) => updateSettings({ translatePartials: v })}
                    />
                  </div>
                )}

                {cat === "glossary" && <GlossaryEditor />}

                {cat === "models" && <ModelsPanel />}

                {cat === "compute" && <ComputePanel />}

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
                        {t("settings.enginesEmpty")}
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
  { id: "light", icon: Sun },
  { id: "dark", icon: Moon },
  { id: "system", icon: Monitor },
] as const;

/**
 * UI language. "Auto" follows the OS; the rest come from whatever locale files
 * shipped, named by their own `$meta.name`, so a new language needs no code.
 */
function LanguageField() {
  const t = useT();
  const { preference, setPreference } = useI18n();
  return (
    <div className="flex items-center justify-between gap-3">
      <div className="space-y-0.5">
        <Label className="text-xs text-foreground">{t("settings.language")}</Label>
        <p className="text-[11px] leading-snug text-muted-foreground">
          {t("settings.languageHint")}
        </p>
      </div>
      <select
        value={preference}
        onChange={(e) => setPreference(e.target.value)}
        className="h-8 shrink-0 rounded-lg border border-border bg-card px-2 text-[12px] text-foreground shadow-sm focus:outline-none focus:ring-2 focus:ring-brand-500/40"
      >
        <option value="auto">{t("settings.languageAuto")}</option>
        {LOCALE_LIST.map((l) => (
          <option key={l.tag} value={l.tag}>
            {l.name}
          </option>
        ))}
      </select>
    </div>
  );
}

/**
 * Appearance picker — Light / Dark / Follow system. Lives in Settings now
 * (used to be a top-bar icon). next-themes resolves the active theme only
 * after mount, so we default the highlight to "system" until then.
 */
function ThemeField() {
  const t = useT();
  const { theme, setTheme } = useTheme();
  // Hydration-safe "are we on the client yet" flag — next-themes only knows the
  // resolved theme after mount. useSyncExternalStore avoids a setState-in-effect.
  const mounted = useSyncExternalStore(
    () => () => {},
    () => true,
    () => false,
  );
  const current =
    mounted && ["light", "dark", "system"].includes(theme ?? "")
      ? (theme as string)
      : "system";

  return (
    <div className="flex items-center justify-between gap-3">
      <div className="space-y-0.5">
        <Label className="text-xs text-foreground">{t("settings.appearance")}</Label>
        <p className="text-[11px] leading-snug text-muted-foreground">
          {t("settings.appearanceHint")}
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
              data-tip={t(`theme.${o.id}`)}
              className={`inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-[11px] font-medium transition-colors ${
                active
                  ? "bg-card text-foreground shadow-sm"
                  : "text-muted-foreground hover:text-foreground"
              }`}
            >
              <Icon className="size-3.5" />
              {t(`theme.${o.id}`)}
            </button>
          );
        })}
      </div>
    </div>
  );
}

function NextSessionNote() {
  const t = useT();
  return (
    <p className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-700 dark:border-amber-900/50 dark:bg-amber-950/30 dark:text-amber-300">
      {t("settings.nextSessionNote")}
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
