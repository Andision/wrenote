// Settings → Compute: which accelerator build the engine runs inference on.
//
// Shows the detected hardware, the runtime the engine picked (and why), and
// the runtime packs that can be installed for this machine. Installing is a
// background job on the engine (progress over the shared jobs SSE); choosing
// an accelerator is persisted to the user config and applies on next launch —
// native bindings can't be swapped inside a running process.
import { useCallback, useEffect, useState } from "react";
import { Download, RefreshCw, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  type Accelerator,
  type ComputeStatus,
  type PackInfo,
  type Variant,
  VARIANT_LABEL,
  formatMb,
  getComputeStatus,
  installRuntime,
  removeRuntime,
  selectAccelerator,
} from "@/lib/compute";
import { formatEta, subscribeJob } from "@/lib/jobs";

interface InstallProgress {
  fraction: number;
  eta: number | null;
  label: string;
}

export function ComputePanel() {
  const [status, setStatus] = useState<ComputeStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [installing, setInstalling] = useState<Record<string, InstallProgress>>({});
  const [restartNeeded, setRestartNeeded] = useState(false);

  const refresh = useCallback(async () => {
    try {
      setStatus(await getComputeStatus());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "could not load compute status");
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    getComputeStatus()
      .then((s) => {
        if (cancelled) return;
        setStatus(s);
        setError(null);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e.message : "could not load compute status");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const install = async (variant: Variant) => {
    try {
      const res = await installRuntime(variant);
      if (!res.job_id) {
        void refresh();
        return;
      }
      setInstalling((s) => ({ ...s, [variant]: { fraction: 0, eta: null, label: "starting…" } }));
      subscribeJob(res.job_id, {
        onSnapshot: (snap) => {
          setInstalling((s) => ({
            ...s,
            [variant]: {
              fraction: snap.fraction,
              eta: snap.eta_s,
              label: snap.log[snap.log.length - 1] ?? snap.phase,
            },
          }));
          if (snap.status !== "running") {
            setInstalling((s) => {
              const next = { ...s };
              delete next[variant];
              return next;
            });
            if (snap.status === "done") {
              toast.success(`${VARIANT_LABEL[variant]} runtime installed`);
              setRestartNeeded(true);
            } else {
              toast.error(snap.error ?? "runtime install failed");
            }
            void refresh();
          }
        },
        onError: () => {
          setInstalling((s) => {
            const next = { ...s };
            delete next[variant];
            return next;
          });
          toast.error("lost the install progress stream");
          void refresh();
        },
      });
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "runtime install failed");
    }
  };

  const remove = async (variant: Variant) => {
    try {
      const res = await removeRuntime(variant);
      if (res.removed) {
        toast.success(`${VARIANT_LABEL[variant]} runtime removed`);
        setRestartNeeded(true);
      }
      void refresh();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "runtime remove failed");
    }
  };

  const choose = async (acc: Accelerator) => {
    try {
      await selectAccelerator(acc);
      setRestartNeeded(true);
      void refresh();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "could not save the accelerator");
    }
  };

  if (error) {
    return (
      <p className="rounded-lg border border-dashed border-border/60 px-3 py-8 text-center text-[12px] text-muted-foreground">
        {error}
      </p>
    );
  }
  if (!status) {
    return <p className="px-1 py-6 text-[12px] text-muted-foreground">Detecting hardware…</p>;
  }

  const hw = status.hardware;
  const activeLabel = status.active ? VARIANT_LABEL[status.active] : "—";
  // Offer the platform's ranked accelerators plus anything installed/built in.
  const offered = Array.from(
    new Set<string>([
      ...hw.accelerators,
      ...status.packs.filter((p) => p.installed).map((p) => p.variant),
      status.builtin,
    ]),
  ) as Variant[];
  const chosen = (status.config.accelerator || "auto") as Accelerator;

  return (
    <div className="space-y-5">
      {/* Hardware */}
      <section className="space-y-2">
        <h3 className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
          Hardware
        </h3>
        <div className="space-y-1 rounded-lg border border-border/60 bg-muted/30 p-3 text-[12px]">
          <Row label="Platform" value={`${status.platform_tag} · ${hw.cpu_count} CPU threads · ${formatMb(hw.ram_mb)} RAM`} />
          {hw.gpus.length === 0 ? (
            <Row label="GPU" value="none detected" />
          ) : (
            hw.gpus.map((g, i) => (
              <Row
                key={i}
                label={i === 0 ? "GPU" : ""}
                value={`${g.name}${g.vram_mb ? ` · ${formatMb(g.vram_mb)}${g.unified_memory ? " shared" : " VRAM"}` : g.unified_memory ? " · unified memory" : ""}`}
              />
            ))
          )}
          {hw.npu && <Row label="NPU" value={`${hw.npu} (detected; not used for inference yet)`} />}
        </div>
      </section>

      {/* Active runtime */}
      <section className="space-y-2">
        <div className="flex items-center justify-between">
          <h3 className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
            Runtime
          </h3>
          <Button variant="ghost" size="icon" className="size-7" onClick={() => void refresh()} data-tip="Refresh">
            <RefreshCw className="size-3.5" />
          </Button>
        </div>
        <div className="space-y-1 rounded-lg border border-border/60 bg-muted/30 p-3 text-[12px]">
          <Row label="Active" value={`${activeLabel} (${status.selection.reason})`} />
          <Row label="Preference" value={status.selection.chain.map((v) => VARIANT_LABEL[v as Variant] ?? v).join(" → ")} />
          {status.vram_budget_mb != null && (
            <Row label="VRAM budget" value={formatMb(status.vram_budget_mb)} />
          )}
          {Object.keys(status.bad).length > 0 && (
            <Row
              label="Skipped"
              value={Object.entries(status.bad)
                .map(([v, why]) => `${VARIANT_LABEL[v as Variant] ?? v}: ${why}`)
                .join("; ")}
            />
          )}
        </div>
      </section>

      {/* Accelerator choice */}
      <section className="space-y-2">
        <h3 className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
          Accelerator
        </h3>
        <div className="flex flex-wrap gap-1.5">
          {(["auto", ...offered] as Accelerator[]).map((acc) => (
            <button
              key={acc}
              type="button"
              onClick={() => void choose(acc)}
              className={`rounded-md border px-2.5 py-1 text-[12px] transition-colors ${
                chosen === acc
                  ? "border-primary bg-primary/10 text-foreground"
                  : "border-border/60 text-muted-foreground hover:bg-muted"
              }`}
            >
              {acc === "auto" ? "Auto" : VARIANT_LABEL[acc as Variant]}
            </button>
          ))}
        </div>
        <p className="text-[11px] text-muted-foreground">
          Auto ranks by detected hardware and falls back to the built-in runtime. A pinned
          accelerator must be installed below.
        </p>
      </section>

      {/* Packs */}
      <section className="space-y-2">
        <h3 className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
          Runtime packs
        </h3>
        <div className="divide-y divide-border/60 rounded-lg border border-border/60 bg-muted/30">
          {status.packs
            .filter((p) => offered.includes(p.variant) || p.installed || p.available)
            .map((p) => (
              <PackRow
                key={p.variant}
                pack={p}
                active={status.active === p.variant}
                progress={installing[p.variant]}
                onInstall={() => void install(p.variant)}
                onRemove={() => void remove(p.variant)}
              />
            ))}
        </div>
        {status.index.checked && status.index.reachable === false && (
          <p className="text-[11px] text-muted-foreground">
            Pack index unreachable — installs are unavailable offline.
          </p>
        )}
      </section>

      {restartNeeded && (
        <p className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-700 dark:border-amber-900/50 dark:bg-amber-950/30 dark:text-amber-300">
          Restart Wrenote to apply the new compute runtime.
        </p>
      )}
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex gap-3">
      <span className="w-24 shrink-0 text-muted-foreground">{label}</span>
      <span className="min-w-0 break-words">{value}</span>
    </div>
  );
}

