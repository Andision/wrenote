// Per-segment WAV playback. One <audio> element per session, driven by
// segment timestamps from the store. Mode-aware: "single" pauses at the
// segment boundary; "continuous" plays through, with the highlighted
// segment tracked via timeupdate.
import { useCallback, useEffect, useMemo, useRef } from "react";

import { recordingUrl } from "@/lib/recording";
import { useSessionStore } from "@/store/sessionStore";

export interface UsePlayback {
  /** Start playback at the given segment's t0. */
  play: (segmentId: string) => void;
  /** Pause (single-segment mode auto-pauses; this lets the user do it manually). */
  pause: () => void;
  /** Resume from the current playhead position. */
  resume: () => void;
  /** Jump the playhead. Useful for the master bar's scrubber. */
  seek: (seconds: number) => void;
}

export function usePlayback(): UsePlayback {
  const sessionId = useSessionStore((s) => s.sessionId);
  const segmentOrder = useSessionStore((s) => s.segmentOrder);
  const segments = useSessionStore((s) => s.segments);
  const mode = useSessionStore((s) => s.settings.playbackMode);
  const setPlayback = useSessionStore((s) => s.setPlayback);
  const setPlaybackTime = useSessionStore((s) => s.setPlaybackTime);
  const setPlaybackLevel = useSessionStore((s) => s.setPlaybackLevel);

  const audioRef = useRef<HTMLAudioElement | null>(null);
  const stopTimerRef = useRef<number | null>(null);
  // Web Audio graph for the speaker-level meter. createMediaElementSource()
  // can only be called once per HTMLAudioElement — we keep the analyser
  // alive across pause/resume and just stop the RAF loop on pause.
  const audioCtxRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const rafRef = useRef<number | null>(null);
  // Latest values, accessible from timeupdate listener without re-binding.
  const orderedSegsRef = useRef<{ id: string; t0: number; t1: number }[]>([]);
  const currentIdRef = useRef<string | null>(null);
  const modeRef = useRef(mode);

  // Build an ordered (id, t0, t1) array whenever segments change.
  const ordered = useMemo(() => {
    return segmentOrder
      .map((id) => {
        const s = segments[id];
        return s ? { id, t0: s.startedAt, t1: s.endedAt } : null;
      })
      .filter((x): x is { id: string; t0: number; t1: number } => Boolean(x))
      .sort((a, b) => a.t0 - b.t0);
  }, [segmentOrder, segments]);

  useEffect(() => {
    orderedSegsRef.current = ordered;
  }, [ordered]);

  useEffect(() => {
    modeRef.current = mode;
  }, [mode]);

  // Tear down audio when the session changes (new src needed).
  useEffect(() => {
    return () => {
      const audio = audioRef.current;
      if (audio) {
        audio.pause();
        audio.src = "";
      }
      audioRef.current = null;
      if (stopTimerRef.current) {
        window.clearTimeout(stopTimerRef.current);
        stopTimerRef.current = null;
      }
      if (rafRef.current) {
        cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }
      // Close the audio context so a re-mount on the same session id can
      // build a fresh MediaElementSource on the new <audio>.
      if (audioCtxRef.current) {
        void audioCtxRef.current.close();
        audioCtxRef.current = null;
      }
      analyserRef.current = null;
      currentIdRef.current = null;
      setPlayback(null, false);
      setPlaybackLevel(0);
    };
  }, [sessionId, setPlayback, setPlaybackLevel]);

  // Per-frame RMS sampler for the speaker meter. Started on `play`,
  // stopped on `pause` so it doesn't burn cycles when silent.
  const sampleLevel = useCallback(() => {
    const a = analyserRef.current;
    if (!a) {
      rafRef.current = null;
      return;
    }
    const buf = new Float32Array(a.fftSize);
    a.getFloatTimeDomainData(buf);
    let sum = 0;
    for (let i = 0; i < buf.length; i++) sum += buf[i] * buf[i];
    setPlaybackLevel(Math.sqrt(sum / buf.length));
    rafRef.current = requestAnimationFrame(sampleLevel);
  }, [setPlaybackLevel]);

  const ensureAudio = useCallback((): HTMLAudioElement | null => {
    if (!sessionId) return null;
    if (!audioRef.current) {
      const a = new Audio();
      // crossOrigin MUST be set before src for the WebAudio API to be
      // allowed to read sample data via MediaElementSource on a different
      // origin. Backend CORS lets http://localhost:5173 fetch the wav.
      a.crossOrigin = "anonymous";
      a.preload = "auto";
      a.src = recordingUrl(sessionId);
      a.addEventListener("loadedmetadata", () => {
        setPlaybackTime(a.currentTime || 0, a.duration || 0);
      });
      a.addEventListener("timeupdate", () => {
        // Always push the playhead to the store so the master playback
        // bar can paint a live scrubber.
        setPlaybackTime(a.currentTime, a.duration || 0);
        // In continuous mode, advance the highlighted segment as the
        // playhead crosses boundaries. (No-op in single mode — the stop
        // timer pauses us before we'd cross.)
        if (modeRef.current !== "continuous") return;
        const t = a.currentTime;
        const order = orderedSegsRef.current;
        const cur = currentIdRef.current;
        if (cur) {
          const found = order.find((s) => s.id === cur);
          if (found && t >= found.t0 && t < found.t1) return;
        }
        const next = order.find((s) => t >= s.t0 && t < s.t1);
        const nextId = next?.id ?? null;
        if (nextId !== currentIdRef.current) {
          currentIdRef.current = nextId;
          setPlayback(nextId, !a.paused);
        }
      });
      a.addEventListener("pause", () => {
        // True end of playback (audio.paused stays true and we have no
        // pending stopTimer for a single-segment auto-pause).
        setPlayback(currentIdRef.current, false);
        if (rafRef.current) {
          cancelAnimationFrame(rafRef.current);
          rafRef.current = null;
        }
        setPlaybackLevel(0);
      });
      a.addEventListener("play", () => {
        setPlayback(currentIdRef.current, true);
        // Lazy-build the analyser graph on the first user-initiated play
        // (avoids the "AudioContext not allowed to start" warning).
        if (!audioCtxRef.current) {
          try {
            const ctx = new AudioContext();
            const src = ctx.createMediaElementSource(a);
            const analyser = ctx.createAnalyser();
            analyser.fftSize = 1024;
            analyser.smoothingTimeConstant = 0.7;
            src.connect(analyser);
            analyser.connect(ctx.destination);
            audioCtxRef.current = ctx;
            analyserRef.current = analyser;
          } catch (e) {
            console.warn("speaker meter setup failed", e);
          }
        }
        if (rafRef.current == null && analyserRef.current) {
          rafRef.current = requestAnimationFrame(sampleLevel);
        }
      });
      a.addEventListener("ended", () => {
        currentIdRef.current = null;
        setPlayback(null, false);
        if (rafRef.current) {
          cancelAnimationFrame(rafRef.current);
          rafRef.current = null;
        }
        setPlaybackLevel(0);
      });
      audioRef.current = a;
    }
    return audioRef.current;
  }, [sessionId, setPlayback, setPlaybackLevel, sampleLevel]);

  const play = useCallback(
    (segmentId: string) => {
      const seg = segments[segmentId];
      if (!seg) return;
      const audio = ensureAudio();
      if (!audio) return;

      // Reset any pending single-segment auto-pause from a prior call.
      if (stopTimerRef.current) {
        window.clearTimeout(stopTimerRef.current);
        stopTimerRef.current = null;
      }

      audio.currentTime = seg.startedAt;
      currentIdRef.current = segmentId;
      setPlayback(segmentId, true);
      void audio.play().catch((e) => {
        console.warn("playback failed", e);
        setPlayback(null, false);
      });

      if (modeRef.current === "single") {
        const durMs = Math.max(0, (seg.endedAt - seg.startedAt) * 1000);
        stopTimerRef.current = window.setTimeout(() => {
          audio.pause();
          stopTimerRef.current = null;
        }, durMs);
      }
    },
    [segments, ensureAudio, setPlayback],
  );

  const pause = useCallback(() => {
    audioRef.current?.pause();
    if (stopTimerRef.current) {
      window.clearTimeout(stopTimerRef.current);
      stopTimerRef.current = null;
    }
  }, []);

  const resume = useCallback(() => {
    const audio = audioRef.current;
    if (!audio) return;
    void audio.play().catch((e) => console.warn("playback failed", e));
    if (modeRef.current === "single" && currentIdRef.current) {
      // Re-arm the auto-pause for the remainder of the current segment.
      const order = orderedSegsRef.current;
      const cur = order.find((s) => s.id === currentIdRef.current);
      if (cur) {
        const remainMs = Math.max(0, (cur.t1 - audio.currentTime) * 1000);
        if (stopTimerRef.current) window.clearTimeout(stopTimerRef.current);
        stopTimerRef.current = window.setTimeout(() => {
          audio.pause();
          stopTimerRef.current = null;
        }, remainMs);
      }
    }
  }, []);

  const seek = useCallback(
    (seconds: number) => {
      const audio = ensureAudio();
      if (!audio) return;
      const target = Math.max(0, seconds);
      audio.currentTime = target;
      // Cancel any single-mode auto-pause: seeking ≈ user took over.
      if (stopTimerRef.current) {
        window.clearTimeout(stopTimerRef.current);
        stopTimerRef.current = null;
      }
      // Update the highlighted segment immediately — don't wait for the
      // next timeupdate tick. Works in single mode too (where timeupdate
      // wouldn't otherwise advance the highlight).
      const order = orderedSegsRef.current;
      const hit = order.find((s) => target >= s.t0 && target < s.t1);
      const nextId = hit?.id ?? null;
      if (nextId !== currentIdRef.current) {
        currentIdRef.current = nextId;
        setPlayback(nextId, !audio.paused);
      }
    },
    [ensureAudio, setPlayback],
  );

  return { play, pause, resume, seek };
}
