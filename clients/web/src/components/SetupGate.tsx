// First-run setup. Three things happen before Wrenote can transcribe:
// choose which features you want, pick the compute runtime, and download the
// models they imply. All three feed one download, so they belong in one flow
// rather than a gate plus a settings page the user never visits — on Windows,
// skipping the runtime step silently means CPU inference forever.
//
// Features first: they decide what there is to download (the full set is
// 4.3 GB, and the chat model alone is 2.5 GB), and on a machine with no
// accelerator they decide whether the runtime question is worth asking.
// Runtime next, deliberately: while no native backend has been imported the
// engine can swap runtimes in place (see RuntimeManager.can_reactivate), so
// choosing here costs no restart. Loading a model is what pins the process.
//
// Returning users (models already present) never see this — unless they ask
// for it back: `openSetup(feature)` re-enters the flow from the dialog a
// switched-off feature raises, and from the developer menu.
import { useCallback, useEffect, useState } from "react";
import { motion } from "motion/react";
import {
  Check,
  Cpu,
  Download,
  ListChecks,
  Loader2,
  ShieldCheck,
  Sparkles,
  TriangleAlert,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { formatEta, subscribeJob } from "@/lib/jobs";
import { hardwareText, optionText } from "@/lib/computeText";
import { useT } from "@/i18n";
import {
  ALL_FEATURES_ON,
  type Features,
  type KindOptions,
  type ModelKind,
  type ModelStatusItem,
  OPTIONAL_FEATURES,
  getModelStatus,
  selectModel,
  setFeatures as setRemoteFeatures,
  startModelDownload,
} from "@/lib/models";
import { ModelPicker } from "@/components/ModelPicker";
import { Switch } from "@/components/ui/switch";
import { useSessionStore } from "@/store/sessionStore";
import { kindReason } from "@/lib/modelText";
import {
  type ComputeStatus,
  type RuntimeOption,
  type Variant,
  VARIANT_LABEL,
  getComputeStatus,
  installRuntime,
  selectAccelerator,
} from "@/lib/compute";

type Step = "checking" | "features" | "compute" | "models";

function gb(bytes: number): string {
  return `${(bytes / 1e9).toFixed(1)} GB`;
}

interface Progress {
  fraction: number;
  eta: number | null;
  label: string;
}

export function SetupGate() {
  const t = useT();
  const [step, setStep] = useState<Step>("checking");
  const [done, setDone] = useState(false);
  // Set when someone asks for this flow back (a switched-off feature's
  // dialog, or the developer menu); cleared when the flow finishes.
  const setupRequest = useSessionStore((s) => s.setupRequest);
  const closeSetup = useSessionStore((s) => s.closeSetup);
  const setFeatureState = useSessionStore((s) => s.setFeatureState);
  const setRemoteSlots = useSessionStore((s) => s.setRemoteSlots);

  // The feature switches, edited locally and sent on Continue: a half-made
  // choice should not change what the rest of the app offers.
  const [features, setFeatures] = useState<Features>(ALL_FEATURES_ON);
  const [featuresBusy, setFeaturesBusy] = useState(false);

  const [compute, setCompute] = useState<ComputeStatus | null>(null);
  // Whether the runtime step is part of this run — it is skipped where there
  // is no accelerator worth installing.
  const [withCompute, setWithCompute] = useState(false);
  const [chosen, setChosen] = useState<Variant | null>(null);
  const [runtimeBusy, setRuntimeBusy] = useState(false);

  const [models, setModels] = useState<ModelStatusItem[]>([]);
  // Model choices for this machine, from the same status call. Picking one
  // changes which files are needed, so the list above is refetched after.
  const [modelOptions, setModelOptions] = useState<KindOptions[]>([]);
  const [pickingModel, setPickingModel] = useState(false);
  const [progress, setProgress] = useState<Progress | null>(null);
  const [error, setError] = useState("");
  const [restartNeeded, setRestartNeeded] = useState(false);

  // Everything present and nobody asking for the flow back → never shown.
  const requested = setupRequest !== null;
  const focus = setupRequest?.focus ?? null;

  /** Leave the flow: hide it, and clear a request so it stays hidden. */
  const finish = useCallback(() => {
    setDone(true);
    closeSetup();
  }, [closeSetup]);

  useEffect(() => {
    let alive = true;
    // The models decide whether this is a first run; the compute status only
    // decides whether the runtime step is worth showing, so it must not block.
    void (async () => {
      try {
        const st = await getModelStatus();
        if (!alive) return;
        setFeatureState(st.features);
        setRemoteSlots(st.remote);
        if (st.all_present && !requested) {
          setDone(true);
          return;
        }
        setDone(false);
        // Re-entered to turn one feature on: it starts switched on, so the
        // user lands on the screen with the thing they asked for already
        // chosen and only has to confirm.
        setFeatures(focus ? { ...st.features, [focus]: true } : st.features);
        setModels(st.models);
        setModelOptions(st.options ?? []);
        let comp: ComputeStatus | null = null;
        try {
          comp = await getComputeStatus();
        } catch {
          /* offline or probing failed — go straight to the models */
        }
        if (!alive) return;
        setCompute(comp);
        const offered = comp?.options.filter((o) => o.usable) ?? [];
        setChosen(offered.find((o) => o.recommended)?.variant ?? null);
        // Only ask when there is a real choice: an accelerator this machine can
        // use, not already installed, and actually published. On a Mac (Metal
        // is built in) or offline, that's nothing — skip the step.
        setWithCompute(
          offered.some((o) => o.accelerated && !o.installed && o.download_mb != null),
        );
        setStep("features");
      } catch (e) {
        if (alive) {
          setError(e instanceof Error ? e.message : String(e));
          setStep("models");
        }
      }
    })();
    return () => {
      alive = false;
    };
  }, [requested, focus, setFeatureState, setRemoteSlots]);

  /** Send the feature switches, then re-read what that leaves to download. */
  const confirmFeatures = useCallback(async () => {
    setFeaturesBusy(true);
    setError("");
    try {
      setFeatureState(await setRemoteFeatures(features));
      const st = await getModelStatus();
      setModels(st.models);
      setModelOptions(st.options ?? []);
      // Everything the chosen features need is already here — a re-entry that
      // only switched something back on, with the files still on disk.
      if (st.all_present) {
        finish();
        return;
      }
      setStep(withCompute ? "compute" : "models");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setFeaturesBusy(false);
    }
  }, [features, finish, setFeatureState, withCompute]);

  /** Apply the runtime choice (installing its pack first when needed). */
  const confirmRuntime = useCallback(async () => {
    if (!chosen) {
      setStep("models");
      return;
    }
    const option = compute?.options.find((o) => o.variant === chosen);
    setError("");
    setRuntimeBusy(true);
    try {
      if (option && !option.installed) {
        const res = await installRuntime(chosen);
        if (res.job_id) {
          setProgress({ fraction: 0, eta: null, label: t("progress.starting") });
          await new Promise<void>((resolve, reject) => {
            subscribeJob(res.job_id!, {
              onSnapshot: (snap) => {
                setProgress({
                  fraction: snap.fraction,
                  eta: snap.eta_s,
                  label: snap.log[snap.log.length - 1] ?? snap.phase,
                });
                if (snap.status === "done") resolve();
                if (snap.status === "error") reject(new Error(snap.error ?? t("compute.installFailed")));
              },
              onError: () => reject(new Error(t("compute.streamLost"))),
            });
          });
        }
      }
      // Pin the choice: "auto" ranks by raw capability and would prefer CUDA on
      // an NVIDIA machine, which is not what this screen recommended.
      const applied = await selectAccelerator(chosen);
      setRestartNeeded(applied.restart_required);
      setProgress(null);
      setStep("models");
    } catch (e) {
      setProgress(null);
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setRuntimeBusy(false);
    }
  }, [chosen, compute, t]);

  const chooseModel = useCallback(
    async (kind: ModelKind, id: string) => {
      setPickingModel(true);
      setError("");
      try {
        await selectModel(kind, id);
        const st = await getModelStatus();
        setModels(st.models);
        setModelOptions(st.options ?? []);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setPickingModel(false);
      }
    },
    [],
  );

  const beginModelDownload = useCallback(() => {
    setError("");
    setProgress({ fraction: 0, eta: null, label: t("progress.starting") });
    startModelDownload()
      .then((res) => {
        if (res.all_present || !res.job_id) {
          finish();
          return;
        }
        subscribeJob(res.job_id, {
          onSnapshot: (snap) => {
            setProgress({
              fraction: snap.fraction,
              eta: snap.eta_s,
              label: snap.log[snap.log.length - 1] ?? snap.phase,
            });
            if (snap.status === "done") finish();
            if (snap.status === "error") {
              setProgress(null);
              setError(snap.error ?? t("setup.downloadFailed"));
            }
          },
          onError: () => {
            setProgress(null);
            setError(t("compute.streamLost"));
          },
        });
      })
      .catch((e: unknown) => {
        setProgress(null);
        setError(e instanceof Error ? e.message : String(e));
      });
  }, [finish, t]);

  if ((done && !requested) || step === "checking") return null;

  const totalModelSize = models.reduce((a, m) => a + m.size, 0);
  const onFeatures = step === "features";
  const onCompute = step === "compute";
  const steps: Step[] = withCompute
    ? ["features", "compute", "models"]
    : ["features", "models"];
  const stepNo = steps.indexOf(step) + 1;

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      className="fixed inset-0 z-50 flex items-center justify-center bg-background/95 p-6 backdrop-blur-md"
    >
      <motion.div
        initial={{ opacity: 0, y: 10, scale: 0.98 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        transition={{ duration: 0.28, ease: [0.22, 0.61, 0.36, 1] }}
        className="flex max-h-[calc(100vh-3rem)] w-full max-w-md flex-col rounded-3xl border border-border/60 bg-card/80 p-8 shadow-xl"
      >
        {/* Everything above the button scrolls when the model list is long;
            the button itself never leaves the screen. */}
        <div className="-mx-2 min-h-0 flex-1 overflow-y-auto px-2">
        <div className="flex items-start justify-between">
          <div className="flex size-14 items-center justify-center rounded-2xl bg-brand-500/15 ring-1 ring-inset ring-brand-500/25">
            {onFeatures ? (
              <ListChecks className="size-7 text-brand-600 dark:text-brand-400" />
            ) : onCompute ? (
              <Sparkles className="size-7 text-brand-600 dark:text-brand-400" />
            ) : (
              <Download className="size-7 text-brand-600 dark:text-brand-400" />
            )}
          </div>
          <span className="mt-1 text-[11px] tabular-nums text-muted-foreground/70">
            {t("setup.step", { current: stepNo, total: steps.length })}
          </span>
        </div>

        <h1 className="mt-5 text-xl font-semibold tracking-tight text-foreground">
          {onFeatures
            ? t("setup.featuresTitle")
            : onCompute
              ? t("setup.computeTitle")
              : t("setup.modelsTitle")}
        </h1>
        <p className="mt-2 flex items-start gap-1.5 text-[13px] leading-relaxed text-muted-foreground">
          <ShieldCheck className="mt-0.5 size-4 shrink-0 text-brand-500" />
          {onFeatures
            ? t("setup.featuresBlurb")
            : onCompute
              ? t("setup.computeBlurb")
              : t("setup.modelsBlurb")}
        </p>

        {onFeatures && (
          <div className="mt-5 space-y-1.5">
            {/* Transcription is not a switch: it is what the app is. Showing
                it, fixed on, is what makes the list read as the whole set. */}
            <FeatureRow fixed label={t("setup.feature.transcribe")}
                        hint={t("setup.feature.transcribeHint")} />
            {OPTIONAL_FEATURES.map((f) => (
              <FeatureRow
                key={f}
                label={t(`setup.feature.${f}`)}
                hint={t(`setup.feature.${f}Hint`)}
                checked={features[f]}
                highlighted={f === focus}
                disabled={featuresBusy}
                onChange={(v) => setFeatures((prev) => ({ ...prev, [f]: v }))}
              />
            ))}
            <p className="pt-2 text-[11px] leading-relaxed text-muted-foreground/70">
              {t("setup.featuresFootnote")}
            </p>
          </div>
        )}

        {onCompute && compute && (
          <div className="mt-5 space-y-1.5">
            {compute.options.map((o) => (
              <OptionRow
                key={o.variant}
                option={o}
                selected={chosen === o.variant}
                disabled={runtimeBusy}
                onSelect={() => setChosen(o.variant)}
              />
            ))}
          </div>
        )}

        {/* Which models, then which files that implies. Only kinds with a real
            choice are shown — a single-entry kind is not a decision — and the
            after-recording recogniser stays on its default here: it is a
            Settings → Models choice, not a first-run one. */}
        {step === "models" &&
          modelOptions
            .filter((k) => k.options.length > 1 && k.kind !== "stt_offline")
            .map((k) => (
              <section key={k.kind} className="mt-5 space-y-1.5">
                <div className="flex items-baseline justify-between gap-2">
                  <h2 className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                    {t(`models.kind.${k.kind}`)}
                  </h2>
                  <span className="text-[11px] text-muted-foreground/70">
                    {kindReason(t, k)}
                  </span>
                </div>
                <ModelPicker kind={k} busy={pickingModel} onPick={(id) => void chooseModel(k.kind, id)} />
              </section>
            ))}

        {step === "models" && (
          <ul className="mt-5 space-y-1.5">
            {models.map((m) => (
              <li
                key={m.filename}
                className="flex items-center justify-between gap-3 rounded-lg border border-border/40 bg-background/40 px-3 py-2 text-[12px]"
              >
                <span className="truncate font-mono text-muted-foreground" title={m.filename}>
                  {m.filename}
                </span>
                <span className="flex shrink-0 items-center gap-1.5">
                  <span className="tabular-nums text-muted-foreground/70">{gb(m.size)}</span>
                  {m.present ? (
                    <Check className="size-4 text-emerald-500" />
                  ) : (
                    <Download className="size-3.5 text-muted-foreground/50" />
                  )}
                </span>
              </li>
            ))}
          </ul>
        )}

        {progress && (
          <div className="mt-6 space-y-2">
            <div className="h-2 overflow-hidden rounded-full bg-muted">
              <motion.div
                className="h-full rounded-full bg-brand-500"
                animate={{ width: `${Math.round(progress.fraction * 100)}%` }}
                transition={{ ease: "linear", duration: 0.2 }}
              />
            </div>
            <div className="flex items-center justify-between text-[11px] text-muted-foreground">
              <span className="flex items-center gap-1.5">
                <Loader2 className="size-3 animate-spin" />
                {progress.label || t("progress.starting")}
              </span>
              <span className="tabular-nums">
                {Math.round(progress.fraction * 100)}% · {formatEta(progress.eta)}
              </span>
            </div>
          </div>
        )}

        {error && (
          <div className="mt-5 flex items-start gap-2 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-[12px] text-red-600 dark:text-red-400">
            <TriangleAlert className="mt-0.5 size-4 shrink-0" />
            <span className="break-words">{error}</span>
          </div>
        )}

        </div>
        {onFeatures ? (
          <Button
            onClick={() => void confirmFeatures()}
            className="mt-6 w-full"
            size="lg"
            disabled={featuresBusy}
          >
            {featuresBusy ? t("progress.starting") : t("common.continue")}
          </Button>
        ) : onCompute ? (
          <>
            <Button
              onClick={() => void confirmRuntime()}
              className="mt-6 w-full"
              size="lg"
              disabled={runtimeBusy || !chosen}
            >
              {runtimeBusy ? t("setup.installing") : t("common.continue")}
            </Button>
            <button
              type="button"
              onClick={() => setStep("models")}
              disabled={runtimeBusy}
              className="mt-3 w-full text-center text-[11px] text-muted-foreground/70 underline-offset-2 hover:underline disabled:opacity-50"
            >
              {t("setup.skip")}
            </button>
          </>
        ) : (
          !progress && (
            <>
              <Button onClick={beginModelDownload} className="mt-6 w-full" size="lg">
                {error ? t("setup.retry") : t("setup.download", { size: gb(totalModelSize) })}
              </Button>
              <p className="mt-3 text-center text-[11px] text-muted-foreground/60">
                {t("setup.modelsHint")}
              </p>
            </>
          )
        )}

        {restartNeeded && step === "models" && (
          <p className="mt-3 text-center text-[11px] text-amber-600 dark:text-amber-400">
            {t("setup.restart")}
          </p>
        )}
      </motion.div>
    </motion.div>
  );
}

/** One feature switch. `fixed` renders transcription: shown, always on, so
 *  the list reads as everything the app does rather than only the extras. */
function FeatureRow({
  label,
  hint,
  checked = true,
  fixed = false,
  highlighted = false,
  disabled = false,
  onChange,
}: {
  label: string;
  hint: string;
  checked?: boolean;
  fixed?: boolean;
  highlighted?: boolean;
  disabled?: boolean;
  onChange?: (next: boolean) => void;
}) {
  const t = useT();
  return (
    <div
      className={`flex items-start gap-3 rounded-lg border px-3 py-2.5 transition-colors ${
        highlighted
          ? "border-brand-500 bg-brand-500/10"
          : "border-border/50 bg-background/40"
      }`}
    >
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2 text-[13px] font-medium text-foreground">
          {label}
          {fixed && (
            <span className="rounded-full bg-muted px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">
              {t("setup.feature.always")}
            </span>
          )}
        </div>
        <p className="mt-0.5 text-[11px] leading-relaxed text-muted-foreground/80">{hint}</p>
      </div>
      <Switch
        checked={checked}
        disabled={fixed || disabled}
        onCheckedChange={(v) => onChange?.(v)}
        aria-label={label}
        className="mt-0.5 shrink-0"
      />
    </div>
  );
}

function OptionRow({
  option,
  selected,
  disabled,
  onSelect,
}: {
  option: RuntimeOption;
  selected: boolean;
  disabled: boolean;
  onSelect: () => void;
}) {
  const t = useT();
  const unusable = !option.usable;
  return (
    <button
      type="button"
      onClick={onSelect}
      disabled={disabled || unusable}
      className={`w-full rounded-lg border px-3 py-2.5 text-left transition-colors ${
        selected
          ? "border-brand-500 bg-brand-500/10"
          : "border-border/50 bg-background/40 hover:bg-muted/60"
      } ${unusable ? "cursor-not-allowed opacity-50 hover:bg-background/40" : ""}`}
    >
      <div className="flex items-center gap-2">
        {option.accelerated ? (
          <Sparkles className="size-3.5 shrink-0 text-muted-foreground" />
        ) : (
          <Cpu className="size-3.5 shrink-0 text-muted-foreground" />
        )}
        <span className="text-[13px] font-medium">{VARIANT_LABEL[option.variant]}</span>
        {option.recommended && (
          <span className="rounded-full bg-brand-500/15 px-1.5 py-0.5 text-[10px] font-medium text-brand-600 dark:text-brand-400">
            {t("setup.recommended")}
          </span>
        )}
        <span className="ml-auto shrink-0 text-[11px] tabular-nums text-muted-foreground">
          {option.download_mb != null ? `${option.download_mb} MB` : ""}
        </span>
      </div>
      {/* What the engine detected, or what rules this out — its codes, our words. */}
      {option.hardware && (
        <div className="mt-0.5 pl-5.5 text-[11px] text-muted-foreground">
          {hardwareText(t, option.hardware)}
        </div>
      )}
      <div className="pl-5.5 text-[11px] text-muted-foreground/70">{optionText(t, option)}</div>
    </button>
  );
}
