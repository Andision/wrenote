import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import {
  Download,
  FileText,
  Languages,
  Loader2,
  MessageSquare,
  Pause,
  Pencil,
  PictureInPicture2,
  Play,
  RefreshCw,
  Square,
  UserSquare2,
} from "lucide-react";

import { ExportMenu } from "@/components/ExportMenu";
import { Button, buttonVariants } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useActiveSessionMeta, useActiveSessionStatus } from "@/hooks/useSessionStatus";
import { useRefineAction } from "@/hooks/useRefineAction";
import { startDiarize, startTranslate } from "@/lib/diarize";
import { hasDesktopOverlay } from "@/lib/overlayBridge";
import { recordingUrl } from "@/lib/recording";
import { confirmDialog } from "@/lib/confirm";
import { cn } from "@/lib/utils";
import { useJobsStore } from "@/store/jobsStore";
import { useSessionStore } from "@/store/sessionStore";
import { useT } from "@/i18n";

interface TopBarProps {
  onStop: () => void;
  onPause: () => void;
  onResume: () => void;
  inPreFlight: boolean;
}

export function TopBar({ onStop, onPause, onResume, inPreFlight }: TopBarProps) {
  const t = useT();
  const connection = useSessionStore((s) => s.connection);
  const title = useSessionStore((s) => s.sessionTitle);
  const renameSession = useSessionStore((s) => s.renameSession);
  const toggleChat = useSessionStore((s) => s.toggleChat);
  const chatOpen = useSessionStore((s) => s.chatOpen);
  const toggleMinutes = useSessionStore((s) => s.toggleMinutes);
  const minutesOpen = useSessionStore((s) => s.minutesOpen);
  const sessionId = useSessionStore((s) => s.sessionId);
  const segmentCountForDiarize = useSessionStore((s) => s.segmentOrder.length);
  const trackJob = useJobsStore((s) => s.track);
  const sessionStatus = useActiveSessionStatus();
  const sessionMeta = useActiveSessionMeta();
  const runRefine = useRefineAction();
  // While the engine rewrites the transcript, nothing else may touch it.
  const processing = sessionStatus === "processing";
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
  // Any real translation present → offer the bilingual export options.
  const hasTranslations = useSessionStore((s) =>
    Object.values(s.segments).some(
      (seg) => seg.transText && seg.transStatus === "final",
    ),
  );

  const finished =
    Boolean(sessionId) &&
    segmentCountForDiarize > 0 &&
    (connection === "disconnected" || connection === "error");
  const canDiarize = finished;
  // Always available on a finished session — even when everything is
  // already translated, the user may want to re-translate (e.g. after
  // changing the target language or fixing the source text).
  const canTranslate = finished;
  // Re-transcribe from the recording. Shown on any finished session; the
  // engine says no (with a reason) when there is no recording to use.
  const canRefine = finished;
  // The WAV only exists once recording has stopped, so the download offer
  // rides the same "finished session" gate as translate / diarize.
  const canDownload =
    Boolean(sessionId) &&
    segmentCountForDiarize > 0 &&
    (connection === "disconnected" || connection === "error");

  const runDiarize = async () => {
    if (!sessionId || activeDiarizeForThis || activeTranslateForThis || processing) return;
    if (hasCustomSpeakerLabel) {
      const ok = await confirmDialog({
        title: t("topbar.diarize.confirmTitle"),
        description: t("topbar.diarize.confirmRenames"),
        confirmLabel: t("topbar.diarize.confirmRerun"),
      });
      if (!ok) return;
    } else if (hasAnySpeakerLabel) {
      const ok = await confirmDialog({
        title: t("topbar.diarize.confirmTitle"),
        description: t("topbar.diarize.confirmExisting"),
        confirmLabel: t("topbar.diarize.confirmAnyway"),
      });
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
        label: t("topbar.diarize.jobLabel", { title: title || t("session.new") }),
        kind: "diarize",
        sessionId,
      });
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      useSessionStore.getState().setError(msg);
    }
  };

  const runTranslate = async () => {
    if (!sessionId || activeTranslateForThis || activeDiarizeForThis || processing) return;
    let retranslate = false;
    if (!hasUntranslated) {
      const ok = await confirmDialog({
        title: t("topbar.translate.confirmTitle"),
        description: t("topbar.translate.confirmAll"),
        confirmLabel: t("topbar.translate.confirmRetranslate"),
      });
      if (!ok) return;
      retranslate = true;
    }
    try {
      const { jobId } = await startTranslate(sessionId, undefined, retranslate);
      trackJob({
        jobId,
        label: t("topbar.translate.jobLabel", { title: title || t("session.new") }),
        kind: "translate",
        sessionId,
      });
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      useSessionStore.getState().setError(msg);
    }
  };
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
  // "connected" is the gap between the socket opening and the backend's `ready`
  // (model load) — still busy, so keep the spinner up instead of going blank.
  const isBusy =
    connection === "connecting" ||
    connection === "connected" ||
    connection === "stopping";

  return (
    <header className="flex h-16 shrink-0 items-center gap-3 border-b bg-card px-4">

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
            data-tip={t("topbar.rename")}
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

      <AnimatePresence>
        {isActive && <RecordingTimer paused={isPaused} />}
        {!isActive && processing && <ProcessingPill key="processing" />}
      </AnimatePresence>

      {/* Floating subtitles — desktop shell only, while a recording is live. */}
      {isActive && hasDesktopOverlay() && (
        <Button
          variant="ghost"
          size="icon"
          onClick={() => void window.wrenoteDesktop?.toggleOverlay()}
          data-tip={t("topbar.overlay")}
          className="size-9"
        >
          <PictureInPicture2 className="size-4" />
        </Button>
      )}

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
                data-tip={isPaused ? t("common.resume") : t("common.pause")}
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
              aria-label={t("topbar.connecting")}
              className="size-9 bg-brand-600/60 text-white shadow-sm dark:bg-brand-500/60"
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
        ) : null}
      </AnimatePresence>

      {canRefine && (
        <Button
          variant="ghost"
          size="icon"
          onClick={() => void runRefine()}
          disabled={processing || activeTranslateForThis || activeDiarizeForThis}
          data-tip={
            processing
              ? t("topbar.refine.running")
              : sessionMeta?.refinedAt
                ? t("topbar.refine.tooltipAgain")
                : t("topbar.refine.tooltip")
          }
          className="size-9"
        >
          {processing ? (
            <Loader2 className="size-4 animate-spin" />
          ) : (
            <RefreshCw className="size-4" />
          )}
        </Button>
      )}

      {canTranslate && (
        <Button
          variant="ghost"
          size="icon"
          onClick={() => void runTranslate()}
          disabled={processing || activeTranslateForThis || activeDiarizeForThis}
          data-tip={
            processing
              ? t("topbar.refine.blocking")
              : activeTranslateForThis
              ? t("topbar.translate.running")
              : activeDiarizeForThis
                ? t("topbar.diarize.blocking")
                : hasUntranslated
                  ? t("topbar.translate.tooltip")
                  : t("topbar.translate.tooltipAll")
          }
          className="size-9"
        >
          {activeTranslateForThis ? (
            <Loader2 className="size-4 animate-spin" />
          ) : (
            <Languages className="size-4" />
          )}
        </Button>
      )}

      {canDiarize && (
        <Button
          variant="ghost"
          size="icon"
          onClick={() => void runDiarize()}
          disabled={processing || activeDiarizeForThis || activeTranslateForThis}
          data-tip={
            processing
              ? t("topbar.refine.blocking")
              : activeDiarizeForThis
              ? t("topbar.diarize.running")
              : activeTranslateForThis
                ? t("topbar.translate.blocking")
                : t("topbar.diarize.tooltip")
          }
          className="size-9"
        >
          {activeDiarizeForThis ? (
            <Loader2 className="size-4 animate-spin" />
          ) : (
            <UserSquare2 className="size-4" />
          )}
        </Button>
      )}

      {canDownload && sessionId && (
        <a
          href={recordingUrl(sessionId)}
          download={`${title || sessionId}.wav`}
          data-tip={t("topbar.download")}
          className={cn(buttonVariants({ variant: "ghost", size: "icon" }), "size-9")}
        >
          <Download className="size-4" />
        </a>
      )}

      {canDownload && sessionId && (
        <ExportMenu sessionId={sessionId} title={title} hasTranslations={hasTranslations} />
      )}

      {finished && (
        <Button
          variant="ghost"
          size="icon"
          onClick={() => toggleMinutes()}
          data-tip={t("topbar.minutes")}
          aria-pressed={minutesOpen}
          className={minutesOpen ? "size-9 bg-accent text-foreground" : "size-9"}
        >
          <FileText className="size-4" />
        </Button>
      )}

      <Button
        variant="ghost"
        size="icon"
        onClick={() => toggleChat()}
        data-tip={t("topbar.chat")}
        aria-pressed={chatOpen}
        className={
          chatOpen
            ? "size-9 bg-accent text-foreground"
            : "size-9"
        }
      >
        <MessageSquare className="size-4" />
      </Button>
    </header>
  );
}

/**
 * Where the recording timer sits while the recording is being transcribed
 * again from the file: the session isn't live, but it isn't done either.
 */
function ProcessingPill() {
  const t = useT();
  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.92 }}
      animate={{ opacity: 1, scale: 1 }}
      exit={{ opacity: 0, scale: 0.92 }}
      transition={{ duration: 0.18 }}
      className="flex h-8 items-center gap-2 rounded-lg border border-brand-500/20 bg-brand-500/8 px-2.5 text-brand-600 dark:border-brand-400/25 dark:bg-brand-400/10 dark:text-brand-400"
      data-tip={t("session.status.processingHint")}
    >
      <Loader2 className="size-3 animate-spin" />
      <span className="text-[12px] font-medium">{t("session.status.processing")}</span>
    </motion.div>
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
