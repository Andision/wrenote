import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import {
  Check,
  ChevronUp,
  Gauge,
  Mic,
  MicOff,
  Pause,
  Play,
  Repeat1,
  Volume2,
  VolumeX,
} from "lucide-react";

import { usePlaybackControls } from "@/hooks/playbackContext";
import { useSessionStore } from "@/store/sessionStore";

export function StatusBar() {
  const micLevel = useSessionStore((s) => s.micLevel);
  const showLevelMeters = useSessionStore((s) => s.settings.showLevelMeters);
  const connection = useSessionStore((s) => s.connection);
  const error = useSessionStore((s) => s.errorMsg);
  const segmentCount = useSessionStore((s) => s.segmentOrder.length);
  // The transport is a fixture of any finished session — visible the moment
  // you stop recording, not only once you've hit play. Hidden while live.
  const canPlayback =
    segmentCount > 0 &&
    (connection === "disconnected" || connection === "error");

  const isLive = connection === "recording";
  // The mic meter is a fixture of the whole capture lifecycle — present from
  // the new-session/pre-flight state through recording, so it never pops into
  // the layout the instant you hit record. A finished session swaps it for
  // the playback transport (which carries its own speaker meter) instead.
  const showMic = !canPlayback;

  return (
    <footer className="flex h-10 shrink-0 items-center gap-3 border-t bg-card px-4 text-[11.5px] text-muted-foreground">
      {/* Left: mic meter — only while capturing. */}
      {showLevelMeters && showMic && (
        <div className="flex shrink-0 items-center gap-3">
          <MicMeter level={micLevel} isLive={isLive} />
        </div>
      )}

      {/* Center: playback transport — persistent on any finished session. */}
      <div className="flex flex-1 items-center justify-center">
        <AnimatePresence>
          {canPlayback && <PlaybackControls />}
        </AnimatePresence>
      </div>

      {/* Right: error */}
      {error && (
        <span className="shrink-0 truncate text-destructive" data-tip={error}>
          ⚠ {error}
        </span>
      )}
    </footer>
  );
}

