// Tells the user, once per launch, that a newer Wrenote exists.
//
// Renders nothing. It asks the engine on mount — the engine caches the answer
// and stays silent while the automatic check is off — and raises one toast
// with a Download action. Settings → General shows the same facts for anyone
// who dismissed it, so this never nags twice.
import { useEffect } from "react";
import { toast } from "sonner";

import { useT } from "@/i18n";
import { downloadTarget, getUpdateStatus, openExternal } from "@/lib/update";

const TOAST_ID = "wrenote-update";

export function UpdateNotice() {
  const t = useT();
  useEffect(() => {
    let cancelled = false;
    getUpdateStatus()
      .then((status) => {
        if (cancelled || !status.available || !status.latest) return;
        const target = downloadTarget(status);
        // One id: a re-run (language change, StrictMode) updates the toast
        // instead of stacking another.
        toast(t("update.available", { version: status.latest }), {
          id: TOAST_ID,
          description: t("update.toastHint"),
          duration: Infinity,
          action: target
            ? { label: t("update.download"), onClick: () => openExternal(target) }
            : undefined,
          cancel: { label: t("common.dismiss"), onClick: () => toast.dismiss(TOAST_ID) },
        });
      })
      .catch(() => {
        /* the About panel says why; a failed check is not launch news */
      });
    return () => {
      cancelled = true;
    };
  }, [t]);
  return null;
}
