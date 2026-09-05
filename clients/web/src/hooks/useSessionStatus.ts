import { useJobsStore } from "@/store/jobsStore";
import { useSessionStore } from "@/store/sessionStore";
import type { SessionMeta, SessionStatus } from "@/types";

/** The status of the session on screen, as the engine last reported it.
 *
 * The list is the source: it is refreshed when a recording stops and when
 * any job ends, so it is current at exactly the moments the status changes.
 * A session the list doesn't know yet (a recording in progress that hasn't
 * been listed) reads from the connection state instead. */
export function useActiveSessionStatus(): SessionStatus | null {
  const sessionId = useSessionStore((s) => s.sessionId);
  const connection = useSessionStore((s) => s.connection);
  const listed = useSessionStore((s) => s.pastSessions.find((p) => p.id === sessionId)?.status);
  if (!sessionId) return null;
  if (connection === "recording" || connection === "paused" || connection === "stopping") {
    return "recording";
  }
  return listed ?? null;
}

/** The listed row for the session on screen, or null. */
export function useActiveSessionMeta(): SessionMeta | null {
  const sessionId = useSessionStore((s) => s.sessionId);
  return useSessionStore((s) => s.pastSessions.find((p) => p.id === sessionId) ?? null);
}

/** Progress (0..1) of the refine job on `sessionId`, or null when none is tracked. */
export function useRefineProgress(sessionId: string | null): number | null {
  return useJobsStore((s) => {
    if (!sessionId) return null;
    for (const j of Object.values(s.jobs)) {
      if (j.kind === "refine" && j.sessionId === sessionId && j.snapshot?.status === "running") {
        return j.snapshot.fraction;
      }
    }
    return null;
  });
}
