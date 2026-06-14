import { useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { Check, Loader2, Merge, Mic, Pause, Pencil, Play, Scissors } from "lucide-react";

import { TimelineMinimap } from "@/components/TimelineMinimap";
import { useAutoScroll } from "@/hooks/useAutoScroll";
import { usePlaybackControls } from "@/hooks/playbackContext";
import {
  buildSpeakerPalette,
  formatRelativeTime,
  SPEAKER_SWATCHES,
} from "@/lib/colors";
import { assignSpeaker, renameSpeaker } from "@/lib/diarize";
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

  // Stable per-speaker colors, assigned by order of first appearance so the
  // transcript and the timeline rail agree. `diarized` tells us whether the
  // session has been through speaker ID yet — only then do we surface an
  // "unknown" chip on the leftover unlabeled segments (so users can label
  // them), rather than cluttering a pre-diarize transcript.
  const speakerColors = useMemo(
    () => buildSpeakerPalette(ordered.map((s) => s.speaker)),
    [ordered],
  );
  const colorOverrides = useSessionStore((s) => s.speakerColors);
  const diarized = speakerColors.size > 0;
  const colorFor = (label: string | null) =>
    label ? colorOverrides[label] ?? speakerColors.get(label) ?? null : null;

  // `pinned`/`scrollToBottom` aren't read — the hook still keeps us stuck to
  // the bottom on new content; we just don't surface a jump-to-latest button.
  const { ref } = useAutoScroll<HTMLDivElement>([ordered]);

  // Auto-follow the playing segment without hijacking the user's scroll.
  // Always follow when playback first starts (prev === null). On continuous
  // auto-advance, only scroll to the next segment if the one we're leaving
  // is still in view — i.e. the user is watching the playhead. If they've
  // scrolled away to read elsewhere, leave their position where it is.
  const playingId = useSessionStore((s) => s.playingSegmentId);
  const prevPlayingIdRef = useRef<string | null>(null);
  useEffect(() => {
    const prev = prevPlayingIdRef.current;
    prevPlayingIdRef.current = playingId;
    if (!playingId) return;
    const container = ref.current;
    const target = document.querySelector<HTMLElement>(
      `[data-segment-ids~="${playingId}"]`,
    );
    if (!container || !target) return;

    let follow = prev === null;
    if (!follow && prev) {
      const prevEl = document.querySelector<HTMLElement>(
        `[data-segment-ids~="${prev}"]`,
      );
      if (!prevEl) {
        follow = true; // segment we were on is gone (e.g. re-segmented)
      } else {
        const c = container.getBoundingClientRect();
        const p = prevEl.getBoundingClientRect();
        follow = p.bottom > c.top && p.top < c.bottom; // leaving segment still visible
      }
    }
    if (follow) target.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [playingId, ref]);

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
        {/* Extra right padding keeps card borders clear of the timeline rail
            (~36px of visuals on the right edge) when the column is narrow,
            e.g. with both the sidebar and chat panel open. */}
        <div className="mx-auto w-full max-w-3xl py-8 pl-6 pr-12">
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
                    color={colorFor(t.segment.speaker ?? null)}
                    diarized={diarized}
                  />
                ))}
              </AnimatePresence>
              <div className="h-8" />
            </div>
          )}
        </div>
      </div>
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
  // Both "connecting" (opening the socket) and "connected" (socket open, but
  // still waiting for the backend's `ready` while it loads models) are pre-roll:
  // the mic isn't capturing yet, so show a spinner — not "Listening…".
  const isPreparing =
    connection === "connecting" || connection === "connected";
  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.25 }}
      className="flex min-h-[60vh] flex-col items-center justify-center text-center"
    >
      <div className="relative">
        <motion.div
          className="flex size-20 items-center justify-center rounded-2xl bg-gradient-to-br from-brand-500/15 to-brand-500/5 ring-1 ring-inset ring-brand-500/20"
          animate={{ scale: [1, 1.04, 1] }}
          transition={{ duration: 1.8, repeat: Infinity, ease: "easeInOut" }}
        >
          {isPreparing ? (
            <Loader2 className="size-8 animate-spin text-brand-600 dark:text-brand-400" />
          ) : (
            <Mic className="size-8 text-brand-600 dark:text-brand-400" />
          )}
        </motion.div>
        {!isPreparing && (
          <span className="absolute -right-1 -bottom-1 size-3 animate-pulse rounded-full bg-brand-500 ring-2 ring-background" />
        )}
      </div>
      <h2 className="mt-6 text-xl font-semibold tracking-tight text-foreground">
        {isPreparing ? "Preparing…" : "Listening…"}
      </h2>
      <p className="mt-2 max-w-md text-[14px] leading-relaxed text-muted-foreground">
        {isPreparing
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
  /** Speaker tint (null when unlabeled). Drives the left accent + chip dot. */
  color?: string | null;
  /** Whether the session has been diarized — gates the "unknown" chip. */
  diarized?: boolean;
}

