// Settings → General: which Wrenote this is, whether a newer one exists, and
// the switch for asking automatically.
//
// The facts come from the engine (lib/update.ts); this renders them. The one
// judgement here is what to say when there is nothing to report — "checked at
// … , up to date" versus "automatic checks are off" — because a blank line
// reads as "didn't work".
import { useEffect, useRef, useState } from "react";
import { ExternalLink, RefreshCw } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { useI18n, useT } from "@/i18n";
import {
  TAPS_BEFORE_HINT,
  TAPS_REQUIRED,
  setDevMode,
  useDevMode,
} from "@/lib/devMode";
import {
  checkForUpdate,
  downloadTarget,
  getUpdateStatus,
  openExternal,
  setUpdateCheck,
  type UpdateStatus,
} from "@/lib/update";

export function UpdatePanel() {
  const devMode = useDevMode();
  // Not state: a half-finished tap run should not re-render anything.
  const tapsRef = useRef(0);
  const t = useT();
  const { locale } = useI18n();
  const [status, setStatus] = useState<UpdateStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getUpdateStatus()
      .then((s) => {
        if (!cancelled) setStatus(s);
      })
      .catch((e: unknown) => {
        if (!cancelled) setFailed(e instanceof Error ? e.message : String(e));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const runCheck = async () => {
    setBusy(true);
    setFailed(null);
    try {
      setStatus(await checkForUpdate());
    } catch (e) {
      setFailed(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const toggleAuto = async (enabled: boolean) => {
    setStatus((s) => (s ? { ...s, enabled } : s));
    try {
      await setUpdateCheck(enabled);
    } catch (e) {
      setStatus((s) => (s ? { ...s, enabled: !enabled } : s));
      setFailed(e instanceof Error ? e.message : String(e));
    }
  };

  const target = status ? downloadTarget(status) : null;

  const countTap = () => {
    if (devMode) return;
    const taps = tapsRef.current + 1;
    tapsRef.current = taps;
    if (taps >= TAPS_REQUIRED) {
      tapsRef.current = 0;
      setDevMode(true);
      toast.success(t("dev.enabled"));
      return;
    }
    const left = TAPS_REQUIRED - taps;
    if (left <= TAPS_BEFORE_HINT) toast(t("dev.tapsLeft", { count: left }));
  };

  const showWhatsNew =
    status?.available && status.release_url && status.download_url && status.release_url !== status.download_url;

  return (
    <div className="space-y-5">
      <div className="flex items-start justify-between gap-3">
        <div className="space-y-0.5">
          {/* Five taps on the version turns developer mode on — see
              lib/devMode.ts for why it is here and not a setting. */}
          <Label
            className="cursor-default select-none text-xs text-foreground"
            onClick={countTap}
          >
            {t("update.version", { version: status?.current ?? "…" })}
          </Label>
          <p className="text-[11px] leading-snug text-muted-foreground">
            {failed ?? (status ? describe(t, locale, status) : "")}
          </p>
          {status?.available && target && (
            <div className="flex flex-wrap items-center gap-2 pt-1.5">
              <Button size="sm" onClick={() => openExternal(target)}>
                <ExternalLink data-icon="inline-start" />
                {t("update.download")}
              </Button>
              {showWhatsNew && status.release_url && (
                <Button size="sm" variant="ghost" onClick={() => openExternal(status.release_url!)}>
                  {t("update.whatsNew")}
                </Button>
              )}
            </div>
          )}
        </div>
        <Button size="sm" variant="outline" disabled={busy} onClick={() => void runCheck()}>
          <RefreshCw data-icon="inline-start" className={busy ? "animate-spin" : undefined} />
          {busy ? t("update.checking") : t("update.checkNow")}
        </Button>
      </div>

      <div className="flex items-start justify-between gap-3">
        <div className="space-y-0.5">
          <Label className="text-xs text-foreground">{t("update.auto")}</Label>
          <p className="text-[11px] leading-snug text-muted-foreground">{t("update.autoHint")}</p>
        </div>
        <Switch
          checked={status?.enabled ?? true}
          disabled={!status}
          onCheckedChange={(v) => void toggleAuto(v)}
        />
      </div>
    </div>
  );
}

type T = ReturnType<typeof useT>;

/** One line under the version: what the last check found, or why there was none. */
function describe(t: T, locale: string, s: UpdateStatus): string {
  if (s.available && s.latest) return t("update.available", { version: s.latest });
  if (s.error) return t(`update.error.${s.error}`);
  if (!s.checked_at) return s.enabled ? "" : t("update.off");
  const when = new Intl.DateTimeFormat(locale, { dateStyle: "medium", timeStyle: "short" }).format(
    new Date(s.checked_at),
  );
  return `${t("update.upToDate")} ${t("update.checkedAt", { when })}`;
}
