// Settings → Models: which model each part of the pipeline uses.
//
// The same ranked options the first-run wizard shows, plus the consequence a
// wizard doesn't have to state: a change here lands either immediately or on
// the next session, depending on where the backend is built. Files a new choice
// needs are downloaded from here too, so choosing is one action, not two.
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import { ModelPicker } from "@/components/ModelPicker";
import { kindReason } from "@/lib/modelText";
import { Button } from "@/components/ui/button";
import { useT } from "@/i18n";
import { formatEta, subscribeJob } from "@/lib/jobs";
import {
  type ModelKind,
  type ModelStatus,
  getModelStatus,
  selectModel,
  startModelDownload,
} from "@/lib/models";

export function ModelsPanel() {
  const t = useT();
  const [status, setStatus] = useState<ModelStatus | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState<{ fraction: number; eta: number | null } | null>(null);
  const [nextSession, setNextSession] = useState(false);

  const refresh = useCallback(async () => {
    try {
      setStatus(await getModelStatus());
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : t("models.statusFailed"));
    }
  }, [t]);

  useEffect(() => {
    let cancelled = false;
    getModelStatus()
      .then((s) => {
        if (!cancelled) setStatus(s);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e.message : t("models.statusFailed"));
      });
    return () => {
      cancelled = true;
    };
  }, [t]);

  const choose = useCallback(
    async (kind: ModelKind, id: string) => {
      setBusy(true);
      setError("");
      try {
        const res = await selectModel(kind, id);
        if (res.applies === "next_session") setNextSession(true);
        await refresh();
      } catch (e) {
        toast.error(e instanceof Error ? e.message : t("models.selectFailed"));
      } finally {
        setBusy(false);
      }
    },
    [refresh, t],
  );

  const download = useCallback(() => {
    setError("");
    setProgress({ fraction: 0, eta: null });
    startModelDownload()
      .then((res) => {
        if (!res.job_id) {
          setProgress(null);
          void refresh();
          return;
        }
        subscribeJob(res.job_id, {
          onSnapshot: (snap) => {
            setProgress({ fraction: snap.fraction, eta: snap.eta_s });
            if (snap.status !== "running") {
              setProgress(null);
              if (snap.status === "error") toast.error(snap.error ?? t("setup.downloadFailed"));
              void refresh();
            }
          },
          onError: () => {
            setProgress(null);
            toast.error(t("compute.streamLost"));
          },
        });
      })
      .catch((e: unknown) => {
        setProgress(null);
        toast.error(e instanceof Error ? e.message : t("setup.downloadFailed"));
      });
  }, [refresh, t]);

  if (error && !status) {
    return (
      <p className="rounded-lg border border-dashed border-border/60 px-3 py-8 text-center text-[12px] text-muted-foreground">
        {error}
      </p>
    );
  }
  if (!status) {
    return <p className="px-1 py-6 text-[12px] text-muted-foreground">{t("models.loading")}</p>;
  }

  const missing = status.models.filter((m) => !m.present);
  const missingMb = Math.round(missing.reduce((a, m) => a + m.size, 0) / 1048576);

  return (
    <div className="space-y-5">
      {status.options.map((kind) => (
        <section key={kind.kind} className="space-y-2">
          <div className="flex items-baseline justify-between gap-2">
            <h3 className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
              {t(`models.kind.${kind.kind}`)}
            </h3>
            <span className="text-[11px] text-muted-foreground/70">{kindReason(t, kind)}</span>
          </div>
          <ModelPicker kind={kind} busy={busy} onPick={(id) => void choose(kind.kind, id)} />
        </section>
      ))}

      {missing.length > 0 && (
        <div className="space-y-2 rounded-lg border border-border/60 bg-muted/30 p-3">
          <p className="text-[12px] text-muted-foreground">
            {t("models.missing", { count: missing.length, mb: missingMb })}
          </p>
          {progress ? (
            <>
              <div className="h-1.5 overflow-hidden rounded-full bg-muted">
                <div
                  className="h-full rounded-full bg-brand-500 transition-[width]"
                  style={{ width: `${Math.round(progress.fraction * 100)}%` }}
                />
              </div>
              <p className="text-[11px] tabular-nums text-muted-foreground">
                {Math.round(progress.fraction * 100)}% · {formatEta(progress.eta)}
              </p>
            </>
          ) : (
            <Button size="sm" onClick={download} disabled={busy}>
              {t("models.downloadMissing")}
            </Button>
          )}
        </div>
      )}

      {nextSession && (
        <p className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-700 dark:border-amber-900/50 dark:bg-amber-950/30 dark:text-amber-300">
          {t("models.nextSession")}
        </p>
      )}
    </div>
  );
}
