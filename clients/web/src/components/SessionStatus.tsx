import { AlertTriangle, Loader2, RefreshCw } from "lucide-react";
import { motion } from "motion/react";

import { Button } from "@/components/ui/button";
import { useActiveSessionMeta, useRefineProgress } from "@/hooks/useSessionStatus";
import { useT } from "@/i18n";
import type { SessionStatus } from "@/types";

/**
 * The small marker next to a session in the sidebar. Only the states that
 * mean "not what you'd expect" get one: a pass in progress, or one that
 * failed. `ready` is the default and says nothing; `recording` is shown by
 * the transport controls already.
 */
export function StatusBadge({ status }: { status: SessionStatus }) {
  const t = useT();
  if (status === "processing") {
    return (
      <span
        className="inline-flex shrink-0 items-center gap-1 rounded-full bg-brand-500/10 px-1.5 py-0.5 text-[10px] font-medium text-brand-600 dark:text-brand-400"
        data-tip={t("session.status.processingHint")}
      >
        <Loader2 className="size-3 animate-spin" />
        {t("session.status.processing")}
      </span>
    );
  }
  if (status === "failed") {
    return (
      <span
        className="inline-flex shrink-0 items-center gap-1 rounded-full bg-destructive/10 px-1.5 py-0.5 text-[10px] font-medium text-destructive"
        data-tip={t("session.status.failedHint")}
      >
        <AlertTriangle className="size-3" />
        {t("session.status.failed")}
      </span>
    );
  }
  return null;
}

/**
 * The strip above the transcript while the engine re-transcribes the
 * recording, and after that pass fails. The rows below it are the live
 * transcript, still readable; the strip says they are about to be replaced
 * (or weren't), and offers the retry.
 */
export function ProcessingBanner({ onRetry }: { onRetry: () => void }) {
  const t = useT();
  const meta = useActiveSessionMeta();
  const progress = useRefineProgress(meta?.id ?? null);
  if (!meta || (meta.status !== "processing" && meta.status !== "failed")) return null;

  if (meta.status === "processing") {
    const pct = progress == null ? null : Math.round(progress * 100);
    return (
      <div
        role="status"
        className="flex items-center gap-3 border-b border-brand-500/20 bg-brand-500/5 px-6 py-2 text-[12.5px] text-foreground"
      >
        <Loader2 className="size-4 shrink-0 animate-spin text-brand-500" />
        <div className="min-w-0 flex-1">
          <span className="font-medium">{t("session.processing.title")}</span>
          <span className="text-muted-foreground"> · {t("session.processing.body")}</span>
        </div>
        {pct != null && (
          <div className="flex w-32 shrink-0 items-center gap-2">
            <div className="relative h-1.5 flex-1 overflow-hidden rounded-full bg-muted">
              <motion.div
                className="absolute inset-y-0 left-0 rounded-full bg-brand-500"
                animate={{ width: `${pct}%` }}
                transition={{ duration: 0.25, ease: "easeOut" }}
              />
            </div>
            <span className="w-8 text-right text-[11px] tabular-nums text-muted-foreground">
              {pct}%
            </span>
          </div>
        )}
      </div>
    );
  }

  return (
    <div
      role="alert"
      className="flex items-center gap-3 border-b border-destructive/20 bg-destructive/5 px-6 py-2 text-[12.5px] text-foreground"
    >
      <AlertTriangle className="size-4 shrink-0 text-destructive" />
      <div className="min-w-0 flex-1">
        <span className="font-medium">{t("session.failed.title")}</span>
        <span className="text-muted-foreground">
          {" "}
          · {t("session.failed.body")}
          {meta.statusDetail ? ` (${meta.statusDetail})` : ""}
        </span>
      </div>
      <Button variant="outline" size="sm" onClick={onRetry} className="h-7 gap-1.5 text-xs">
        <RefreshCw className="size-3.5" />
        {t("session.failed.retry")}
      </Button>
    </div>
  );
}
