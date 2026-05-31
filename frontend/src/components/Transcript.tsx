import { useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { ArrowDown, Mic, Pause, Pencil, Play } from "lucide-react";

import { TimelineMinimap } from "@/components/TimelineMinimap";
import { Button } from "@/components/ui/button";
import { useAutoScroll } from "@/hooks/useAutoScroll";
import { usePlaybackControls } from "@/hooks/playbackContext";
import { formatRelativeTime } from "@/lib/colors";
import { renameSpeaker } from "@/lib/diarize";
import { useSessionStore } from "@/store/sessionStore";
import type { Segment } from "@/types";

/**
 * Single-column "document" layout. Each segment is a card with the original
 * on top and the translation below — same visual rhythm as DeepL / Apple
 * Translate. Centred with a comfortable reading width.
 */
export function Transcript() {
  const segmentOrder = useSessionStore((s) => s.segmentOrder);
  const segments = useSessionStore((s) => s.segments);
  const srcLang = useSessionStore((s) => s.settings.srcLang);
  const tgtLang = useSessionStore((s) => s.settings.tgtLang);

  const ordered = useMemo(
    () =>
      segmentOrder
        .map((id) => segments[id])
        .filter((s): s is Segment => Boolean(s))
        .sort((a, b) => a.startedAt - b.startedAt),
    [segmentOrder, segments],
  );

  // After diarize, group consecutive segments with the same speaker into
  // a single "turn" card. Pure presentation — original segment rows in DB
  // are untouched, so re-diarize / playback timing still work. Segments
  // without a real speaker label fall through one-per-card as before.
  const turns = useMemo(() => mergeBySpeaker(ordered), [ordered]);

  // Whenever the currently-playing segment changes (user clicks ▶, scrubs,
  // or continuous mode advances), scroll its card into view. Skips when
  // user hasn't started any playback to avoid hijacking the scroll.
  const playingId = useSessionStore((s) => s.playingSegmentId);
  useEffect(() => {
    if (!playingId) return;
    const el = document.querySelector<HTMLElement>(
      `[data-segment-ids~="${playingId}"]`,
    );
    if (el) el.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [playingId]);

  const { ref, pinned, scrollToBottom } = useAutoScroll<HTMLDivElement>([ordered]);

  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.24, ease: [0.22, 0.61, 0.36, 1] }}
      className="absolute inset-0 flex flex-col overflow-hidden"
    >
      <TimelineMinimap scrollRef={ref} segments={ordered} />
      <div ref={ref} className="flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-3xl px-6 py-8">
          {ordered.length === 0 ? (
            <ListeningState />
          ) : (
            <div className="space-y-2.5">
              <AnimatePresence initial={false}>
                {turns.map((t) => (
                  <SegmentCard
                    key={t.segment.segmentId}
                    seg={t.segment}
                    relatedIds={t.relatedIds}
                    srcLang={srcLang}
                    tgtLang={tgtLang}
                  />
                ))}
              </AnimatePresence>
              <div className="h-8" />
            </div>
          )}
        </div>
      </div>

      {!pinned && ordered.length > 0 && (
        <Button
          onClick={scrollToBottom}
          size="icon"
          title="Jump to latest"
          className="absolute bottom-6 right-10 size-9 rounded-full shadow-lg"
        >
          <ArrowDown className="size-4" />
        </Button>
      )}
    </motion.div>
  );
}

/**
 * Reached during the warm-up window between Start and the first segment.
 * Splits into two sub-states based on whether the backend has fully
 * loaded models ("Connecting") or is now waiting for speech ("Listening").
 * Cold-start UX lives in `<PreFlight/>`.
 */
function ListeningState() {
  const connection = useSessionStore((s) => s.connection);
  const isConnecting = connection === "connecting";
  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.25 }}
      className="flex min-h-[60vh] flex-col items-center justify-center text-center"
    >
      <div className="relative">
        <motion.div
          className="flex size-20 items-center justify-center rounded-2xl bg-gradient-to-br from-blue-500/15 to-blue-500/5 ring-1 ring-inset ring-blue-500/20"
          animate={{ scale: [1, 1.04, 1] }}
          transition={{ duration: 1.8, repeat: Infinity, ease: "easeInOut" }}
        >
          <Mic className="size-8 text-blue-600 dark:text-blue-400" />
        </motion.div>
        <span className="absolute -right-1 -bottom-1 size-3 animate-pulse rounded-full bg-blue-500 ring-2 ring-background" />
      </div>
      <h2 className="mt-6 text-xl font-semibold tracking-tight text-foreground">
        {isConnecting ? "Warming up models…" : "Listening…"}
      </h2>
      <p className="mt-2 max-w-md text-[14px] leading-relaxed text-muted-foreground">
        {isConnecting
          ? "First start takes a moment while Whisper and the translator load."
          : "Start speaking. Words will appear here as they're recognised."}
      </p>
    </motion.div>
  );
}

