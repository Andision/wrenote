import { AnimatePresence, motion } from "motion/react";
import { Download, Plus, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { formatRelativeTime } from "@/lib/colors";
import { recordingUrl } from "@/lib/recording";
import { useSessionStore } from "@/store/sessionStore";

export function Sidebar() {
  const open = useSessionStore((s) => s.sidebarOpen);
  const pastSessions = useSessionStore((s) => s.pastSessions);
  const activeId = useSessionStore((s) => s.sessionId);
  const loadSession = useSessionStore((s) => s.loadSession);
  const deletePast = useSessionStore((s) => s.deletePastSession);
  const startNew = useSessionStore((s) => s.startNewSession);
  const connection = useSessionStore((s) => s.connection);
  // While a session is live (recording / paused / starting / stopping) we
  // lock past-session navigation. Otherwise the top-bar controls would
  // still operate the live WS while the user is staring at a frozen,
  // unrelated transcript — confusing and dangerous (Stop would end the
  // wrong thing).
  const locked =
    connection === "recording" ||
    connection === "paused" ||
    connection === "connecting" ||
    connection === "stopping";

  // Animate both width (to keep `<main>` from snap-resizing) and content
  // x-offset (so items slide in rather than crunching). 220ms is short
  // enough to feel responsive but long enough to read as a deliberate gesture.
  return (
    <AnimatePresence initial={false}>
      {open && (
        <motion.aside
          key="sidebar"
          initial={{ width: 0, opacity: 0 }}
          animate={{ width: 288, opacity: 1 }}
          exit={{ width: 0, opacity: 0 }}
          transition={{ duration: 0.22, ease: [0.32, 0.72, 0, 1] }}
          className="flex shrink-0 flex-col overflow-hidden border-r bg-sidebar"
        >
          <SidebarBody
            pastSessions={pastSessions}
            activeId={activeId}
            loadSession={loadSession}
            deletePast={deletePast}
            startNew={startNew}
            locked={locked}
          />
        </motion.aside>
      )}
    </AnimatePresence>
  );
}

interface SidebarBodyProps {
  pastSessions: ReturnType<typeof useSessionStore.getState>["pastSessions"];
  activeId: string | null;
  loadSession: (id: string) => Promise<void>;
  deletePast: (id: string) => Promise<void>;
  startNew: () => string;
  locked: boolean;
}

function SidebarBody({
  pastSessions,
  activeId,
  loadSession,
  deletePast,
  startNew,
  locked,
}: SidebarBodyProps) {
  return (
    <motion.div
      className="flex h-full w-72 flex-col"
      initial={{ x: -20 }}
      animate={{ x: 0 }}
      exit={{ x: -20 }}
      transition={{ duration: 0.22, ease: [0.32, 0.72, 0, 1] }}
    >
      <header className="flex h-12 items-center justify-between border-b px-3">
        <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          Sessions
        </span>
        <Button
          variant="ghost"
          size="icon"
          className="size-7"
          onClick={() => startNew()}
          disabled={locked}
          title={locked ? "Stop the current session first" : "New session"}
        >
          <Plus className="size-4" />
        </Button>
      </header>

      {locked && (
        <div className="px-3 py-2 text-[11px] text-muted-foreground">
          Switching sessions is disabled while recording.
        </div>
      )}

      <ScrollArea className="flex-1">
        <div className="space-y-0.5 p-2">
          {pastSessions.length === 0 ? (
            <p className="px-2 py-8 text-center text-xs text-muted-foreground">
              No past sessions yet. Hit <span className="font-medium">Record</span> to start.
            </p>
          ) : (
            pastSessions.map((s) => {
              const date = new Date(s.createdAt);
              const dateStr = date.toLocaleDateString();
              const timeStr = date.toLocaleTimeString([], {
                hour: "2-digit",
                minute: "2-digit",
              });
              const isActive = s.id === activeId;
              return (
                <button
                  key={s.id}
                  onClick={() => {
                    if (locked) return;
                    void loadSession(s.id);
                  }}
                  disabled={locked && !isActive}
                  title={
                    locked && !isActive
                      ? "Stop the current session before loading another"
                      : undefined
                  }
                  className={`group flex w-full items-start gap-2 rounded-md px-2 py-2 text-left transition-colors ${
                    isActive
                      ? "bg-accent"
                      : locked
                        ? "cursor-not-allowed opacity-40"
                        : "hover:bg-accent/60"
                  }`}
                >
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm font-medium text-foreground">
                      {s.title}
                    </div>
                    <div className="mt-0.5 truncate text-[11px] text-muted-foreground">
                      {dateStr} · {timeStr} · {formatRelativeTime(s.durationS)} ·{" "}
                      {s.srcLang.toUpperCase()}→{s.tgtLang.toUpperCase()}
                    </div>
                  </div>
                  <span className="invisible flex shrink-0 items-center gap-0.5 group-hover:visible">
                    <a
                      href={recordingUrl(s.id)}
                      onClick={(e) => e.stopPropagation()}
                      download={`${s.title || s.id}.wav`}
                      title="Download recording"
                      className="rounded p-1 text-muted-foreground hover:bg-accent hover:text-foreground"
                    >
                      <Download className="size-3.5" />
                    </a>
                    <span
                      role="button"
                      tabIndex={0}
                      aria-label="Delete session"
                      onClick={(e) => {
                        e.stopPropagation();
                        if (confirm(`Delete "${s.title}" and its recording?`)) void deletePast(s.id);
                      }}
                      className="rounded p-1 text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
                    >
                      <Trash2 className="size-3.5" />
                    </span>
                  </span>
                </button>
              );
            })
          )}
        </div>
      </ScrollArea>
    </motion.div>
  );
}
