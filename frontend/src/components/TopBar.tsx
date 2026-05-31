import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import {
  ArrowRight,
  Languages,
  Loader2,
  MessageSquare,
  Mic,
  PanelLeft,
  Pause,
  Pencil,
  Play,
  Settings,
  Square,
  UserSquare2,
} from "lucide-react";

import {
  LanguageSelect,
  SOURCE_LANGUAGES,
  TARGET_LANGUAGES,
} from "@/components/LanguageSelect";
import { ThemeToggle } from "@/components/ThemeToggle";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Separator } from "@/components/ui/separator";
import { startDiarize, startTranslate } from "@/lib/diarize";
import { useJobsStore } from "@/store/jobsStore";
import { useSessionStore } from "@/store/sessionStore";

interface TopBarProps {
  onStart: () => void;
  onStop: () => void;
  onPause: () => void;
  onResume: () => void;
  inPreFlight: boolean;
}

export function TopBar({ onStart, onStop, onPause, onResume, inPreFlight }: TopBarProps) {
  const connection = useSessionStore((s) => s.connection);
  const title = useSessionStore((s) => s.sessionTitle);
  const renameSession = useSessionStore((s) => s.renameSession);
  const toggleSidebar = useSessionStore((s) => s.toggleSidebar);
  const toggleSettings = useSessionStore((s) => s.toggleSettings);
  const toggleChat = useSessionStore((s) => s.toggleChat);
  const chatOpen = useSessionStore((s) => s.chatOpen);
  const sessionId = useSessionStore((s) => s.sessionId);
  const segmentCountForDiarize = useSessionStore((s) => s.segmentOrder.length);
  const trackJob = useJobsStore((s) => s.track);
  const activeDiarizeForThis = useJobsStore((s) =>
    Object.values(s.jobs).some(
      (j) =>
        j.kind === "diarize" &&
        j.sessionId === sessionId &&
        j.snapshot?.status !== "done" &&
        j.snapshot?.status !== "error",
    ),
  );
  const activeTranslateForThis = useJobsStore((s) =>
    Object.values(s.jobs).some(
      (j) =>
        j.kind === "translate" &&
        j.sessionId === sessionId &&
        j.snapshot?.status !== "done" &&
        j.snapshot?.status !== "error",
    ),
  );
  // Are there speaker labels already? Distinguish "Speaker N" (auto)
  // from any custom rename, so we can warn only when the user has
  // invested manual edits that re-running diarize would clobber.
  // Two *primitive* selectors so Zustand's default Object.is comparison
  // works — returning a fresh {hasAny,hasCustom} object each call
  // triggers React's "getSnapshot should be cached" infinite loop.
  const hasAnySpeakerLabel = useSessionStore((s) => {
    for (const seg of Object.values(s.segments)) {
      const sp = seg.speaker;
      if (sp && sp !== "unknown") return true;
    }
    return false;
  });
  const hasCustomSpeakerLabel = useSessionStore((s) => {
    for (const seg of Object.values(s.segments)) {
      const sp = seg.speaker;
      if (sp && sp !== "unknown" && !/^Speaker \d+$/.test(sp)) return true;
    }
    return false;
  });
  // Any segment that has original text but no real translation → button
  // is meaningful. (Segments whose lang already matches the target get
  // skipped server-side, so it's safe to click even when most are done.)
  const hasUntranslated = useSessionStore((s) =>
    Object.values(s.segments).some(
      (seg) =>
        seg.origText &&
        (!seg.transText || seg.transStatus === "skipped"),
    ),
  );

  const canDiarize =
    Boolean(sessionId) &&
    segmentCountForDiarize > 0 &&
    (connection === "disconnected" || connection === "error");
  const canTranslate =
    Boolean(sessionId) &&
    segmentCountForDiarize > 0 &&
    hasUntranslated &&
    (connection === "disconnected" || connection === "error");

  const runDiarize = async () => {
    if (!sessionId || activeDiarizeForThis) return;
    if (hasCustomSpeakerLabel) {
      const ok = confirm(
        "Re-running speaker identification will reset any speaker " +
        "renames (e.g. Alice → Speaker 1). Continue?",
      );
      if (!ok) return;
    } else if (hasAnySpeakerLabel) {
      const ok = confirm(
        "This session already has speaker labels. Re-run anyway?",
      );
      if (!ok) return;
    }
    try {
      const { jobId } = await startDiarize(sessionId);
      // onDone is rebuilt from the persisted record on refresh, so we
      // don't pass a closure here — jobsStore knows what "diarize" means.
      trackJob({
        jobId,
        // Session title in the label so the overlay tells you *which*
        // session is being processed when you have multiple in flight.
        label: `Identify speakers: ${title || "Untitled session"}`,
        kind: "diarize",
        sessionId,
      });
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      useSessionStore.getState().setError(msg);
    }
  };

  const runTranslate = async () => {
    if (!sessionId || activeTranslateForThis) return;
    try {
      const { jobId } = await startTranslate(sessionId);
      trackJob({
        jobId,
        label: `Translate: ${title || "Untitled session"}`,
        kind: "translate",
        sessionId,
      });
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      useSessionStore.getState().setError(msg);
    }
  };
  const settings = useSessionStore((s) => s.settings);
  const updateSettings = useSessionStore((s) => s.updateSettings);
  const segmentCount = useSessionStore((s) => s.segmentOrder.length);

  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");

  const startEditing = () => {
    setDraft(title);
    setEditing(true);
  };
  const commit = () => {
    setEditing(false);
    if (draft.trim() && draft !== title) renameSession(draft.trim());
  };

  const isRecording = connection === "recording";
  const isPaused = connection === "paused";
  const isActive = isRecording || isPaused;
  const isBusy = connection === "connecting" || connection === "stopping";

  return (
    <header className="flex h-16 shrink-0 items-center gap-3 border-b bg-card px-4">
      <Button
        variant="ghost"
        size="icon"
        onClick={() => toggleSidebar()}
        title="Toggle sessions sidebar"
        className="size-9"
      >
        <PanelLeft className="size-4" />
      </Button>

      {/* Wordmark */}
      <div className="flex items-center gap-2">
        <div className="flex size-7 items-center justify-center rounded-md bg-foreground text-background">
          <Languages className="size-4" />
        </div>
        <span className="text-[15px] font-semibold tracking-tight text-foreground">
          interpreter
        </span>
      </div>

      <Separator orientation="vertical" className="!h-7" />

      {/* Session title + meta */}
      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        {editing ? (
          <Input
            autoFocus
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onBlur={commit}
            onKeyDown={(e) => {
              if (e.key === "Enter") commit();
              if (e.key === "Escape") setEditing(false);
            }}
            className="h-7 max-w-xs text-[14px]"
          />
        ) : (
          <button
            onDoubleClick={startEditing}
            className="group flex min-w-0 items-center gap-1.5 self-start rounded text-[14px] font-medium text-foreground hover:text-foreground/80"
            title="Double-click to rename"
          >
            <span className="truncate">{title}</span>
            <Pencil className="size-3 opacity-0 transition-opacity group-hover:opacity-40" />
          </button>
        )}
        {segmentCount > 0 && (
          <div className="text-[11.5px] text-muted-foreground">
            {segmentCount} segments
          </div>
        )}
      </div>

      {/* Interactive language chips — only here when pre-flight is gone, so
          motion can do the shared-element move from pre-flight to TopBar. */}
      <AnimatePresence>
        {!inPreFlight && (
          <motion.div
            key="lang-strip-topbar"
            layout
            layoutId="lang-strip"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.2 }}
            className="flex items-center gap-1.5 rounded-xl border border-border/60 bg-card/60 px-1.5 py-1"
          >
            <LanguageSelect
              value={settings.srcLang}
              options={SOURCE_LANGUAGES}
              onChange={(v) => updateSettings({ srcLang: v })}
              disabled={isActive}
              size="compact"
              ariaLabel="Source language"
            />
            {settings.translateEnabled && (
              <>
                <ArrowRight className="size-3 text-muted-foreground/60" />
                <LanguageSelect
                  value={settings.tgtLang}
                  options={TARGET_LANGUAGES}
                  onChange={(v) => updateSettings({ tgtLang: v })}
                  disabled={isActive}
                  size="compact"
                  ariaLabel="Target language"
                />
              </>
            )}
            <button
              type="button"
              onClick={() =>
                !isActive &&
                updateSettings({ translateEnabled: !settings.translateEnabled })
              }
              disabled={isActive}
              title={
                settings.translateEnabled
                  ? "Translation on — click for transcribe-only"
                  : "Transcribe-only — click to turn translation on"
              }
              className={
                "ml-0.5 inline-flex h-6 items-center rounded-md px-1.5 text-[10px] font-semibold uppercase tracking-wider transition-colors " +
                (settings.translateEnabled
                  ? "bg-blue-500/10 text-blue-600 hover:bg-blue-500/15 dark:text-blue-400"
                  : "bg-muted text-muted-foreground hover:bg-muted/70") +
                (isActive ? " opacity-60" : "")
              }
            >
              {settings.translateEnabled ? "Translate" : "STT only"}
            </button>
          </motion.div>
        )}
      </AnimatePresence>

      <AnimatePresence>
        {isActive && <RecordingTimer paused={isPaused} />}
      </AnimatePresence>

      <AnimatePresence mode="wait" initial={false}>
        {inPreFlight ? null : isActive ? (
          <motion.div
            key="active-controls"
            initial={{ opacity: 0, scale: 0.92 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0, scale: 0.92 }}
            transition={{ duration: 0.15 }}
            className="flex items-center gap-1.5"
          >
            <motion.div whileHover={{ scale: 1.04 }} whileTap={{ scale: 0.95 }}>
              <Button
                variant="ghost"
                size="icon"
                onClick={isPaused ? onResume : onPause}
                title={isPaused ? "Resume" : "Pause"}
                className="size-9 rounded-full text-foreground/70 hover:bg-accent hover:text-foreground"
              >
                {isPaused ? (
                  <Play className="size-4 fill-current" />
                ) : (
                  <Pause className="size-4 fill-current" />
                )}
              </Button>
            </motion.div>
            <motion.div whileHover={{ scale: 1.03 }} whileTap={{ scale: 0.96 }}>
              <Button
                variant="destructive"
                size="default"
                onClick={onStop}
                className="gap-1.5"
              >
                <Square className="size-3.5 fill-current" />
                Stop
              </Button>
            </motion.div>
          </motion.div>
        ) : isBusy ? (
          // Connecting: just a spinner inside a square button — no label.
          // Fixed width matches the Record button so the layout doesn't jitter.
          <motion.div
            key="busy"
            initial={{ opacity: 0, scale: 0.92 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0, scale: 0.92 }}
            transition={{ duration: 0.15 }}
          >
            <Button
              size="icon"
              disabled
              aria-label="Connecting"
              className="size-9 bg-blue-600/60 text-white shadow-sm dark:bg-blue-500/60"
            >
              <motion.span
                animate={{ rotate: 360 }}
                transition={{ duration: 0.9, repeat: Infinity, ease: "linear" }}
                className="inline-flex"
              >
                <Loader2 className="size-4" />
              </motion.span>
            </Button>
          </motion.div>
        ) : (
          <motion.div
            key="record"
            initial={{ opacity: 0, scale: 0.92 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0, scale: 0.92 }}
            transition={{ duration: 0.15 }}
            whileHover={{ scale: 1.03 }}
            whileTap={{ scale: 0.96 }}
          >
            <Button
              size="default"
              onClick={onStart}
              className="gap-1.5 bg-blue-600 text-white shadow-sm hover:bg-blue-700 dark:bg-blue-500 dark:hover:bg-blue-600"
            >
              <Mic className="size-4" />
              Record
            </Button>
          </motion.div>
        )}
      </AnimatePresence>

      {canTranslate && (
        <Button
          variant="ghost"
          size="icon"
          onClick={() => void runTranslate()}
          disabled={activeTranslateForThis}
          title={
            activeTranslateForThis
              ? "Already running — see the progress bar"
              : "Translate untranslated segments (runs in background)"
          }
          className="size-9"
        >
          <Languages className="size-4" />
        </Button>
      )}

      {canDiarize && (
        <Button
          variant="ghost"
          size="icon"
          onClick={() => void runDiarize()}
          disabled={activeDiarizeForThis}
          title={
            activeDiarizeForThis
              ? "Already running — see the progress bar"
              : "Identify speakers (runs in background)"
          }
          className="size-9"
        >
          <UserSquare2 className="size-4" />
        </Button>
      )}

      <Button
        variant="ghost"
        size="icon"
        onClick={() => toggleChat()}
        title="Chat about this session"
        aria-pressed={chatOpen}
        className={
          chatOpen
            ? "size-9 bg-accent text-foreground"
            : "size-9"
        }
      >
        <MessageSquare className="size-4" />
      </Button>

      <ThemeToggle />

      <Button
        variant="ghost"
        size="icon"
        onClick={() => toggleSettings()}
        title="Settings"
        className="size-9"
      >
        <Settings className="size-4" />
      </Button>
    </header>
  );
}

