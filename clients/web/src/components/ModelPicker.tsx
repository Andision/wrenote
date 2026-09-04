// Model choices for one kind, ranked for this machine.
//
// Shared by the first-run wizard and Settings → Models so the two can't drift
// into describing the same choice differently. The engine ranks and explains
// (codes + facts); this only renders.
import { Check, Cpu, Download } from "lucide-react";

import { useT } from "@/i18n";
import { type KindOptions, type ModelOption } from "@/lib/models";
import { modelNote } from "@/lib/modelText";

export function ModelPicker({
  kind,
  busy,
  onPick,
}: {
  kind: KindOptions;
  busy?: boolean;
  onPick: (id: string) => void;
}) {
  return (
    <div className="space-y-1.5">
      {kind.options.map((o) => (
        <ModelRow key={o.id} option={o} busy={busy} onPick={() => onPick(o.id)} />
      ))}
    </div>
  );
}

function ModelRow({
  option,
  busy,
  onPick,
}: {
  option: ModelOption;
  busy?: boolean;
  onPick: () => void;
}) {
  const t = useT();
  // A model the machine can't run stays visible and explains itself, but is not
  // selectable — offering it would only produce a confusing failure later.
  const blocked = !option.fits;

  return (
    <button
      type="button"
      onClick={onPick}
      disabled={busy || blocked}
      className={`w-full rounded-lg border px-3 py-2.5 text-left transition-colors ${
        option.selected
          ? "border-brand-500 bg-brand-500/10"
          : "border-border/50 bg-background/40 hover:bg-muted/60"
      } ${blocked ? "cursor-not-allowed opacity-50 hover:bg-background/40" : ""}`}
    >
      <div className="flex items-center gap-2">
        {option.installed ? (
          <Check className="size-3.5 shrink-0 text-emerald-500" />
        ) : blocked ? (
          <Cpu className="size-3.5 shrink-0 text-muted-foreground" />
        ) : (
          <Download className="size-3.5 shrink-0 text-muted-foreground" />
        )}
        <span className="text-[13px] font-medium">{option.name}</span>
        {option.recommended && !blocked && (
          <span className="rounded-full bg-brand-500/15 px-1.5 py-0.5 text-[10px] font-medium text-brand-600 dark:text-brand-400">
            {t("setup.recommended")}
          </span>
        )}
        <span className="ml-auto shrink-0 text-[11px] tabular-nums text-muted-foreground">
          {option.installed
            ? t("models.onDisk")
            : t("models.sizeMb", { mb: option.size_mb })}
        </span>
      </div>
      <div className="pl-5.5 text-[11px] text-muted-foreground/80">
        {modelNote(t, option)}
      </div>
    </button>
  );
}
