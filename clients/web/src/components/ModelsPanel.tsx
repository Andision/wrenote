// Settings → Models: which model each part of the pipeline uses.
//
// The same ranked options the first-run wizard shows, plus the consequence a
// wizard doesn't have to state: a change here lands either immediately or on
// the next session, depending on where the backend is built. Files a new choice
// needs are downloaded from here too, so choosing is one action, not two.
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import { EndpointCard } from "@/components/EndpointCard";
import { ModelPicker } from "@/components/ModelPicker";
import { kindReason } from "@/lib/modelText";
import { Button } from "@/components/ui/button";
import { useT } from "@/i18n";
import { formatEta, subscribeJob } from "@/lib/jobs";
import { confirmDialog } from "@/lib/confirm";
import {
  type EndpointPatch,
  type EndpointSlot,
  type ModelKind,
  type ModelOption,
  type ModelStatus,
  type OptionalFeature,
  OPTIONAL_FEATURES,
  deleteModel,
  getModelStatus,
  saveEndpoint,
  selectModel,
  setFeatures,
  startModelDownload,
  testEndpoint,
} from "@/lib/models";
import { Switch } from "@/components/ui/switch";
import { useSessionStore } from "@/store/sessionStore";

export function ModelsPanel() {
  const t = useT();
  const [status, setStatus] = useState<ModelStatus | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState<{ fraction: number; eta: number | null } | null>(null);
  const [nextSession, setNextSession] = useState(false);
  /** Which slot's endpoint form is expanded. Held here rather than in the card
   *  because the card is remounted whenever the engine's answer changes (see
   *  its `key` below), and a form that collapsed itself on save would be a
   *  strange thing to watch happen. */
  const [openEndpoint, setOpenEndpoint] = useState<string | null>(null);

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

  const setFeatureState = useSessionStore((s) => s.setFeatureState);

  /** Turn a feature on or off from here, rather than only at first run. What
   *  it needs is then in `missing` below, so the download offer covers it. */
  const toggleFeature = useCallback(
    async (feature: OptionalFeature, on: boolean) => {
      setBusy(true);
      try {
        setFeatureState(await setFeatures({ [feature]: on }));
        await refresh();
      } catch (e) {
        toast.error(e instanceof Error ? e.message : t("models.selectFailed"));
      } finally {
        setBusy(false);
      }
    },
    [refresh, setFeatureState, t],
  );

  /** Remove a downloaded model's files. Deleting the one a slot is *using*
   *  is allowed — it is the honest way to reclaim the space, and the slot
   *  says what happened — but it is asked about first, because the next
   *  session would otherwise fail on a file that vanished. */
  const remove = useCallback(
    async (option: ModelOption) => {
      if (option.selected) {
        const ok = await confirmDialog({
          title: t("models.deleteInUseTitle", { name: option.name }),
          description: t("models.deleteInUseBody"),
          confirmLabel: t("models.deleteConfirm"),
          destructive: true,
        });
        if (!ok) return;
      }
      setBusy(true);
      try {
        const res = await deleteModel(option.id);
        if (res.failed.length > 0) {
          toast.error(t("models.deleteFailed", { error: res.failed[0].error }));
        } else {
          toast.success(t("models.deleted", { name: option.name, mb: res.freed_mb }));
        }
        await refresh();
      } catch (e) {
        toast.error(e instanceof Error ? e.message : String(e));
      } finally {
        setBusy(false);
      }
    },
    [refresh, t],
  );

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

  const setRemoteSlots = useSessionStore((s) => s.setRemoteSlots);

  /** Save one slot's HTTP endpoint. The privacy line on the recording screen
   *  reads the store, so it has to learn about this without a reload. */
  const storeEndpoint = useCallback(
    async (kind: EndpointSlot, patch: EndpointPatch) => {
      setBusy(true);
      setError("");
      try {
        const res = await saveEndpoint(kind, patch);
        setRemoteSlots(res.remote);
        if (res.applies === "next_session") setNextSession(true);
        await refresh();
      } catch (e) {
        toast.error(e instanceof Error ? e.message : t("models.selectFailed"));
      } finally {
        setBusy(false);
      }
    },
    [refresh, setRemoteSlots, t],
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

  // A feature that is off has no model to download, so its slot shows the
  // switch and nothing else — the model choice is only a question once the
  // answer to "do you want this at all" is yes.
  const featureFor = (kind: ModelKind): OptionalFeature | null =>
    (OPTIONAL_FEATURES as string[]).includes(kind) ? (kind as OptionalFeature) : null;

  return (
    <div className="space-y-5">
      {status.options.map((kind) => {
        const feature = featureFor(kind.kind);
        const on = feature === null || status.features[feature];
        // Absent for a slot that cannot be pointed at a URL (speech
        // recognition); the engine decides which those are.
        const endpoint = status.endpoints[kind.kind];
        return (
          <section key={kind.kind} className="space-y-2">
            <div className="flex items-baseline justify-between gap-2">
              <h3 className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                {t(`models.kind.${kind.kind}`)}
              </h3>
              {feature === null ? (
                <span className="text-[11px] text-muted-foreground/70">{kindReason(t, kind)}</span>
              ) : (
                <label className="flex shrink-0 items-center gap-2 text-[11px] text-muted-foreground/70">
                  {on ? kindReason(t, kind) : t("models.featureOff")}
                  <Switch
                    checked={on}
                    disabled={busy}
                    onCheckedChange={(v) => void toggleFeature(feature, v)}
                    aria-label={t(`setup.feature.${feature}`)}
                  />
                </label>
              )}
            </div>
            {on && (
              <>
                <ModelPicker
                  kind={kind}
                  busy={busy}
                  onPick={(id) => void choose(kind.kind, id)}
                  onDelete={(o) => void remove(o)}
                />
                {/* The other way to answer this slot. Same section as the
                    downloadable models, because it is the same question. */}
                {endpoint && (
                  <EndpointCard
                    // Keyed on what the engine says, so the fields reset by
                    // remounting instead of being written back from an effect
                    // — the shape that goes stale (see ARCHITECTURE.md on the
                    // eslint clean-up).
                    key={`${kind.kind}:${endpoint.base_url}:${endpoint.model}:${endpoint.api_key_env}:${String(endpoint.has_api_key)}`}
                    slot={kind.kind as EndpointSlot}
                    status={endpoint}
                    busy={busy}
                    open={openEndpoint === kind.kind}
                    onOpenChange={(v) => setOpenEndpoint(v ? kind.kind : null)}
                    onSave={(patch) => storeEndpoint(kind.kind as EndpointSlot, patch)}
                    onTest={() => testEndpoint(kind.kind as EndpointSlot)}
                  />
                )}
              </>
            )}
          </section>
        );
      })}

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
