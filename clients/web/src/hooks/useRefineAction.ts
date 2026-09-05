import { useCallback } from "react";

import { confirmDialog } from "@/lib/confirm";
import { RefineRefusedError, isRefineRefusal, startRefine } from "@/lib/refine";
import { useJobsStore } from "@/store/jobsStore";
import { useSessionStore } from "@/store/sessionStore";
import { useT } from "@/i18n";

/**
 * Start the whole-recording pass on the session on screen, by hand.
 *
 * `confirm` asks first: a re-run replaces the transcript, and edits to the
 * live rows go with it. The retry after a failed pass skips the question —
 * the user just asked for exactly this.
 */
export function useRefineAction(): (opts?: { confirm?: boolean }) => Promise<void> {
  const t = useT();
  return useCallback(
    async ({ confirm = true } = {}) => {
      const store = useSessionStore.getState();
      const sessionId = store.sessionId;
      if (!sessionId) return;
      if (confirm) {
        const ok = await confirmDialog({
          title: t("topbar.refine.confirmTitle"),
          description: t("topbar.refine.confirmBody"),
          confirmLabel: t("topbar.refine.confirmRun"),
        });
        if (!ok) return;
      }
      try {
        const { jobId } = await startRefine(sessionId);
        useJobsStore.getState().track({
          jobId,
          label: store.sessionTitle || t("session.new"),
          kind: "refine",
          sessionId,
        });
        // The list carries the status; refresh it so the badge and banner
        // switch to "processing" right away.
        await store.refreshPastSessions();
      } catch (e) {
        // A reason the engine names gets its words; anything else is shown as is.
        const code = e instanceof RefineRefusedError && isRefineRefusal(e.code) ? e.code : null;
        useSessionStore
          .getState()
          .setError(code ? t(`session.refuse.${code}`) : e instanceof Error ? e.message : String(e));
      }
    },
    [t],
  );
}
