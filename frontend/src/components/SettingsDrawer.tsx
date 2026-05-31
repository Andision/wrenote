import { useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { ChevronRight } from "lucide-react";

import { Label } from "@/components/ui/label";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";
import { useSessionStore } from "@/store/sessionStore";

export function SettingsDrawer() {
  const open = useSessionStore((s) => s.settingsOpen);
  const settings = useSessionStore((s) => s.settings);
  const updateSettings = useSessionStore((s) => s.updateSettings);
  const connection = useSessionStore((s) => s.connection);
  const ready = useSessionStore((s) => s.ready);
  const sessionInProgress =
    connection === "recording" || connection === "stopping";

  const [advancedOpen, setAdvancedOpen] = useState(false);

  return (
    <Sheet open={open} onOpenChange={(o) => useSessionStore.getState().toggleSettings(o)}>
      <SheetContent side="right" className="w-[380px] sm:w-[400px]">
        <SheetHeader>
          <SheetTitle>Settings</SheetTitle>
          <SheetDescription>
            Behaviour, performance, and the engines under the hood.
          </SheetDescription>
        </SheetHeader>

        <div className="space-y-6 overflow-y-auto px-4 pb-6">
          {sessionInProgress && (
            <p className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-700 dark:border-amber-900/50 dark:bg-amber-950/30 dark:text-amber-300">
              Some changes only take effect on the next session.
            </p>
          )}

          <Section title="General">
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
          </Section>

          <Disclosure
            open={advancedOpen}
            onToggle={() => setAdvancedOpen((o) => !o)}
            title="Advanced"
          >
            <Section title="Segmentation">
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
            </Section>

            <Section title="Real-time">
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
            </Section>

            <Section title="Backends">
              {ready ? (
                <div className="space-y-2 rounded-lg border border-border/60 bg-muted/30 p-3">
                  <BackendRow
                    label="STT"
                    info={ready.stt}
                  />
                  <BackendRow
                    label="MT"
                    info={ready.translator}
                  />
                  <BackendRow
                    label="VAD"
                    info={ready.vad}
                  />
                  {ready.speaker && (
                    <BackendRow
                      label="Speaker"
                      info={ready.speaker}
                    />
                  )}
                </div>
              ) : (
                <p className="rounded-lg border border-dashed border-border/60 px-3 py-4 text-center text-[11px] text-muted-foreground">
                  Start a session to see the active backends.
                </p>
              )}
            </Section>
          </Disclosure>
        </div>
      </SheetContent>
    </Sheet>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="space-y-4">
      <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
        {title}
      </h3>
      <div className="space-y-4">{children}</div>
    </section>
  );
}

function Disclosure({
  open,
  onToggle,
  title,
  children,
}: {
  open: boolean;
  onToggle: () => void;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-3">
      <button
        type="button"
        onClick={onToggle}
        className="flex w-full items-center justify-between rounded-md py-1 text-left transition-colors hover:text-foreground"
      >
        <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          {title}
        </span>
        <motion.span
          animate={{ rotate: open ? 90 : 0 }}
          transition={{ duration: 0.18 }}
          className="text-muted-foreground"
        >
          <ChevronRight className="size-3.5" />
        </motion.span>
      </button>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            key="advanced-body"
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.22, ease: [0.22, 0.61, 0.36, 1] }}
            className="overflow-hidden"
          >
            <div className="space-y-6 pt-2">{children}</div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
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
        <div className="truncate font-medium text-foreground">
          {info.name ?? "—"}
        </div>
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
