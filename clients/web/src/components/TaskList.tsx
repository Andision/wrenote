// The task record, bottom-left in the status bar.
//
// Dismissing a progress toast used to lose the job: the only view of it was
// the toast, so closing one meant there was nowhere left to see whether it
// finished. This is that somewhere — everything the client is tracking or has
// tracked this session, running or dismissed or done, each with the way to
// the session it belongs to.
import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { ArrowRight, CheckCircle2, Loader2, X, XCircle } from "lucide-react";

import { useT } from "@/i18n";
import { formatEta } from "@/lib/jobs";
import { jobLabel, jobStatusLine } from "@/lib/jobText";
import { useJobsStore, type TrackedJob } from "@/store/jobsStore";
import { useSessionStore } from "@/store/sessionStore";

export function TaskList() {
  const jobs = useJobsStore((s) => s.jobs);
  const order = useJobsStore((s) => s.order);
  const clearFinished = useJobsStore((s) => s.clearFinished);
  const t = useT();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("mousedown", onDown);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("mousedown", onDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);

  // Newest first: the one you want is the one you just started.
  const rows = [...order].reverse().map((id) => jobs[id]).filter(Boolean);
  const running = rows.filter((j) => (j.snapshot?.status ?? "running") === "running");
  // Nothing ever tracked this session — no chip, no empty popover.
  if (rows.length === 0) return null;

  return (
    <div ref={ref} className="relative shrink-0">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        data-tip={t("tasks.tooltip")}
        aria-label={t("tasks.tooltip")}
        className={`flex items-center gap-1.5 rounded-md px-1.5 py-1 text-[11.5px] transition-colors ${
          open ? "bg-accent text-foreground" : "hover:bg-accent hover:text-foreground"
        }`}
      >
        {running.length > 0 ? (
          <>
            <Loader2 className="size-3 animate-spin text-brand-500" />
            <span className="tabular-nums">
              {t("tasks.running", { count: running.length })}
            </span>
          </>
        ) : (
          <>
            <CheckCircle2 className="size-3 text-emerald-500" />
            <span>{t("tasks.idle")}</span>
          </>
        )}
      </button>

      <AnimatePresence>
        {open && (
          <motion.div
            initial={{ opacity: 0, y: 6, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 4, scale: 0.98 }}
            transition={{ duration: 0.16, ease: "easeOut" }}
            className="absolute bottom-full left-0 z-50 mb-2 w-[340px] max-w-[calc(100vw-2rem)] overflow-hidden rounded-xl border border-border bg-popover text-popover-foreground shadow-xl"
          >
            <header className="flex items-center justify-between gap-2 border-b border-border/70 px-3 py-2">
              <span className="text-[12px] font-semibold">{t("tasks.title")}</span>
              {rows.some((j) => j.snapshot?.status === "done" || j.snapshot?.status === "error") && (
                <button
                  type="button"
                  onClick={clearFinished}
                  className="text-[11px] text-muted-foreground underline-offset-2 hover:underline"
                >
                  {t("tasks.clear")}
                </button>
              )}
            </header>
            <ul className="max-h-72 divide-y divide-border/60 overflow-y-auto">
              {rows.map((job) => (
                <TaskRow key={job.id} job={job} onNavigate={() => setOpen(false)} />
              ))}
            </ul>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

function TaskRow({ job, onNavigate }: { job: TrackedJob; onNavigate: () => void }) {
  const t = useT();
  const forget = useJobsStore((s) => s.forget);
  const currentSessionId = useSessionStore((s) => s.sessionId);
  const loadSession = useSessionStore((s) => s.loadSession);
  const status = job.snapshot?.status ?? "running";
  const pct = Math.round(((job.snapshot?.fraction ?? 0) * 100) || 0);
  const elsewhere = job.sessionId !== "" && job.sessionId !== currentSessionId;

  return (
    <li className="flex items-start gap-2 px-3 py-2">
      <span className="mt-0.5 shrink-0">
        {status === "done" ? (
          <CheckCircle2 className="size-3.5 text-emerald-500" />
        ) : status === "error" ? (
          <XCircle className="size-3.5 text-destructive" />
        ) : (
          <Loader2 className="size-3.5 animate-spin text-brand-500" />
        )}
      </span>
      <div className="min-w-0 flex-1">
        <div className="truncate text-[12px] font-medium">{jobLabel(t, job)}</div>
        <div className="truncate text-[11px] text-muted-foreground">
          {jobStatusLine(t, job)}
          {status === "running" && ` · ${pct}% · ${formatEta(job.snapshot?.eta_s ?? null)}`}
        </div>
      </div>
      {elsewhere && (
        <button
          type="button"
          onClick={() => {
            void loadSession(job.sessionId);
            onNavigate();
          }}
          data-tip={t("progress.openSession")}
          aria-label={t("progress.openSession")}
          className="shrink-0 rounded p-1 text-muted-foreground hover:bg-accent"
        >
          <ArrowRight className="size-3.5" />
        </button>
      )}
      {status !== "running" && (
        <button
          type="button"
          onClick={() => forget(job.id)}
          data-tip={t("tasks.remove")}
          aria-label={t("tasks.remove")}
          className="shrink-0 rounded p-1 text-muted-foreground hover:bg-accent"
        >
          <X className="size-3.5" />
        </button>
      )}
    </li>
  );
}