function PlaybackControls() {
  const playingId = useSessionStore((s) => s.playingSegmentId);
  const isPlaying = useSessionStore((s) => s.isPlaying);
  const current = useSessionStore((s) => s.playbackCurrentTime);
  const duration = useSessionStore((s) => s.playbackDuration);
  const segments = useSessionStore((s) => s.segments);
  const sessionId = useSessionStore((s) => s.sessionId);
  const playbackLevel = useSessionStore((s) => s.playbackLevel);
  const showLevelMeters = useSessionStore((s) => s.settings.showLevelMeters);
  const loopA = useSessionStore((s) => s.loopA);
  const loopB = useSessionStore((s) => s.loopB);
  const { pause, resume, seek, prime } = usePlaybackControls();

  // Build the <audio> as soon as the transport appears so the total time is
  // populated before the first play (re-primes when the session changes).
  useEffect(() => {
    prime();
  }, [prime, sessionId]);

  const seg = playingId ? segments[playingId] : null;
  const pct = duration ? Math.min(100, Math.max(0, (current / duration) * 100)) : 0;

  const barRef = useRef<HTMLDivElement>(null);
  const [scrubbing, setScrubbing] = useState(false);

  // Map a clientX on the bar to a seek. seek() also moves the highlighted
  // segment (see usePlayback), so scrubbing scrolls the transcript too.
  const seekToClientX = (clientX: number) => {
    const el = barRef.current;
    if (!el || !duration) return;
    const rect = el.getBoundingClientRect();
    const ratio = (clientX - rect.left) / rect.width;
    seek(Math.max(0, Math.min(duration, ratio * duration)));
  };

  const onBarPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!duration) return;
    e.preventDefault();
    e.currentTarget.setPointerCapture(e.pointerId);
    setScrubbing(true);
    seekToClientX(e.clientX);
  };
  const onBarPointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    if (scrubbing) seekToClientX(e.clientX);
  };
  const onBarPointerUp = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!scrubbing) return;
    setScrubbing(false);
    e.currentTarget.releasePointerCapture(e.pointerId);
  };

  const onTogglePlay = () => {
    if (isPlaying) pause();
    else if (playingId) resume();
    // Nothing cued yet — start from the top of the recording.
    else {
      seek(0);
      resume();
    }
  };

  const onJumpToCard = () => {
    if (!playingId) return;
    const el = document.querySelector<HTMLElement>(
      `[data-segment-ids~="${playingId}"]`,
    );
    if (el) el.scrollIntoView({ behavior: "smooth", block: "center" });
  };

  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: 8 }}
      transition={{ duration: 0.18 }}
      className="flex w-full max-w-[720px] items-center gap-2.5"
    >
      {showLevelMeters && (
        <SpeakerMeter level={playbackLevel} isPlaying={isPlaying} />
      )}

      <button
        onClick={onTogglePlay}
        data-tip={isPlaying ? "Pause" : playingId ? "Resume" : "Play from start"}
        className="inline-flex size-6 shrink-0 items-center justify-center rounded-full bg-brand-600 text-white hover:bg-brand-700 dark:bg-brand-500 dark:hover:bg-brand-600"
      >
        {isPlaying ? (
          <Pause className="size-3 fill-current" />
        ) : (
          <Play className="size-3 fill-current" />
        )}
      </button>

      <span className="shrink-0 font-mono text-[10px] tabular-nums text-muted-foreground">
        {fmtTime(current)}
      </span>

      <div
        ref={barRef}
        onPointerDown={onBarPointerDown}
        onPointerMove={onBarPointerMove}
        onPointerUp={onBarPointerUp}
        className={`group/bar relative h-1.5 min-w-0 flex-1 touch-none rounded-full bg-muted ${
          scrubbing ? "cursor-grabbing" : "cursor-pointer"
        }`}
      >
        <div className="pointer-events-none absolute inset-0 overflow-hidden rounded-full">
          <motion.div
            className="absolute inset-y-0 left-0 rounded-full bg-brand-500"
            animate={{ width: `${pct}%` }}
            transition={{ duration: scrubbing ? 0 : 0.12, ease: "linear" }}
          />
        </div>
        {/* A–B loop region + endpoint marks (drawn over the fill). */}
        {duration > 0 && loopA != null && loopB != null && (
          <div
            className="pointer-events-none absolute inset-y-0 rounded-full bg-brand-600/25"
            style={{
              left: `${(Math.min(loopA, loopB) / duration) * 100}%`,
              width: `${(Math.abs(loopB - loopA) / duration) * 100}%`,
            }}
          />
        )}
        {duration > 0 && loopA != null && (
          <span
            className="pointer-events-none absolute top-1/2 h-2.5 w-0.5 -translate-x-1/2 -translate-y-1/2 rounded-full bg-brand-600 dark:bg-brand-400"
            style={{ left: `${(loopA / duration) * 100}%` }}
          />
        )}
        {duration > 0 && loopB != null && (
          <span
            className="pointer-events-none absolute top-1/2 h-2.5 w-0.5 -translate-x-1/2 -translate-y-1/2 rounded-full bg-brand-600 dark:bg-brand-400"
            style={{ left: `${(loopB / duration) * 100}%` }}
          />
        )}
        {/* Grab handle at the playhead, enlarging while scrubbing. */}
        {duration > 0 && (
          <span
            className={`pointer-events-none absolute top-1/2 -translate-x-1/2 -translate-y-1/2 rounded-full bg-brand-600 shadow-sm transition-[width,height] dark:bg-brand-400 ${
              scrubbing ? "size-3" : "size-2 opacity-0 group-hover/bar:opacity-100"
            }`}
            style={{ left: `${pct}%` }}
          />
        )}
      </div>

      <span className="shrink-0 font-mono text-[10px] tabular-nums text-muted-foreground">
        {fmtTime(duration)}
      </span>

      <LoopControls current={current} />

      <SpeedControl />

      {/* Fixed-width slot for the now-playing snippet. Always reserved (even
          when empty) and never sized to the text, so the scrubber above keeps
          a constant width as the playing segment changes. */}
      <div className="hidden w-[20ch] shrink-0 sm:block">
        {seg?.origText && (
          <button
            onClick={onJumpToCard}
            data-tip="Scroll to this segment in the transcript"
            className="flex w-full min-w-0 items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground"
          >
            <ChevronUp className="size-3 shrink-0" />
            <span className="truncate">{seg.origText}</span>
          </button>
        )}
      </div>
    </motion.div>
  );
}

