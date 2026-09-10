import { AnimatePresence, motion } from "motion/react";
import { ArrowRight, CheckCircle2, X, XCircle } from "lucide-react";

import { formatEta } from "@/lib/jobs";
import { jobLabel } from "@/lib/jobText";
import { useJobsStore, type TrackedJob } from "@/store/jobsStore";
import { useSessionStore } from "@/store/sessionStore";
import { useT } from "@/i18n";

/**
 * The toasts, bottom-right: one card per job in flight, with a bar and an
 * ETA. Dismissing hides the card, not the job — the list bottom-left
 * (TaskList) is where a dismissed or finished one is still findable, which is
 * also why the raw-log expander is gone from here: a toast you have to unfold
 * is a toast doing the list's job.
 */
export function ProgressOverlay() {
  const jobs = useJobsStore((s) => s.jobs);
  const order = useJobsStore((s) => s.order);

  return (
    <div className="pointer-events-none fixed bottom-4 right-4 z-50 flex w-[360px] max-w-[calc(100vw-2rem)] flex-col gap-2">
      <AnimatePresence initial={false}>
        {order.map((id) => {
          const tracked = jobs[id];
          if (!tracked || tracked.dismissed) return null;
          return <JobCard key={id} tracked={tracked} />;
        })}
      </AnimatePresence>
    </div>
  );
}

function JobCard({ tracked }: { tracked: TrackedJob }) {
  const dismiss = useJobsStore((s) => s.dismiss);
  const currentSessionId = useSessionStore((s) => s.sessionId);
  const loadSession = useSessionStore((s) => s.loadSession);
  const t = useT();
  const snap = tracked.snapshot;
  const status = snap?.status ?? "running";
  const pct = Math.round(((snap?.fraction ?? 0) * 100) || 0);
  const label = jobLabel(t, tracked);
  // A job on some other session: say so, and offer the way there. Without it
  // the card names a session you can't get to from the card.
  const elsewhere = tracked.sessionId !== "" && tracked.sessionId !== currentSessionId;

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
            {label}
          </div>
          <div className="truncate text-[11px] text-muted-foreground">
            {status === "error"
              ? snap?.error || t("progress.failed")
              : status === "done"
                ? t("progress.complete")
                : snap?.phase || t("progress.starting")}
          </div>
        </div>
        {elsewhere && (
          <button
            onClick={() => void loadSession(tracked.sessionId)}
            data-tip={t("progress.openSession")}
            aria-label={t("progress.openSession")}
            className="rounded p-1 text-muted-foreground hover:bg-accent"
          >
            <ArrowRight className="size-3.5" />
          </button>
        )}
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