function PackRow({
  pack,
  active,
  progress,
  onInstall,
  onRemove,
}: {
  pack: PackInfo;
  active: boolean;
  progress?: InstallProgress;
  onInstall: () => void;
  onRemove: () => void;
}) {
  const state = pack.builtin
    ? "Built in"
    : pack.installed
      ? `Installed${pack.version ? ` · ${pack.version}` : ""}`
      : pack.available
        ? `Available${pack.release?.size ? ` · ${formatMb(Math.round(pack.release.size / 1048576))}` : ""}`
        : "Not published for this machine";
  return (
    <div className="flex items-center gap-3 px-3 py-2 text-[12px]">
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="font-medium">{VARIANT_LABEL[pack.variant]}</span>
          {active && (
            <span className="rounded-full bg-emerald-500/15 px-1.5 py-0.5 text-[10px] font-medium text-emerald-600 dark:text-emerald-400">
              active
            </span>
          )}
        </div>
        {progress ? (
          <div className="mt-1">
            <div className="h-1 w-full overflow-hidden rounded-full bg-muted">
              <div
                className="h-full rounded-full bg-primary transition-[width]"
                style={{ width: `${Math.round(progress.fraction * 100)}%` }}
              />
            </div>
            <div className="mt-0.5 text-[11px] text-muted-foreground">
              {progress.label} · {Math.round(progress.fraction * 100)}% · {formatEta(progress.eta)}
            </div>
          </div>
        ) : (
          <div className="text-[11px] text-muted-foreground">{state}</div>
        )}
      </div>
      {!pack.builtin && !progress && (
        pack.installed ? (
          <Button variant="ghost" size="icon" className="size-7" onClick={onRemove} data-tip="Remove pack">
            <Trash2 className="size-3.5" />
          </Button>
        ) : (
          <Button
            variant="ghost"
            size="icon"
            className="size-7"
            onClick={onInstall}
            disabled={!pack.available}
            data-tip={pack.available ? "Install pack" : "Not available"}
          >
            <Download className="size-3.5" />
          </Button>
        )
      )}
    </div>
  );
}
