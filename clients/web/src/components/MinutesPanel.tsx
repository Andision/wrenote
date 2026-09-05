import { useEffect, useMemo, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import {
  AlertTriangle,
  Check,
  Copy,
  Download,
  FileText,
  Loader2,
  RefreshCw,
  Sparkles,
  X,
} from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  MinutesRefusedError,
  fetchMinutesMarkdown,
  getMinutes,
  startMinutes,
  type Minutes,
  type MinutesState,
} from "@/lib/minutes";
import { downloadText } from "@/lib/export";
import { cn } from "@/lib/utils";
import { useJobsStore } from "@/store/jobsStore";
import { useSessionStore } from "@/store/sessionStore";
import { useT } from "@/i18n";

/**
 * Right-side panel with the meeting minutes the chat model wrote for the
 * session: one document per language, generated on request and kept.
 * Shares the column with chat (one open at a time).
 */
export function MinutesPanel() {
  const open = useSessionStore((s) => s.minutesOpen);
  const sessionId = useSessionStore((s) => s.sessionId);
  return (
    <AnimatePresence initial={false}>
      {open && (
        <motion.aside
          key="minutes-panel"
          initial={{ width: 0, opacity: 0 }}
          animate={{ width: 400, opacity: 1 }}
          exit={{ width: 0, opacity: 0 }}
          transition={{ duration: 0.22, ease: [0.32, 0.72, 0, 1] }}
          className="flex shrink-0 flex-col overflow-hidden border-l bg-card"
        >
          {/* Keyed by session so every piece of panel state starts over with it. */}
          <MinutesBody key={sessionId ?? "none"} sessionId={sessionId} />
        </motion.aside>
      )}
    </AnimatePresence>
  );
}

/** The languages worth a tab: the session's target first, then its source
 *  (when pinned), then any language minutes already exist in. */
function languagesFor(src: string, tgt: string, existing: Minutes[]): string[] {
  const out: string[] = [];
  for (const l of [tgt, src, ...existing.map((m) => m.lang)]) {
    if (l && l !== "auto" && !out.includes(l)) out.push(l);
  }
  return out;
}

