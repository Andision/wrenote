import { useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { CheckCircle2, ChevronDown, ChevronUp, X, XCircle } from "lucide-react";

import { formatEta } from "@/lib/jobs";
import { useJobsStore, type TrackedJob } from "@/store/jobsStore";
import { useT } from "@/i18n";

/**
 * Floating popover (bottom-right) listing every backend job we're
 * subscribed to. Hides itself when no jobs are tracked. Each card shows a
 * bar + ETA; "details" expands the raw log for the curious.
 */
export function ProgressOverlay() {
  const jobs = useJobsStore((s) => s.jobs);
  const order = useJobsStore((s) => s.order);

  return (
    <div className="pointer-events-none fixed bottom-4 right-4 z-50 flex w-[360px] max-w-[calc(100vw-2rem)] flex-col gap-2">
      <AnimatePresence initial={false}>
        {order.map((id) => {
          const tracked = jobs[id];
          if (!tracked) return null;
          return <JobCard key={id} tracked={tracked} />;
        })}
      </AnimatePresence>
    </div>
  );
}

function JobCard({ tracked }: { tracked: TrackedJob }) {
  const dismiss = useJobsStore((s) => s.dismiss);
  const [expanded, setExpanded] = useState(false);
  const t = useT();
  const snap = tracked.snapshot;
  const status = snap?.status ?? "running";
  const pct = Math.round(((snap?.fraction ?? 0) * 100) || 0);

  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: 16, scale: 0.96 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={{ opacity: 0, y: 8, scale: 0.96 }}
      transition={{ duration: 0.22, ease: [0.22, 0.61, 0.36, 1] }}
      className="pointer-events-auto overflow-hidden rounded-xl border border-border bg-card shadow-lg"
    >
      <header className="flex items-center gap-2 px-3 py-2">
        {status === "done" ? (
          <CheckCircle2 className="size-4 text-emerald-500" />
        ) : status === "error" ? (
          <XCircle className="size-4 text-destructive" />
        ) : (
          <RunningDot />
        )}
        <div className="min-w-0 flex-1">
          <div className="truncate text-[13px] font-medium text-foreground">
            {tracked.label}
          </div>
          <div className="truncate text-[11px] text-muted-foreground">
            {status === "error"
              ? snap?.error || t("progress.failed")
              : status === "done"
                ? t("progress.complete")
                : snap?.phase || t("progress.starting")}
          </div>
        </div>
        <button
          onClick={() => setExpanded((x) => !x)}
          data-tip={expanded ? t("progress.hideLog") : t("progress.showLog")}
          className="rounded p-1 text-muted-foreground hover:bg-accent"
        >
          {expanded ? (
            <ChevronDown className="size-3.5" />
          ) : (
            <ChevronUp className="size-3.5" />
          )}
        </button>
        <button
          onClick={() => dismiss(tracked.id)}
          data-tip={t("common.dismiss")}
          className="rounded p-1 text-muted-foreground hover:bg-accent"
        >
          <X className="size-3.5" />
        </button>
      </header>

      {/* Bar + ETA */}
      <div className="px-3 pb-2">
        <div className="relative h-1.5 w-full overflow-hidden rounded-full bg-muted">
          <motion.div
            className={
              status === "error"
                ? "absolute inset-y-0 left-0 rounded-full bg-destructive"
                : status === "done"
                  ? "absolute inset-y-0 left-0 rounded-full bg-emerald-500"
                  : "absolute inset-y-0 left-0 rounded-full bg-brand-500"
            }
            animate={{ width: `${pct}%` }}
            transition={{ duration: 0.25, ease: "easeOut" }}
          />
        </div>
        <div className="mt-1 flex items-center justify-between text-[10.5px] tabular-nums text-muted-foreground">
          <span>{pct}%</span>
          {status === "running" && (
            <span>{formatEta(snap?.eta_s ?? null)}</span>
          )}
        </div>
      </div>

      {/* Log (expandable) */}
      <AnimatePresence initial={false}>
        {expanded && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.18 }}
            className="border-t border-border/70 bg-muted/30"
          >
            <pre className="max-h-32 overflow-y-auto px-3 py-2 font-mono text-[10.5px] leading-snug text-muted-foreground">
              {(snap?.log ?? []).join("\n") || "(no log yet)"}
            </pre>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  );
}

function RunningDot() {
  return (
    <motion.span
      className="size-2 rounded-full bg-brand-500"
      animate={{ scale: [1, 1.25, 1], opacity: [1, 0.5, 1] }}
      transition={{ duration: 1.4, repeat: Infinity, ease: "easeInOut" }}
    />
  );
}