function SegmentCard({
  seg,
  relatedIds,
  srcLang,
  tgtLang,
  color = null,
  diarized = false,
}: SegmentCardProps) {
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
  // Inline text editing: only on a finished, final, single-segment card. A
  // merged speaker turn (relatedIds 2+) can't map an edit back to one segment —
  // that needs split/merge (Layer 2).
  const editSegmentText = useSessionStore((s) => s.editSegmentText);
  const splitSegment = useSessionStore((s) => s.splitSegment);
  const mergeWithNext = useSessionStore((s) => s.mergeWithNext);
  const editable = canPlay && (relatedIds?.length ?? 1) <= 1;
  // Merge target is the next segment by order; disabled on the last card.
  const isLast = useSessionStore(
    (s) => s.segmentOrder[s.segmentOrder.length - 1] === seg.segmentId,
  );
  const canMerge = editable && !isLast;

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
      // Left accent in the speaker color (when known). Inline borderLeftColor
      // overrides only the left edge; the rest follows the state classes.
      style={color ? { borderLeftColor: color, borderLeftWidth: 3 } : undefined}
      className={`group rounded-xl border bg-card px-5 py-4 transition-colors duration-200 ${
        isPartial
          ? "border-brand-500/40 shadow-[0_0_0_2px_rgba(170,122,78,0.08)]"
          : isHighlight
            ? "border-brand-500/60 shadow-[0_0_0_2px_rgba(170,122,78,0.12)]"
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
            data-tip={isPlayingThis ? "Pause" : "Play this segment"}
            className="inline-flex size-5 items-center justify-center rounded-full bg-muted/60 text-muted-foreground transition-colors hover:bg-brand-500/20 hover:text-brand-600 dark:hover:text-brand-400"
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
        {seg.speaker && seg.speaker !== "unknown" ? (
          <SpeakerChip
            name={seg.speaker}
            isUnknown={false}
            segmentIds={related}
            color={color}
          />
        ) : (
          diarized &&
          !isPartial && (
            <SpeakerChip
              name="unknown"
              isUnknown
              segmentIds={related}
              color={null}
            />
          )
        )}
        {isPartial && (
          <span className="inline-flex items-center gap-1 rounded-full bg-brand-500/10 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-brand-600 dark:text-brand-400">
            <span className="size-1.5 animate-pulse rounded-full bg-current" />
            Live
          </span>
        )}
        {canMerge && (
          <button
            type="button"
            onClick={() => mergeWithNext(seg.segmentId)}
            data-tip="Merge with the next segment"
            className="ml-auto inline-flex size-5 items-center justify-center rounded text-muted-foreground opacity-0 transition-opacity hover:bg-accent hover:text-foreground group-hover:opacity-100"
            aria-label="Merge with next segment"
          >
            <Merge className="size-3.5" />
          </button>
        )}
      </header>

      <Row
        lang={resolvedSrcLang}
        text={seg.origText}
        placeholder="…"
        emphasis="primary"
        partial={isPartial}
        editable={editable}
        onCommit={(t) => editSegmentText(seg.segmentId, { origText: t })}
        onSplit={(offset) => splitSegment(seg.segmentId, offset)}
      />

      {showTranslation && (
        <>
          <div className="my-2 h-px bg-border/60" />
          <Row
            lang={tgtLang}
            text={seg.transText}
            placeholder={seg.transStatus === "pending" ? "Translating…" : "—"}
            emphasis="secondary"
            partial={seg.transStatus === "partial"}
            pending={seg.transStatus === "pending"}
            stale={seg.transStatus === "stale"}
            editable={editable}
            onCommit={(t) => editSegmentText(seg.segmentId, { transText: t })}
          />
        </>
      )}
    </motion.article>
  );
}

function autosize(el: HTMLTextAreaElement | null) {
  if (!el) return;
  el.style.height = "auto";
  el.style.height = `${el.scrollHeight}px`;
}

function Row({
  lang,
  text,
  placeholder = "",
  emphasis,
  partial,
  pending,
  stale,
  editable,
  onCommit,
  onSplit,
}: {
  lang: string;
  /** Raw text (may be empty); `placeholder` is shown when empty & not editing. */
  text: string;
  placeholder?: string;
  emphasis: "primary" | "secondary";
  partial?: boolean;
  pending?: boolean;
  /** Translation no longer matches an edited original. */
  stale?: boolean;
  editable?: boolean;
  onCommit?: (text: string) => void;
  /** If set, show a "Split here" action while editing (splits at the caret). */
  onSplit?: (offset: number) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(text);
  const taRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (editing) {
      const ta = taRef.current;
      ta?.focus();
      ta?.select();
      autosize(ta);
    }
  }, [editing]);

  const begin = () => {
    if (!editable) return;
    setDraft(text);
    setEditing(true);
  };
  const commit = () => {
    setEditing(false);
    const v = draft.trim();
    if (onCommit && v !== text.trim()) onCommit(v);
  };

  const chip = (
    <span className="mt-1 inline-flex h-[18px] w-9 shrink-0 items-center justify-center rounded-md bg-muted font-mono text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
      {lang}
    </span>
  );

  const textClass = [
    "flex-1",
    emphasis === "primary"
      ? "text-[15.5px] leading-[1.55] text-foreground"
      : "text-[14px] leading-[1.55] text-muted-foreground",
    partial ? "italic" : "",
    pending ? "italic opacity-60" : "",
    stale ? "opacity-50" : "",
  ]
    .filter(Boolean)
    .join(" ");

  if (editing) {
    return (
      <div className="flex gap-3">
        {chip}
        <div className="flex flex-1 flex-col gap-1">
          <textarea
            ref={taRef}
            value={draft}
            onChange={(e) => {
              setDraft(e.target.value);
              autosize(e.target);
            }}
            onBlur={commit}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                commit();
              } else if (e.key === "Escape") {
                e.preventDefault();
                setEditing(false);
              }
            }}
            rows={1}
            className={`resize-none rounded-md border border-brand-500/40 bg-background px-2 py-1 ${
              emphasis === "primary" ? "text-[15.5px]" : "text-[14px]"
            } leading-[1.55] text-foreground focus:outline-none focus:ring-2 focus:ring-brand-500/30`}
          />
          <div className="flex items-center gap-3 text-[11px] text-muted-foreground">
            <span>Enter to save · Esc to cancel</span>
            {onSplit && (
              <button
                type="button"
                // mousedown (not click) + preventDefault keeps the textarea
                // focused so selectionStart is valid; splits the saved text at
                // the caret (drops any uncommitted draft change).
                onMouseDown={(e) => {
                  e.preventDefault();
                  const offset = taRef.current?.selectionStart ?? 0;
                  setEditing(false);
                  onSplit(offset);
                }}
                className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 hover:bg-accent hover:text-foreground"
              >
                <Scissors className="size-3" />
                Split here
              </button>
            )}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="flex gap-3">
      {chip}
      <p
        onDoubleClick={begin}
        title={editable ? "Double-click to edit" : undefined}
        className={`${textClass}${editable ? " cursor-text rounded hover:bg-accent/30" : ""}`}
      >
        {text || placeholder}
        {stale && (
          <span className="ml-2 align-middle text-[10px] font-medium uppercase tracking-wider text-amber-600 dark:text-amber-400">
            · outdated, re-translate
          </span>
        )}
      </p>
    </div>
  );
}

/**
 * Speaker label chip. Click to edit. A real label cascades the rename to ALL
 * segments by that speaker (server-side, then mirrored locally). An "unknown"
 * chip instead assigns the typed name to just *this* card's segments, so you
 * can label the bits diarization missed without touching the rest.
 */
function SpeakerChip({
  name,
  isUnknown,
  segmentIds,
  color,
}: {
  name: string;
  isUnknown: boolean;
  segmentIds: string[];
  color: string | null;
}) {
  const sessionId = useSessionStore((s) => s.sessionId);
  const applySpeakerRename = useSessionStore((s) => s.applySpeakerRename);
  const applySpeakerLabels = useSessionStore((s) => s.applySpeakerLabels);
  const setSpeakerColor = useSessionStore((s) => s.setSpeakerColor);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (editing) {
      // Unknown chips start blank (type a fresh name); real chips pre-fill.
      setDraft(isUnknown ? "" : name);
      queueMicrotask(() => inputRef.current?.select());
    }
  }, [editing, name, isUnknown]);

  const commit = async () => {
    const next = draft.trim();
    setEditing(false);
    if (!next || !sessionId) return;
    if (isUnknown) {
      // Assign just this card's segments.
      const labels = Object.fromEntries(segmentIds.map((id) => [id, next]));
      applySpeakerLabels(labels);
      try {
        await assignSpeaker(sessionId, segmentIds, next);
      } catch (e) {
        console.warn(e);
      }
    } else {
      if (next === name) return;
      applySpeakerRename(name, next);
      try {
        await renameSpeaker(sessionId, name, next);
      } catch (e) {
        applySpeakerRename(next, name); // roll back
        console.warn(e);
      }
    }
  };

  if (editing) {
    return (
      <span className="relative inline-flex items-center gap-1">
        <input
          ref={inputRef}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={commit}
          placeholder={isUnknown ? "Name this speaker" : undefined}
          onKeyDown={(e) => {
            if (e.key === "Enter") commit();
            if (e.key === "Escape") setEditing(false);
          }}
          className="h-[18px] w-28 rounded-full border border-brand-500/30 bg-background px-2 text-[10.5px] font-semibold outline-none focus:ring-2 focus:ring-brand-500/30"
        />
        {/* Explicit confirm. mousedown + preventDefault commits without the
            input's onBlur firing first (which would race the unmount). */}
        <button
          type="button"
          onMouseDown={(e) => {
            e.preventDefault();
            void commit();
          }}
          data-tip="Save (or press Enter)"
          className="inline-flex size-[18px] shrink-0 items-center justify-center rounded-full bg-brand-600 text-white transition-colors hover:bg-brand-700 dark:bg-brand-500 dark:hover:bg-brand-600"
        >
          <Check className="size-3" />
        </button>
        {/* Color picker — only for a real (named) speaker. mousedown +
            preventDefault keeps the input focused so onBlur doesn't commit
            and unmount before the swatch click registers. */}
        {!isUnknown && (
          <span className="absolute left-0 top-full z-30 mt-1 flex items-center gap-1 rounded-md border border-border bg-popover p-1.5 shadow-md">
            {SPEAKER_SWATCHES.map((c) => (
              <button
                key={c}
                type="button"
                onMouseDown={(e) => {
                  e.preventDefault();
                  setSpeakerColor(name, c);
                }}
                aria-label={`Set color ${c}`}
                className={`size-4 rounded-full transition-transform hover:scale-110 ${
                  color === c
                    ? "ring-2 ring-foreground ring-offset-1 ring-offset-popover"
                    : ""
                }`}
                style={{ backgroundColor: c }}
              />
            ))}
          </span>
        )}
      </span>
    );
  }

  return (
    <button
      onClick={() => setEditing(true)}
      data-tip={
        isUnknown
          ? "Unidentified — click to name this speaker (this segment only)"
          : "Click to rename — applies to all segments by this speaker"
      }
      className={`inline-flex items-center gap-1 rounded-full border px-1.5 py-0.5 text-[10px] font-semibold tracking-wide transition-colors ${
        isUnknown
          ? "border-dashed border-border bg-transparent text-muted-foreground hover:border-brand-500/40 hover:text-foreground"
          : "border-border bg-accent/40 text-foreground hover:border-brand-500/40 hover:bg-accent"
      }`}
    >
      {color && (
        <span
          aria-hidden
          className="size-1.5 rounded-full"
          style={{ backgroundColor: color }}
        />
      )}
      {isUnknown ? "unknown" : name}
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