/**
 * Live mm:ss readout for the active session. Mounted while connection is
 * "recording" or "paused"; resets on each new session. Pause freezes the
 * displayed time and switches the styling to neutral. Music-player vibe:
 * tabular-nums monospace + a pulsing dot (only while live).
 */
function RecordingTimer({ paused }: { paused: boolean }) {
  const [elapsedMs, setElapsedMs] = useState(0);
  // wall-clock start, minus total paused intervals, gives the live count.
  const startRef = useRef<number>(Date.now());
  const pausedTotalRef = useRef<number>(0);
  const pausedAtRef = useRef<number | null>(null);

  // Track pause/resume edges to accumulate paused intervals.
  useEffect(() => {
    if (paused && pausedAtRef.current === null) {
      pausedAtRef.current = Date.now();
    } else if (!paused && pausedAtRef.current !== null) {
      pausedTotalRef.current += Date.now() - pausedAtRef.current;
      pausedAtRef.current = null;
    }
  }, [paused]);

  useEffect(() => {
    startRef.current = Date.now();
    pausedTotalRef.current = 0;
    pausedAtRef.current = null;
    setElapsedMs(0);
    const id = window.setInterval(() => {
      const now = Date.now();
      const inPause =
        pausedAtRef.current !== null ? now - pausedAtRef.current : 0;
      setElapsedMs(now - startRef.current - pausedTotalRef.current - inPause);
    }, 500);
    return () => window.clearInterval(id);
  }, []);

  const totalSec = Math.max(0, Math.floor(elapsedMs / 1000));
  const mm = Math.floor(totalSec / 60);
  const ss = totalSec % 60;

  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.92 }}
      animate={{ opacity: 1, scale: 1 }}
      exit={{ opacity: 0, scale: 0.92 }}
      transition={{ duration: 0.18 }}
      className={
        paused
          ? "flex h-8 items-center gap-2 rounded-lg border border-border bg-muted/60 px-2.5 text-muted-foreground"
          : "flex h-8 items-center gap-2 rounded-lg border border-red-500/20 bg-red-500/8 px-2.5 text-red-600 dark:border-red-400/25 dark:bg-red-400/10 dark:text-red-400"
      }
    >
      {paused ? (
        <Pause className="size-3 fill-current" />
      ) : (
        <motion.span
          aria-hidden
          className="size-1.5 rounded-full bg-current"
          animate={{ opacity: [1, 0.25, 1] }}
          transition={{ duration: 1.4, repeat: Infinity, ease: "easeInOut" }}
        />
      )}
      <span className="font-mono text-[12.5px] font-semibold tabular-nums">
        {String(mm).padStart(2, "0")}:{String(ss).padStart(2, "0")}
      </span>
    </motion.div>
  );
}
