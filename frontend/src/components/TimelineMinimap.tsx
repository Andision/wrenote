import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { motion } from "motion/react";

import type { Segment } from "@/types";

interface TimelineMinimapProps {
  /** The transcript scroll container we mirror. */
  scrollRef: React.RefObject<HTMLDivElement | null>;
  /** All visible segments in display order. */
  segments: Segment[];
}

/**
 * Thin vertical rail on the right edge of the transcript with a moving
 * indicator showing the visible range, density dots per segment, and a
 * hover tooltip with the time at the cursor. Click jumps the transcript
 * to that position.
 */
export function TimelineMinimap({ scrollRef, segments }: TimelineMinimapProps) {
  const railRef = useRef<HTMLDivElement>(null);
  const [scrollTop, setScrollTop] = useState(0);
  const [scrollHeight, setScrollHeight] = useState(1);
  const [clientHeight, setClientHeight] = useState(1);
  const [hoverY, setHoverY] = useState<number | null>(null);

  // Subscribe to the transcript scroll position.
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

  // Density dots: one per segment, placed at its proportional position.
  const dots = useMemo(() => {
    const totalTime = segments.reduce(
      (m, s) => Math.max(m, s.endedAt || s.startedAt),
      0,
    );
    if (totalTime <= 0) return [];
    return segments.map((s) => ({
      id: s.segmentId,
      pct: Math.min(100, Math.max(0, (s.startedAt / totalTime) * 100)),
    }));
  }, [segments]);

  // Tick labels: one per minute, but capped so we don't draw thousands.
  const ticks = useMemo(() => {
    const totalTime = segments.reduce(
      (m, s) => Math.max(m, s.endedAt || s.startedAt),
      0,
    );
    if (totalTime < 60) return [];
    const totalMin = Math.ceil(totalTime / 60);
    const stepMin = totalMin <= 10 ? 1 : totalMin <= 30 ? 5 : 10;
    const out: { pct: number; label: string }[] = [];
    for (let m = stepMin; m <= totalMin; m += stepMin) {
      const t = m * 60;
      if (t > totalTime) break;
      out.push({
        pct: (t / totalTime) * 100,
        label: m >= 60 ? `${Math.floor(m / 60)}h` : `${m}m`,
      });
    }
    return out;
  }, [segments]);

  // Visible-range band positions.
  const viewTopPct = (scrollTop / scrollHeight) * 100;
  const viewHeightPct = (clientHeight / scrollHeight) * 100;

  const onMouseMove = (e: React.MouseEvent<HTMLDivElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    setHoverY(e.clientY - rect.top);
  };

  const onMouseLeave = () => setHoverY(null);

  const onClick = useCallback(
    (e: React.MouseEvent<HTMLDivElement>) => {
      const el = scrollRef.current;
      if (!el) return;
      const rect = e.currentTarget.getBoundingClientRect();
      const ratio = (e.clientY - rect.top) / rect.height;
      el.scrollTo({
        top: ratio * (el.scrollHeight - el.clientHeight),
        behavior: "smooth",
      });
    },
    [scrollRef],
  );

  // Hover tooltip time, derived from mouse Y over the rail.
  const totalTime = segments.reduce(
    (m, s) => Math.max(m, s.endedAt || s.startedAt),
    0,
  );
  const hoverPct = (() => {
    const rail = railRef.current;
    if (!rail || hoverY == null) return null;
    return Math.min(100, Math.max(0, (hoverY / rail.clientHeight) * 100));
  })();
  const hoverTime =
    hoverPct != null && totalTime > 0 ? (hoverPct / 100) * totalTime : null;

  // No content yet? Don't render the rail.
  if (segments.length === 0 || totalTime <= 0) return null;

  return (
    // Hit area is wider than the visual rail so the rail is easy to grab.
    // All visual elements anchor to the *right* edge; the extra width on
    // the left is empty padding for clicks.
    <div
      ref={railRef}
      onMouseMove={onMouseMove}
      onMouseLeave={onMouseLeave}
      onClick={onClick}
      className="group/mm absolute right-0 top-0 z-10 h-full w-12 cursor-pointer"
    >
      {/* Spine — thickens on hover so the user sees what they're aiming at. */}
      <div className="absolute right-3.5 top-2 bottom-2 w-[2px] rounded-full bg-border transition-colors group-hover/mm:bg-muted-foreground/40" />

      {/* Density dots */}
      {dots.map((d) => (
        <div
          key={d.id}
          style={{ top: `calc(${d.pct}% + 4px)` }}
          className="absolute right-[9px] size-2 -translate-y-1/2 rounded-full bg-muted-foreground/40 transition-colors group-hover/mm:bg-muted-foreground/70"
        />
      ))}

      {/* Minute / hour ticks */}
      {ticks.map((t, i) => (
        <div
          key={i}
          style={{ top: `calc(${t.pct}% + 4px)` }}
          className="pointer-events-none absolute right-5 -translate-y-1/2 text-[9px] tabular-nums text-muted-foreground/60"
        >
          {t.label}
        </div>
      ))}

      {/* Visible-range band */}
      <motion.div
        className="absolute right-2 w-1.5 rounded-full bg-blue-500/70 shadow-[0_0_0_1px_rgba(59,130,246,0.2)]"
        animate={{
          top: `calc(${viewTopPct}% + 4px)`,
          height: `${viewHeightPct}%`,
        }}
        transition={{ duration: 0.12, ease: "linear" }}
      />

      {/* Hover tooltip */}
      {hoverTime != null && (
        <div
          className="pointer-events-none absolute right-9 z-20 -translate-y-1/2 rounded-md border border-border bg-popover px-1.5 py-0.5 font-mono text-[10px] tabular-nums text-popover-foreground shadow-md"
          style={{ top: `${hoverY ?? 0}px` }}
        >
          {fmt(hoverTime)}
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