export function MinutesBody({ sessionId }: { sessionId: string | null }) {
  const t = useT();
  const toggleMinutes = useSessionStore((s) => s.toggleMinutes);
  const title = useSessionStore((s) => s.sessionTitle);
  const segmentCount = useSessionStore((s) => s.segmentOrder.length);
  const connection = useSessionStore((s) => s.connection);
  const meta = useSessionStore((s) => s.pastSessions.find((p) => p.id === s.sessionId));
  const settings = useSessionStore((s) => s.settings);
  const trackJob = useJobsStore((s) => s.track);

  // null until the first fetch answers (the loading state).
  const [state, setState] = useState<MinutesState | null>(null);
  const [lang, setLang] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);


  // The job writing minutes right now: ours (tracked) or one found via GET.
  const jobId = state?.jobId ?? null;
  const running = useJobsStore((s) => {
    for (const j of Object.values(s.jobs)) {
      if (j.kind === "minutes" && j.sessionId === sessionId && j.snapshot?.status === "running") {
        return j;
      }
    }
    return jobId && s.jobs[jobId]?.snapshot?.status === "running" ? s.jobs[jobId] : null;
  });
  const runningLang = running?.label ?? state?.jobLang ?? null;
  // Fetch on mount, and again once the job we watched ends.
  const runningId = running?.id ?? null;
  useEffect(() => {
    if (runningId || !sessionId) return;
    let cancelled = false;
    getMinutes(sessionId)
      .then((next) => {
        if (cancelled) return;
        setState(next);
        // A job GET told us about (a reload mid-generation): follow it.
        if (next.jobId && !useJobsStore.getState().jobs[next.jobId]) {
          trackJob({ jobId: next.jobId, label: next.jobLang ?? "", kind: "minutes", sessionId });
        }
      })
      .catch((e: unknown) => {
        if (!cancelled) toast.error(e instanceof Error ? e.message : String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [runningId, sessionId, trackJob]);

  const srcLang = meta?.srcLang ?? settings.srcLang;
  const tgtLang = meta?.tgtLang ?? settings.tgtLang;
  const langs = useMemo(
    () => languagesFor(srcLang, tgtLang, state?.minutes ?? []),
    [srcLang, tgtLang, state?.minutes],
  );
  const activeLang = lang && langs.includes(lang) ? lang : (langs[0] ?? null);
  const current = state?.minutes.find((m) => m.lang === activeLang) ?? null;
  const finished =
    Boolean(sessionId) && segmentCount > 0 && (connection === "disconnected" || connection === "error");
  const busy = Boolean(running);

  const generate = async () => {
    if (!sessionId || !activeLang || busy) return;
    try {
      const { jobId } = await startMinutes(sessionId, activeLang);
      // The label is the language; the overlay words it.
      trackJob({ jobId, label: activeLang, kind: "minutes", sessionId });
      setState((s) => (s ? { ...s, jobId, jobLang: activeLang } : s));
    } catch (e) {
      const code = e instanceof MinutesRefusedError ? e.code : null;
      toast.error(
        code === "busy"
          ? t("minutes.busy")
          : code === "no_transcript"
            ? t("minutes.noTranscript")
            : e instanceof Error
              ? e.message
              : String(e),
      );
    }
  };

  const copy = async () => {
    if (!sessionId || !current) return;
    try {
      await navigator.clipboard.writeText(await fetchMinutesMarkdown(sessionId, current.lang));
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
      toast.success(t("minutes.copied"));
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    }
  };

  const download = async () => {
    if (!sessionId || !current) return;
    try {
      const text = await fetchMinutesMarkdown(sessionId, current.lang);
      downloadText(`${title || sessionId} - ${t("minutes.title")}`, "md", text);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <div className="flex h-full w-[400px] flex-col">
      <header className="flex h-12 items-center gap-1 border-b px-3">
        <FileText className="size-4 shrink-0 text-brand-600 dark:text-brand-400" />
        <span className="min-w-0 flex-1 truncate text-sm font-semibold text-foreground">
          {t("minutes.title")}
        </span>
        {current && (
          <>
            <Button variant="ghost" size="icon" className="size-7" onClick={() => void copy()} data-tip={t("minutes.copy")}>
              {copied ? <Check className="size-4 text-green-600" /> : <Copy className="size-4" />}
            </Button>
            <Button variant="ghost" size="icon" className="size-7" onClick={() => void download()} data-tip={t("minutes.download")}>
              <Download className="size-4" />
            </Button>
          </>
        )}
        <Button variant="ghost" size="icon" className="size-7" onClick={() => toggleMinutes(false)} data-tip={t("common.close")}>
          <X className="size-4" />
        </Button>
      </header>

      {langs.length > 1 && (
        <div className="flex gap-1 border-b px-3 py-2">
          {langs.map((l) => {
            const has = state?.minutes.some((m) => m.lang === l);
            return (
              <button
                key={l}
                type="button"
                onClick={() => setLang(l)}
                aria-pressed={l === activeLang}
                className={cn(
                  "rounded-md px-2.5 py-1 text-[12px] font-medium uppercase transition-colors",
                  l === activeLang ? "bg-brand-600 text-white" : "bg-accent/50 text-foreground hover:bg-accent",
                  !has && l !== activeLang && "opacity-60",
                )}
              >
                {l}
                {runningLang === l && busy && <Loader2 className="ml-1 inline size-3 animate-spin" />}
              </button>
            );
          })}
        </div>
      )}

      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4">
        {!sessionId || (segmentCount === 0 && !current) ? (
          <Empty title={t("minutes.noSessionTitle")} hint={t("minutes.noSessionHint")} />
        ) : busy && runningLang === activeLang ? (
          <Progress fraction={running?.snapshot?.fraction ?? 0} phase={running?.snapshot?.phase ?? ""} />
        ) : !state ? (
          <Empty title={t("minutes.loading")} hint="" spinner />
        ) : !current ? (
          <div className="flex flex-col items-center gap-3 py-10 text-center">
            <Sparkles className="size-8 text-brand-500" />
            <p className="text-sm font-medium text-foreground">{t("minutes.emptyTitle")}</p>
            <p className="max-w-[28ch] text-[12.5px] text-muted-foreground">{t("minutes.emptyHint")}</p>
            <Button onClick={() => void generate()} disabled={!finished || busy} className="mt-2 gap-1.5">
              <Sparkles className="size-4" />
              {t("minutes.generate")}
            </Button>
            {!finished && <p className="text-[11px] text-muted-foreground">{t("minutes.waitForStop")}</p>}
          </div>
        ) : (
          <Document
            minutes={current}
            onRegenerate={() => void generate()}
            canRegenerate={finished && !busy}
          />
        )}
      </div>
    </div>
  );
}

function Empty({ title, hint, spinner }: { title: string; hint: string; spinner?: boolean }) {
  return (
    <div className="flex flex-col items-center gap-2 py-10 text-center">
      {spinner ? (
        <Loader2 className="size-6 animate-spin text-muted-foreground" />
      ) : (
        <FileText className="size-8 text-muted-foreground/60" />
      )}
      <p className="text-sm font-medium text-foreground">{title}</p>
      {hint && <p className="max-w-[28ch] text-[12.5px] text-muted-foreground">{hint}</p>}
    </div>
  );
}

function Progress({ fraction, phase }: { fraction: number; phase: string }) {
  const t = useT();
  const pct = Math.round(fraction * 100);
  return (
    <div role="status" className="flex flex-col items-center gap-3 py-10 text-center">
      <Loader2 className="size-6 animate-spin text-brand-500" />
      <p className="text-sm font-medium text-foreground">{t("minutes.writing")}</p>
      <div className="w-48">
        <div className="relative h-1.5 w-full overflow-hidden rounded-full bg-muted">
          <motion.div
            className="absolute inset-y-0 left-0 rounded-full bg-brand-500"
            animate={{ width: `${pct}%` }}
            transition={{ duration: 0.25, ease: "easeOut" }}
          />
        </div>
        <div className="mt-1 text-[11px] tabular-nums text-muted-foreground">
          {pct}% {phase && `· ${phase}`}
        </div>
      </div>
    </div>
  );
}

function Document({
  minutes,
  onRegenerate,
  canRegenerate,
}: {
  minutes: Minutes;
  onRegenerate: () => void;
  canRegenerate: boolean;
}) {
  const t = useT();
  const c = minutes.content;
  const when = new Date(minutes.generatedAt);
  return (
    <div className="space-y-5 text-[13.5px] leading-relaxed text-foreground">
      {minutes.stale && (
        <div role="note" className="flex items-start gap-2 rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-[12px] text-amber-800 dark:text-amber-300">
          <AlertTriangle className="mt-0.5 size-3.5 shrink-0" />
          <span>{t("minutes.stale")}</span>
        </div>
      )}
      {c.summary && (
        <Section title={t("minutes.section.summary")}>
          <p className="whitespace-pre-wrap">{c.summary}</p>
        </Section>
      )}
      {c.key_points.length > 0 && (
        <Section title={t("minutes.section.keyPoints")}>
          <ul className="list-disc space-y-1 pl-5">{c.key_points.map((x, i) => <li key={i}>{x}</li>)}</ul>
        </Section>
      )}
      {c.decisions.length > 0 && (
        <Section title={t("minutes.section.decisions")}>
          <ul className="list-disc space-y-1 pl-5">{c.decisions.map((x, i) => <li key={i}>{x}</li>)}</ul>
        </Section>
      )}
      {c.action_items.length > 0 && (
        <Section title={t("minutes.section.actionItems")}>
          <ul className="space-y-1.5">
            {c.action_items.map((a, i) => (
              <li key={i} className="flex items-start gap-2">
                <span className="mt-[5px] size-3 shrink-0 rounded-sm border border-muted-foreground/60" aria-hidden />
                <span>
                  {a.text}
                  {(a.owner || a.due) && (
                    <span className="ml-1.5 inline-flex gap-1 align-middle">
                      {a.owner && <Chip>{a.owner}</Chip>}
                      {a.due && <Chip>{a.due}</Chip>}
                    </span>
                  )}
                </span>
              </li>
            ))}
          </ul>
        </Section>
      )}
      {c.open_questions.length > 0 && (
        <Section title={t("minutes.section.openQuestions")}>
          <ul className="list-disc space-y-1 pl-5">{c.open_questions.map((x, i) => <li key={i}>{x}</li>)}</ul>
        </Section>
      )}
      <div className="flex items-center justify-between border-t pt-3 text-[11px] text-muted-foreground">
        <span>
          {t("minutes.generatedAt", { when: when.toLocaleString() })}
          {minutes.model && ` · ${minutes.model}`}
        </span>
        <Button variant="ghost" size="sm" className="h-7 gap-1.5 text-xs" onClick={onRegenerate} disabled={!canRegenerate}>
          <RefreshCw className="size-3.5" />
          {t("minutes.regenerate")}
        </Button>
      </div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section>
      <h3 className="mb-1.5 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">{title}</h3>
      {children}
    </section>
  );
}

function Chip({ children }: { children: React.ReactNode }) {
  return (
    <span className="rounded-full bg-accent px-1.5 py-0.5 text-[10.5px] font-medium text-foreground">{children}</span>
  );
}