interface SegmentCardProps {
  seg: Segment;
  /** All original segment IDs this card represents (1 for un-merged,
   * 2+ for merged speaker turns). Used for play highlight + jump-to-card
   * lookup from the StatusBar playback control. */
  relatedIds?: string[];
  srcLang: string;
  tgtLang: string;
}

function SegmentCard({ seg, relatedIds, srcLang, tgtLang }: SegmentCardProps) {
  const { play, pause } = usePlaybackControls();
  const timestamp = formatRelativeTime(seg.startedAt);
  const isPartial = seg.origStatus === "partial";
  const showTranslation = seg.transStatus !== "skipped";
  const resolvedSrcLang = srcLang === "auto" ? "auto" : srcLang;
  // Playback state — highlight applies if ANY of our sub-segments is the
  // one currently playing (covers merged speaker turns under continuous mode).
  const playingId = useSessionStore((s) => s.playingSegmentId);
  const isPlaying = useSessionStore((s) => s.isPlaying);
  const related = relatedIds && relatedIds.length > 0 ? relatedIds : [seg.segmentId];
  const isHighlight = playingId !== null && related.includes(playingId);
  const isPlayingThis = isPlaying && isHighlight;
  // Recording-in-progress sessions don't have a WAV file yet, so playback
  // would 404. The button only shows once recording is fully stopped.
  const connection = useSessionStore((s) => s.connection);
  const canPlay =
    !isPartial &&
    connection !== "recording" &&
    connection !== "paused" &&
    connection !== "connecting";

  return (
    <motion.article
      layout
      data-segment-id={seg.segmentId}
      // Space-separated list of every sub-segment id this card represents.
      // The StatusBar playback control queries by this so jump-to-card
      // works regardless of which sub-segment is the active playhead.
      data-segment-ids={related.join(" ")}
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -4 }}
      transition={{ duration: 0.22, ease: [0.22, 0.61, 0.36, 1] }}
      className={`group rounded-xl border bg-card px-5 py-4 transition-colors duration-200 ${
        isPartial
          ? "border-blue-500/40 shadow-[0_0_0_2px_rgba(59,130,246,0.08)]"
          : isHighlight
            ? "border-blue-500/60 shadow-[0_0_0_2px_rgba(59,130,246,0.12)]"
            : "border-border hover:border-foreground/15"
      }`}
    >
      <header className="mb-2.5 flex items-center gap-2">
        {canPlay && (
          <button
            type="button"
            onClick={() =>
              isPlayingThis ? pause() : play(seg.segmentId)
            }
            title={isPlayingThis ? "Pause" : "Play this segment"}
            className="inline-flex size-5 items-center justify-center rounded-full bg-muted/60 text-muted-foreground transition-colors hover:bg-blue-500/20 hover:text-blue-600 dark:hover:text-blue-400"
          >
            {isPlayingThis ? (
              <Pause className="size-2.5 fill-current" />
            ) : (
              <Play className="size-2.5 fill-current" />
            )}
          </button>
        )}
        <span className="font-mono text-[11px] font-medium text-muted-foreground">
          {timestamp}
        </span>
        {seg.speaker && seg.speaker !== "unknown" && (
          <SpeakerChip name={seg.speaker} />
        )}
        {isPartial && (
          <span className="inline-flex items-center gap-1 rounded-full bg-blue-500/10 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-blue-600 dark:text-blue-400">
            <span className="size-1.5 animate-pulse rounded-full bg-current" />
            Live
          </span>
        )}
      </header>

      <Row
        lang={resolvedSrcLang}
        text={seg.origText || "…"}
        emphasis="primary"
        partial={isPartial}
      />

      {showTranslation && (
        <>
          <div className="my-2 h-px bg-border/60" />
          <Row
            lang={tgtLang}
            text={
              seg.transText ||
              (seg.transStatus === "pending" ? "Translating…" : "—")
            }
            emphasis="secondary"
            partial={seg.transStatus === "partial"}
            pending={seg.transStatus === "pending"}
          />
        </>
      )}
    </motion.article>
  );
}