/**
 * Loop controls: a segment-repeat toggle and an A–B loop. A–B cycles
 * idle → (set A) → (set B) → active → cleared, each click capturing the
 * current playhead. The actual looping is enforced in usePlayback.
 */
function LoopControls({ current }: { current: number }) {
  const loopMode = useSessionStore((s) => s.loopMode);
  const loopA = useSessionStore((s) => s.loopA);
  const loopB = useSessionStore((s) => s.loopB);
  const setLoopMode = useSessionStore((s) => s.setLoopMode);
  const setLoopA = useSessionStore((s) => s.setLoopA);
  const setLoopB = useSessionStore((s) => s.setLoopB);

  const segmentOn = loopMode === "segment";
  const abActive = loopMode === "ab" && loopA != null && loopB != null;
  const abAwaiting = loopMode === "ab" && loopA != null && loopB == null;

  const toggleSegment = () => {
    if (segmentOn) {
      setLoopMode("off");
    } else {
      setLoopA(null);
      setLoopB(null);
      setLoopMode("segment");
    }
  };

  const cycleAB = () => {
    if (loopMode !== "ab") {
      setLoopA(current);
      setLoopB(null);
      setLoopMode("ab");
    } else if (loopB == null) {
      setLoopB(current);
    } else {
      setLoopMode("off");
      setLoopA(null);
      setLoopB(null);
    }
  };

  return (
    <div className="flex shrink-0 items-center gap-0.5">
      <button
        onClick={toggleSegment}
        aria-pressed={segmentOn}
        data-tip="Loop the current segment"
        className={`inline-flex size-6 items-center justify-center rounded-md transition-colors ${
          segmentOn
            ? "bg-brand-500/15 text-brand-600 dark:text-brand-400"
            : "text-muted-foreground hover:bg-accent hover:text-foreground"
        }`}
      >
        <Repeat1 className="size-3.5" />
      </button>
      <button
        onClick={cycleAB}
        aria-pressed={abActive}
        data-tip={
          abActive
            ? "A–B loop on — click to clear"
            : abAwaiting
              ? "Click to set point B"
              : "A–B loop — click to set point A"
        }
        className={`inline-flex h-6 w-[2.5rem] items-center justify-center rounded-md text-[10px] font-bold tabular-nums transition-colors ${
          abActive || abAwaiting
            ? "bg-brand-500/15 text-brand-600 dark:text-brand-400"
            : "text-muted-foreground hover:bg-accent hover:text-foreground"
        }`}
      >
        {abAwaiting ? "B?" : "A–B"}
      </button>
    </div>
  );
}

const SPEEDS = [2, 1.5, 1, 0.75, 0.5] as const;

/**
 * Playback-speed picker. A compact "1×" button that opens an upward menu of
 * gears. The chosen rate lives in the store and is applied to the <audio>
 * element by usePlayback, so it sticks across segment changes.
 */
