import { AnimatePresence, motion } from "motion/react";
import {
  ChevronUp,
  Mic,
  MicOff,
  Pause,
  Play,
  Volume2,
  VolumeX,
} from "lucide-react";

import { usePlaybackControls } from "@/hooks/playbackContext";
import { useSessionStore } from "@/store/sessionStore";

export function StatusBar() {
  const micLevel = useSessionStore((s) => s.micLevel);
  const playbackLevel = useSessionStore((s) => s.playbackLevel);
  const showLevelMeters = useSessionStore((s) => s.settings.showLevelMeters);
  const connection = useSessionStore((s) => s.connection);
  const error = useSessionStore((s) => s.errorMsg);
  const playingId = useSessionStore((s) => s.playingSegmentId);
  const isPlaying = useSessionStore((s) => s.isPlaying);
  const playbackVisible = Boolean(playingId) || isPlaying;

  const isLive = connection === "recording";

  return (
    <footer className="flex h-10 shrink-0 items-center gap-3 border-t bg-card px-4 text-[11.5px] text-muted-foreground">
      {/* Left: level meters (mic always shown; speaker only while playing) */}
      {showLevelMeters && (
        <div className="flex shrink-0 items-center gap-3">
          <MicMeter level={micLevel} isLive={isLive} />
          {playbackVisible && (
            <SpeakerMeter level={playbackLevel} isPlaying={isPlaying} />
          )}
        </div>
      )}

      {/* Center: playback controls (only when audio playing/paused) */}
      <div className="flex flex-1 items-center justify-center">
        <AnimatePresence>
          {playbackVisible && <PlaybackControls />}
        </AnimatePresence>
      </div>

      {/* Right: error */}
      {error && (
        <span className="shrink-0 truncate text-destructive" title={error}>
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
  const { pause, resume, seek } = usePlaybackControls();

  const seg = playingId ? segments[playingId] : null;
  const pct = duration ? Math.min(100, Math.max(0, (current / duration) * 100)) : 0;

  const onScrub = (e: React.MouseEvent<HTMLDivElement>) => {
    if (!duration) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const ratio = (e.clientX - rect.left) / rect.width;
    // seek() also updates the highlighted segment (see usePlayback) so a
    // scrub will scroll the transcript via the auto-follow effect.
    seek(Math.max(0, Math.min(duration, ratio * duration)));
  };

  const onTogglePlay = () => {
    if (isPlaying) pause();
    else if (playingId) resume();
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
      <button
        onClick={onTogglePlay}
        title={isPlaying ? "Pause" : "Resume"}
        className="inline-flex size-6 shrink-0 items-center justify-center rounded-full bg-blue-600 text-white hover:bg-blue-700 dark:bg-blue-500 dark:hover:bg-blue-600"
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
        onClick={onScrub}
        className="relative h-1.5 min-w-0 flex-1 cursor-pointer overflow-hidden rounded-full bg-muted"
      >
        <motion.div
          className="absolute inset-y-0 left-0 rounded-full bg-blue-500"
          animate={{ width: `${pct}%` }}
          transition={{ duration: 0.12, ease: "linear" }}
        />
      </div>

      <span className="shrink-0 font-mono text-[10px] tabular-nums text-muted-foreground">
        {fmtTime(duration)}
      </span>

      {seg?.origText && (
        <button
          onClick={onJumpToCard}
          title="Scroll to this segment in the transcript"
          className="hidden min-w-0 items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground sm:inline-flex"
          style={{ maxWidth: "22ch" }}
        >
          <ChevronUp className="size-3 shrink-0" />
          <span className="truncate">{seg.origText}</span>
        </button>
      )}
    </motion.div>
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
          <Mic className="size-3.5 text-blue-600 dark:text-blue-400" />
        </motion.span>
      ) : (
        <MicOff className="size-3.5" />
      )}
      <div className="relative h-1.5 w-20 overflow-hidden rounded-full bg-muted">
        {isLive && (
          <motion.div
            aria-hidden
            className="absolute inset-0 rounded-full bg-blue-400/20"
            animate={{ opacity: [0.3, 0.7, 0.3] }}
            transition={{ duration: 1.8, repeat: Infinity, ease: "easeInOut" }}
          />
        )}
        <div
          className={`absolute inset-y-0 left-0 rounded-full transition-[width] duration-100 ${
            isLive ? "bg-blue-500" : "bg-muted-foreground/30"
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
    <div className="flex items-center gap-2">
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
