// Settings → Developer: the states the app normally shows once and then
// never again.
//
// Two of them cost real time to reach by hand, and this is what the panel is
// for. The first-run flow only appears when a model is missing, so looking at
// it meant deleting files from ~/.wrenote/models in a terminal — now it is a
// button, and so is the delete. Everything else here is read-only: where this
// process keeps its files, and the config it actually loaded, which is the
// first question any bug report needs answered.
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { RotateCcw, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { useT } from "@/i18n";
import { setDevMode } from "@/lib/devMode";
import { getAppInfo, type AppInfo } from "@/lib/info";
import {
  type ModelStatus,
  deleteModel,
  getModelStatus,
} from "@/lib/models";
import { useSessionStore } from "@/store/sessionStore";

export function DevPanel() {
  const t = useT();
  const openSetup = useSessionStore((s) => s.openSetup);
  const [status, setStatus] = useState<ModelStatus | null>(null);
  const [info, setInfo] = useState<AppInfo | null>(null);
  const [busy, setBusy] = useState("");
  // Bumped after a delete to re-read both. A counter rather than an async
  // refresh() the effect calls: the effect must set no state synchronously.
  const [reload, setReload] = useState(0);

  useEffect(() => {
    let alive = true;
    Promise.all([getModelStatus(), getAppInfo().catch(() => null)])
      .then(([st, nfo]) => {
        if (!alive) return;
        setStatus(st);
        setInfo(nfo);
      })
      .catch(() => {
        /* the panel degrades to its buttons; nothing here is load-bearing */
      });
    return () => {
      alive = false;
    };
  }, [reload]);

  const remove = useCallback(
    async (id: string) => {
      setBusy(id);
      try {
        const res = await deleteModel(id);
        if (res.failed.length > 0) {
          toast.error(t("dev.deleteFailed", { error: res.failed[0].error }));
        } else {
          toast.success(t("dev.deleted", { mb: res.freed_mb }));
        }
        setReload((n) => n + 1);
      } catch (e) {
        toast.error(e instanceof Error ? e.message : String(e));
      } finally {
        setBusy("");
      }
    },
    [t],
  );

  // Every model the catalogue offers, with the ones on disk deletable. Flat
  // rather than grouped by slot: the same file can back two slots, and what
  // matters here is which bytes exist.
  const onDisk = (status?.options ?? [])
    .flatMap((k) => k.options)
    .filter((o) => o.installed)
    .filter((o, i, all) => all.findIndex((x) => x.id === o.id) === i);

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-3">
        <div className="space-y-0.5">
          <Label className="text-xs text-foreground">{t("dev.setupTitle")}</Label>
          <p className="text-[11px] leading-snug text-muted-foreground">
            {t("dev.setupHint")}
          </p>
        </div>
        <Button size="sm" variant="outline" onClick={() => openSetup()}>
          <RotateCcw data-icon="inline-start" />
          {t("dev.setupRun")}
        </Button>
      </div>

      <section className="space-y-2">
        <Label className="text-xs text-foreground">{t("dev.modelsTitle")}</Label>
        <p className="text-[11px] leading-snug text-muted-foreground">
          {t("dev.modelsHint")}
        </p>
        {onDisk.length === 0 ? (
          <p className="rounded-lg border border-dashed border-border/60 px-3 py-5 text-center text-[12px] text-muted-foreground">
            {t("dev.modelsNone")}
          </p>
        ) : (
          <ul className="space-y-1.5">
            {onDisk.map((o) => (
              <li
                key={o.id}
                className="flex items-center gap-2 rounded-lg border border-border/50 bg-background/40 px-3 py-2"
              >
                <span className="min-w-0 flex-1 truncate text-[13px] text-foreground">
                  {o.name}
                </span>
                <span className="shrink-0 tabular-nums text-[11px] text-muted-foreground">
                  {o.size_mb} MB
                </span>
                <Button
                  size="icon-sm"
                  variant="destructive"
                  disabled={busy !== ""}
                  onClick={() => void remove(o.id)}
                  data-tip={t("dev.delete")}
                  aria-label={t("dev.delete")}
                >
                  <Trash2 className="size-3.5" />
                </Button>
              </li>
            ))}
          </ul>
        )}
      </section>

      {info && (
        <section className="space-y-2">
          <Label className="text-xs text-foreground">{t("dev.pathsTitle")}</Label>
          <dl className="space-y-1 rounded-lg border border-border/50 bg-background/40 px-3 py-2">
            {Object.entries(info.paths).map(([key, value]) => (
              <div key={key} className="flex items-baseline gap-2 text-[11px]">
                <dt className="shrink-0 text-muted-foreground">{key}</dt>
                <dd className="min-w-0 flex-1 truncate font-mono text-foreground" title={value}>
                  {value}
                </dd>
              </div>
            ))}
          </dl>
        </section>
      )}

      {info && (
        <section className="space-y-2">
          <Label className="text-xs text-foreground">{t("dev.configTitle")}</Label>
          <p className="text-[11px] leading-snug text-muted-foreground">
            {t("dev.configHint")}
          </p>
          <pre className="max-h-64 overflow-auto rounded-lg border border-border/50 bg-background/40 p-3 text-[10px] leading-relaxed text-muted-foreground">
            {JSON.stringify(info.config, null, 2)}
          </pre>
        </section>
      )}

      <div className="flex items-start justify-between gap-3 border-t border-border/60 pt-4">
        <div className="space-y-0.5">
          <Label className="text-xs text-foreground">{t("dev.offTitle")}</Label>
          <p className="text-[11px] leading-snug text-muted-foreground">{t("dev.offHint")}</p>
        </div>
        <Button
          size="sm"
          variant="ghost"
          onClick={() => {
            setDevMode(false);
            toast(t("dev.disabled"));
          }}
        >
          {t("dev.off")}
        </Button>
      </div>
    </div>
  );
}
