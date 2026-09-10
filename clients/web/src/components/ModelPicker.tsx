// Model choices for one kind, ranked for this machine.
//
// Shared by the first-run wizard and Settings → Models so the two can't drift
// into describing the same choice differently. The engine ranks and explains
// (codes + facts); this only renders.
import { Check, Cpu, Download, Trash2 } from "lucide-react";

import { useT } from "@/i18n";
import { iconTip } from "@/lib/tooltip";
import { type KindOptions, type ModelOption } from "@/lib/models";
import { modelNote, modelTags } from "@/lib/modelText";

export function ModelPicker({
  kind,
  busy,
  onPick,
  onDelete,
}: {
  kind: KindOptions;
  busy?: boolean;
  onPick: (id: string) => void;
  /** Offered on rows whose files are on disk. Absent in the first-run
   *  wizard, where there is nothing downloaded yet to remove. */
  onDelete?: (option: ModelOption) => void;
}) {
  return (
    <div className="space-y-1.5">
      {kind.options.map((o) => (
        <ModelRow
          key={o.id}
          option={o}
          busy={busy}
          onPick={() => onPick(o.id)}
          onDelete={onDelete && o.installed ? () => onDelete(o) : undefined}
        />
      ))}
    </div>
  );
}

function ModelRow({
  option,
  busy,
  onPick,
  onDelete,
}: {
  option: ModelOption;
  busy?: boolean;
  onPick: () => void;
  onDelete?: () => void;
}) {
  const t = useT();
  // A model the machine can't run stays visible and explains itself, but is not
  // selectable — offering it would only produce a confusing failure later.
  const blocked = !option.fits;

  // The delete control is a sibling of the row, not a child: a <button>
  // inside a <button> is invalid, and nesting it would also make every
  // click on it a click on the row.
  return (
    <div className="relative">
      <button
        type="button"
        onClick={onPick}
        disabled={busy || blocked}
        data-tip={modelNote(t, option) || undefined}
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
          <span
            className={`ml-auto shrink-0 text-[11px] tabular-nums text-muted-foreground ${
              onDelete ? "pr-7" : ""
            }`}
          >
            {option.installed
              ? t("models.onDisk")
              : t("models.sizeMb", { mb: option.size_mb })}
          </span>
        </div>
        {/* Tier and memory as tags; the sentence the catalogue carries is the
            tooltip, so a reader who wants it can hover for it. */}
        <div className="flex flex-wrap gap-1 pl-5.5 pt-1">
          {modelTags(t, option).map((tag) => (
            <span
              key={tag.key}
              className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${
                tag.tone === "blocked"
                  ? "bg-destructive/10 text-destructive"
                  : "bg-muted text-muted-foreground"
              }`}
            >
              {tag.label}
            </span>
          ))}
        </div>
      </button>
      {onDelete && (
        <button
          type="button"
          onClick={onDelete}
          disabled={busy}
          {...iconTip(t("models.deleteFiles", { mb: option.size_mb }))}
          className="absolute right-2 top-2 inline-flex size-6 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-destructive/10 hover:text-destructive disabled:opacity-50"
        >
          <Trash2 className="size-3.5" />
        </button>
      )}
    </div>
  );
}
