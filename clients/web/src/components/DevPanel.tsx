// Settings → Developer: the states the app normally shows once and then
// never again.
//
// The first-run flow only appears when a model is missing, so looking at it
// meant deleting files from ~/.wrenote/models in a terminal and restarting.
// Deleting a model is a normal thing to want (Settings → Models has it, next
// to the model it removes); re-entering the flow is not, and that is what
// this panel is for. The rest is read-only: where this process keeps its
// files, and the config it actually loaded, which is the first question any
// bug report needs answered.
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { RotateCcw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { useT } from "@/i18n";
import { setDevMode } from "@/lib/devMode";
import { getAppInfo, type AppInfo } from "@/lib/info";
import { useSessionStore } from "@/store/sessionStore";

export function DevPanel() {
  const t = useT();
  const openSetup = useSessionStore((s) => s.openSetup);
  const [info, setInfo] = useState<AppInfo | null>(null);

  useEffect(() => {
    let alive = true;
    getAppInfo()
      .then((nfo) => {
        if (alive) setInfo(nfo);
      })
      .catch(() => {
        /* the panel degrades to its button; nothing here is load-bearing */
      });
    return () => {
      alive = false;
    };
  }, []);

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