function Row({
  lang,
  text,
  emphasis,
  partial,
  pending,
}: {
  lang: string;
  text: string;
  emphasis: "primary" | "secondary";
  partial?: boolean;
  pending?: boolean;
}) {
  return (
    <div className="flex gap-3">
      <span className="mt-1 inline-flex h-[18px] w-9 shrink-0 items-center justify-center rounded-md bg-muted font-mono text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
        {lang}
      </span>
      <p
        className={[
          "flex-1",
          emphasis === "primary"
            ? "text-[15.5px] leading-[1.55] text-foreground"
            : "text-[14px] leading-[1.55] text-muted-foreground",
          partial ? "italic" : "",
          pending ? "italic opacity-60" : "",
        ]
          .filter(Boolean)
          .join(" ")}
      >
        {text}
      </p>
    </div>
  );
}

/**
 * Speaker label chip. Click to edit; commit applies the rename to ALL
 * segments with the same current label (server-side cascade, then we
 * mirror locally so the UI updates without a full session reload).
 */
function SpeakerChip({ name }: { name: string }) {
  const sessionId = useSessionStore((s) => s.sessionId);
  const applySpeakerRename = useSessionStore((s) => s.applySpeakerRename);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(name);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (editing) {
      setDraft(name);
      // next tick so the input is mounted before we focus
      queueMicrotask(() => inputRef.current?.select());
    }
  }, [editing, name]);

  const commit = async () => {
    const next = draft.trim();
    setEditing(false);
    if (!next || next === name || !sessionId) return;
    // Optimistic local update — the server confirms via the rename call.
    applySpeakerRename(name, next);
    try {
      await renameSpeaker(sessionId, name, next);
    } catch (e) {
      // Roll back on failure.
      applySpeakerRename(next, name);
      console.warn(e);
    }
  };

  if (editing) {
    return (
      <input
        ref={inputRef}
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") commit();
          if (e.key === "Escape") setEditing(false);
        }}
        className="h-[18px] w-24 rounded-full border border-blue-500/30 bg-background px-2 text-[10.5px] font-semibold outline-none focus:ring-2 focus:ring-blue-500/30"
      />
    );
  }

  return (
    <button
      onClick={() => setEditing(true)}
      title="Click to rename — applies to all segments by this speaker"
      className="inline-flex items-center gap-1 rounded-full border border-border bg-accent/40 px-1.5 py-0.5 text-[10px] font-semibold tracking-wide text-foreground transition-colors hover:border-blue-500/40 hover:bg-accent"
    >
      {name}
      <Pencil className="size-2.5 text-muted-foreground" />
    </button>
  );
}

interface SpeakerTurn {
  /** Synthetic merged segment for rendering. segmentId = first sub-segment id. */
  segment: Segment;
  /** Original segment IDs covered by this turn, in order. */
  relatedIds: string[];
}

/**
 * Build "speaker turns" — adjacent same-speaker segments collapse into one
 * synthetic merged segment for display. Pre-diarize content and any
 * segments labeled "unknown" fall through one-card-per-segment (no merge).
 */
function mergeBySpeaker(ordered: Segment[]): SpeakerTurn[] {
  const out: SpeakerTurn[] = [];
  for (const seg of ordered) {
    const last = out[out.length - 1];
    const canMerge =
      last !== undefined &&
      seg.speaker != null &&
      seg.speaker !== "unknown" &&
      seg.speaker === last.segment.speaker;
    if (canMerge) {
      const prev = last.segment;
      const joinSep = (a: string, b: string) => (a && b ? `${a} ${b}` : a + b);
      // Don't let a single partial in the middle of an otherwise-final
      // turn mark the whole bubble as partial.
      const origStatus =
        prev.origStatus === "partial" || seg.origStatus === "partial"
          ? "partial"
          : "final";
      const transStatus: Segment["transStatus"] =
        prev.transStatus === "skipped" && seg.transStatus === "skipped"
          ? "skipped"
          : prev.transStatus === "partial" || seg.transStatus === "partial"
            ? "partial"
            : "final";
      const merged: Segment = {
        ...prev,
        endedAt: Math.max(prev.endedAt, seg.endedAt),
        origText: joinSep(prev.origText, seg.origText),
        origStatus,
        transText: joinSep(prev.transText, seg.transText),
        transStatus,
      };
      out[out.length - 1] = {
        segment: merged,
        relatedIds: [...last.relatedIds, seg.segmentId],
      };
    } else {
      out.push({ segment: seg, relatedIds: [seg.segmentId] });
    }
  }
  return out;
}
