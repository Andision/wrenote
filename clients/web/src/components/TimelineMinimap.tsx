import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { motion } from "motion/react";

import { buildSpeakerPalette } from "@/lib/colors";
import { useSessionStore } from "@/store/sessionStore";
import type { Segment } from "@/types";

interface TimelineMinimapProps {
  /** The transcript scroll container we mirror. */
  scrollRef: React.RefObject<HTMLDivElement | null>;
  /** All visible segments in display order. */
  segments: Segment[];
}

/** Least time between two measurements of the card offsets. */
const MEASURE_EVERY_MS = 350;

interface Marker {
  id: string;
  /** Scroll offset (px) of the segment's card within the scroll container. */
  offsetTop: number;
  /** Session-relative start time (s) — shown in the hover tooltip. */
  t0: number;
  /** Speaker tint, or null for un-diarized segments. */
  color: string | null;
}

/**
 * Right-edge navigation rail — a "smart scrollbar" for the transcript.
 * Everything lives in scroll-space so the viewport thumb, the per-segment
 * ticks and the play marker line up exactly. Drag anywhere to scrub, click
 * to jump, hover to read the time at the cursor. Thickens on hover.
 */
export function TimelineMinimap({ scrollRef, segments }: TimelineMinimapProps) {
  const railRef = useRef<HTMLDivElement>(null);
  const [scrollTop, setScrollTop] = useState(0);
  const [scrollHeight, setScrollHeight] = useState(1);
  const [clientHeight, setClientHeight] = useState(1);
  const [markers, setMarkers] = useState<Marker[]>([]);
  const [hoverY, setHoverY] = useState<number | null>(null);
  const [dragging, setDragging] = useState(false);

  const playingId = useSessionStore((s) => s.playingSegmentId);

  const segMap = useMemo(() => {
    const m = new Map<string, Segment>();
    for (const s of segments) m.set(s.segmentId, s);
    return m;
  }, [segments]);

  // Same palette as the transcript cards, so a tick and its card agree —
  // computed colors with the user's per-speaker overrides applied on top.
  const colorOverrides = useSessionStore((s) => s.speakerColors);
  const palette = useMemo(() => {
    const p = buildSpeakerPalette(segments.map((s) => s.speaker));
    for (const [label, color] of Object.entries(colorOverrides)) {
      if (p.has(label)) p.set(label, color);
    }
    return p;
  }, [segments, colorOverrides]);

  // Track the transcript's scroll geometry.
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const update = () => {
      setScrollTop(el.scrollTop);
      setScrollHeight(Math.max(1, el.scrollHeight));
      setClientHeight(Math.max(1, el.clientHeight));
    };
    update();
    el.addEventListener("scroll", update, { passive: true });
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => {
      el.removeEventListener("scroll", update);
      ro.disconnect();
    };
  }, [scrollRef]);

  // Measure each card's scroll offset (one tick per card). Re-runs when the
  // segment set or layout changes. Streaming changes the set every ~800 ms
  // and each measure reads every card's offset, so measures are coalesced
  // to one per MEASURE_EVERY_MS at most — a tick a third of a second late
  // is invisible; a layout pass per partial on a two-hour transcript is not.
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    let raf = 0;
    let timer = 0;
    let last = 0;
    const measure = () => {
      const cards = el.querySelectorAll<HTMLElement>("[data-segment-ids]");
      const out: Marker[] = [];
      cards.forEach((card) => {
        const id = (card.dataset.segmentIds || "").split(" ")[0];
        if (!id) return;
        const seg = segMap.get(id);
        out.push({
          id,
          offsetTop: card.offsetTop,
          t0: seg?.startedAt ?? 0,
          color: seg?.speaker ? palette.get(seg.speaker) ?? null : null,
        });
      });
      setScrollHeight(Math.max(1, el.scrollHeight));
      setMarkers(out);
    };
    const run = () => {
      last = performance.now();
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(measure);
    };
    const schedule = () => {
      window.clearTimeout(timer);
      const wait = Math.max(0, MEASURE_EVERY_MS - (performance.now() - last));
      timer = window.setTimeout(run, wait);
    };
    schedule();
    const ro = new ResizeObserver(schedule);
    ro.observe(el);
    return () => {
      window.clearTimeout(timer);
      cancelAnimationFrame(raf);
      ro.disconnect();
    };
  }, [scrollRef, segMap, palette]);

  // Map a clientY on the rail to a scroll position (centered on the cursor).
  const scrollToClientY = useCallback(
    (clientY: number) => {
      const el = scrollRef.current;
      const rail = railRef.current;
      if (!el || !rail) return;
      const rect = rail.getBoundingClientRect();
      const ratio = Math.min(1, Math.max(0, (clientY - rect.top) / rect.height));
      const target = ratio * el.scrollHeight - el.clientHeight / 2;
      el.scrollTop = Math.max(0, Math.min(el.scrollHeight - el.clientHeight, target));
    },
    [scrollRef],
  );

  const onPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.currentTarget.setPointerCapture(e.pointerId);
    setDragging(true);
    scrollToClientY(e.clientY);
  };
  const onPointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    const rect = railRef.current?.getBoundingClientRect();
    if (rect) setHoverY(e.clientY - rect.top);
    if (dragging) scrollToClientY(e.clientY);
  };
  const onPointerUp = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!dragging) return;
    setDragging(false);
    e.currentTarget.releasePointerCapture(e.pointerId);
  };
  const onPointerLeave = () => {
    if (!dragging) setHoverY(null);
  };

  // Viewport thumb position.
  const thumbTop = (scrollTop / scrollHeight) * 100;
  const thumbHeight = (clientHeight / scrollHeight) * 100;

  // Currently-playing segment marker (playingId may be a merged-into id, so
  // fall back to a direct DOM lookup if it's not a card's first id).
  const playPct = useMemo(() => {
    if (!playingId) return null;
    const hit = markers.find((m) => m.id === playingId);
    if (hit) return (hit.offsetTop / scrollHeight) * 100;
    const el = scrollRef.current?.querySelector<HTMLElement>(
      `[data-segment-ids~="${playingId}"]`,
    );
    return el ? (el.offsetTop / scrollHeight) * 100 : null;
  }, [playingId, markers, scrollHeight, scrollRef]);

  // The segment nearest the hovered position — drives the time + text preview.
  const hoverInfo = useMemo(() => {
    const rail = railRef.current;
    if (hoverY == null || !rail || markers.length === 0) return null;
    const contentY = (hoverY / rail.clientHeight) * scrollHeight;
    let best = markers[0];
    let bestD = Infinity;
    for (const m of markers) {
      const d = Math.abs(m.offsetTop - contentY);
      if (d < bestD) {
        bestD = d;
        best = m;
      }
    }
    const seg = segMap.get(best.id);
    return {
      t0: best.t0,
      text: seg?.origText ?? "",
      speaker: seg?.speaker && seg.speaker !== "unknown" ? seg.speaker : null,
    };
  }, [hoverY, scrollHeight, markers, segMap]);

  if (segments.length === 0) return null;

  return (
    // Hit area is wider than the visual rail so it's easy to grab; visuals
    // anchor to the right edge, the rest is transparent padding for clicks.
    <div
      ref={railRef}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerLeave={onPointerLeave}
      className={`group/mm absolute right-0 top-0 z-10 h-full w-12 touch-none select-none ${
        dragging ? "cursor-grabbing" : "cursor-pointer"
      }`}
    >
      {/* Spine — everything below shares the same center axis (~28px from edge) */}
      <div className="absolute inset-y-1 right-[26px] w-1 rounded-full bg-border transition-colors group-hover/mm:bg-muted-foreground/40" />

      {/* Per-segment ticks, tinted by speaker */}
      {markers.map((m) => (
        <div
          key={m.id}
          style={{
            top: `${(m.offsetTop / scrollHeight) * 100}%`,
            backgroundColor: m.color ?? undefined,
          }}
          className={`absolute right-5 h-[3px] w-4 -translate-y-1/2 rounded-full transition-opacity ${
            m.color
              ? "opacity-70 group-hover/mm:opacity-100"
              : "bg-muted-foreground/40 group-hover/mm:bg-muted-foreground/70"
          }`}
        />
      ))}

      {/* Viewport thumb. Width and center axis stay constant (scaleX is
          anchored at the element's own center, so the grab "pop" widens it
          without nudging the axis the ticks share). On drag: brighter fill,
          a thicker ring and a soft glow — pure color/shadow, no layout. */}
      <motion.div
        className="absolute right-5 w-4 rounded-full bg-brand-500/30 ring-1 ring-inset ring-brand-500/50 transition-[background-color,box-shadow,--tw-ring-color] duration-150 group-hover/mm:bg-brand-500/45 group-hover/mm:ring-brand-500/70 data-[drag=true]:bg-brand-500/65 data-[drag=true]:ring-2 data-[drag=true]:ring-brand-500/80 data-[drag=true]:shadow-[0_0_12px_2px_rgba(158,111,69,0.5)]"
        data-drag={dragging}
        style={{ transformOrigin: "center" }}
        animate={{
          top: `${thumbTop}%`,
          height: `${Math.max(4, thumbHeight)}%`,
          scaleX: dragging ? 1.45 : 1,
        }}
        transition={{
          top: { duration: dragging ? 0 : 0.12, ease: "linear" },
          height: { duration: dragging ? 0 : 0.12, ease: "linear" },
          scaleX: { type: "spring", stiffness: 500, damping: 30 },
        }}
      />

      {/* Current-play marker */}
      {playPct != null && (
        <motion.div
          className="absolute right-[18px] h-0.5 w-5 -translate-y-1/2 rounded-full bg-brand-600 shadow-[0_0_6px_rgba(158,111,69,0.6)] dark:bg-brand-400"
          animate={{ top: `${playPct}%` }}
          transition={{ duration: 0.18, ease: "easeOut" }}
        />
      )}

      {/* Hover preview: time + the dialogue at that position */}
      {hoverInfo && hoverY != null && (
        <div
          className="pointer-events-none absolute right-12 z-20 w-56 -translate-y-1/2 rounded-lg border border-border bg-popover px-2.5 py-1.5 shadow-lg"
          style={{ top: `${hoverY}px` }}
        >
          <div className="flex items-center gap-1.5">
            <span className="font-mono text-[10px] tabular-nums text-muted-foreground">
              {fmt(hoverInfo.t0)}
            </span>
            {hoverInfo.speaker && (
              <span className="truncate text-[10px] font-medium text-foreground">
                {hoverInfo.speaker}
              </span>
            )}
          </div>
          {hoverInfo.text && (
            <p className="mt-0.5 line-clamp-3 text-[11px] leading-snug text-popover-foreground">
              {hoverInfo.text}
            </p>
          )}
        </div>
      )}
    </div>
  );
}

function fmt(s: number): string {
  if (!isFinite(s) || s < 0) s = 0;
  const total = Math.floor(s);
  const m = Math.floor(total / 60);
  const sec = total % 60;
  return `${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
}