function SpeedControl() {
  const rate = useSessionStore((s) => s.playbackRate);
  const setRate = useSessionStore((s) => s.setPlaybackRate);
  const [open, setOpen] = useState(false);

  return (
    <div className="relative shrink-0">
      <button
        onClick={() => setOpen((o) => !o)}
        data-tip="Playback speed"
        aria-haspopup="menu"
        aria-expanded={open}
        className={`inline-flex h-6 w-[3.25rem] shrink-0 items-center justify-center gap-1 rounded-md px-1 font-mono text-[10px] font-semibold tabular-nums transition-colors ${
          rate !== 1 || open
            ? "bg-brand-500/10 text-brand-600 dark:text-brand-400"
            : "text-muted-foreground hover:bg-accent hover:text-foreground"
        }`}
      >
        <Gauge className="size-3" />
        {rate}×
      </button>

      {open && (
        <>
          {/* Click-away backdrop. */}
          <div className="fixed inset-0 z-10" onClick={() => setOpen(false)} />
          <motion.div
            role="menu"
            initial={{ opacity: 0, y: 4, scale: 0.96 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            transition={{ duration: 0.14, ease: "easeOut" }}
            className="absolute bottom-full right-0 z-20 mb-1.5 min-w-[4.5rem] overflow-hidden rounded-lg border border-border bg-popover p-1 shadow-lg"
          >
            {SPEEDS.map((r) => {
              const active = r === rate;
              return (
                <button
                  key={r}
                  role="menuitemradio"
                  aria-checked={active}
                  onClick={() => {
                    setRate(r);
                    setOpen(false);
                  }}
                  className={`flex w-full items-center justify-between gap-2 rounded-md px-2 py-1 font-mono text-[11px] tabular-nums transition-colors ${
                    active
                      ? "bg-brand-500/10 font-semibold text-brand-600 dark:text-brand-400"
                      : "text-popover-foreground hover:bg-accent"
                  }`}
                >
                  {r}×
                  {active && <Check className="size-3" />}
                </button>
              );
            })}
          </motion.div>
        </>
      )}
    </div>
  );
}

function fmtTime(s: number): string {
  if (!isFinite(s) || s < 0) s = 0;
  const total = Math.floor(s);
  const m = Math.floor(total / 60);
  const sec = total % 60;
  return `${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
}

function MicMeter({ level, isLive }: { level: number; isLive: boolean }) {
  // 0–0.25 RMS maps to 0–100%.
  const pct = Math.min(100, level * 400);
  return (
    <div className="flex items-center gap-2">
      {isLive ? (
        <motion.span
          className="inline-flex"
          animate={{ scale: [1, 1.12, 1], opacity: [0.85, 1, 0.85] }}
          transition={{ duration: 1.4, repeat: Infinity, ease: "easeInOut" }}
        >
          <Mic className="size-3.5 text-brand-600 dark:text-brand-400" />
        </motion.span>
      ) : (
        <MicOff className="size-3.5" />
      )}
      <div className="relative h-1.5 w-20 overflow-hidden rounded-full bg-muted">
        {isLive && (
          <motion.div
            aria-hidden
            className="absolute inset-0 rounded-full bg-brand-400/20"
            animate={{ opacity: [0.3, 0.7, 0.3] }}
            transition={{ duration: 1.8, repeat: Infinity, ease: "easeInOut" }}
          />
        )}
        <div
          className={`absolute inset-y-0 left-0 rounded-full transition-[width] duration-100 ${
            isLive ? "bg-brand-500" : "bg-muted-foreground/30"
          }`}
          style={{ width: `${pct.toFixed(1)}%` }}
        />
      </div>
    </div>
  );
}

function SpeakerMeter({ level, isPlaying }: { level: number; isPlaying: boolean }) {
  // Playback RMS tends to be louder than mic — bump the scale a little.
  const pct = Math.min(100, level * 250);
  return (
    <div className="flex shrink-0 items-center gap-2">
      {isPlaying ? (
        <Volume2 className="size-3.5 text-emerald-600 dark:text-emerald-400" />
      ) : (
        <VolumeX className="size-3.5" />
      )}
      <div className="relative h-1.5 w-20 overflow-hidden rounded-full bg-muted">
        <div
          className={`absolute inset-y-0 left-0 rounded-full transition-[width] duration-100 ${
            isPlaying ? "bg-emerald-500" : "bg-muted-foreground/30"
          }`}
          style={{ width: `${pct.toFixed(1)}%` }}
        />
      </div>
    </div>
  );
}
