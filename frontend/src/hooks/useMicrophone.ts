// Microphone capture hook. Requests user media, sets up the AudioWorklet that
// downsamples to 16 kHz int16 PCM, and feeds each ~100 ms chunk to the
// provided callback. Also reports RMS levels via the store.
import { useCallback, useRef } from "react";

import { PCM_RECORDER_SOURCE } from "../lib/audio-worklet";
import { useSessionStore } from "../store/sessionStore";

interface UseMicrophoneOptions {
  onPcm: (chunk: ArrayBuffer) => void;
}

export function useMicrophone({ onPcm }: UseMicrophoneOptions) {
  const audioCtxRef = useRef<AudioContext | null>(null);
  const workletRef = useRef<AudioWorkletNode | null>(null);
  const muteGainRef = useRef<GainNode | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  // Pause gate: when true, mic stays alive (no re-permission prompt on
  // resume) but PCM chunks are dropped before reaching the WS.
  const pausedRef = useRef(false);

  const start = useCallback(async () => {
    if (audioCtxRef.current) return;
    if (!window.isSecureContext) {
      throw new Error(
        "Not a secure context — open via http://localhost or http://127.0.0.1, not file://",
      );
    }

    const micDeviceId = useSessionStore.getState().settings.micDeviceId;
    const baseAudio: MediaTrackConstraints = {
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
    };
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: micDeviceId ? { ...baseAudio, deviceId: { exact: micDeviceId } } : baseAudio,
      });
    } catch (e) {
      // Chosen device gone (unplugged) → fall back to the system default.
      if (micDeviceId && (e as Error).name === "OverconstrainedError") {
        try {
          stream = await navigator.mediaDevices.getUserMedia({ audio: baseAudio });
        } catch (e2) {
          throw new Error(`Microphone unavailable: ${(e2 as Error).message}`);
        }
      } else {
        throw new Error(`Microphone permission denied or unavailable: ${(e as Error).message}`);
      }
    }
    streamRef.current = stream;

    const audioCtx = new AudioContext();
    audioCtxRef.current = audioCtx;
    if (audioCtx.state === "suspended") await audioCtx.resume();

    const blobUrl = URL.createObjectURL(
      new Blob([PCM_RECORDER_SOURCE], { type: "application/javascript" }),
    );
    await audioCtx.audioWorklet.addModule(blobUrl);

    const node = new AudioWorkletNode(audioCtx, "pcm-recorder");
    workletRef.current = node;

    node.port.onmessage = (ev) => {
      const m = ev.data as { kind: "pcm"; buf: ArrayBuffer } | { kind: "rms"; value: number };
      if (pausedRef.current) {
        // Still flush the level meter to 0 so the UI reads "muted".
        if (m.kind === "rms") useSessionStore.getState().setMicLevel(0);
        return;
      }
      if (m.kind === "pcm") {
        onPcm(m.buf);
      } else if (m.kind === "rms") {
        useSessionStore.getState().setMicLevel(m.value);
      }
    };

    // Connect mic → worklet → mute-gain → destination. The mute gain is
    // necessary because some browsers don't run the worklet's process() if
    // its output isn't wired to a terminal node.
    const source = audioCtx.createMediaStreamSource(stream);
    const muteGain = audioCtx.createGain();
    muteGain.gain.value = 0;
    source.connect(node);
    node.connect(muteGain);
    muteGain.connect(audioCtx.destination);
    muteGainRef.current = muteGain;
  }, [onPcm]);

  const stop = useCallback(() => {
    try {
      workletRef.current?.disconnect();
    } catch {
      /* ignore */
    }
    try {
      muteGainRef.current?.disconnect();
    } catch {
      /* ignore */
    }
    try {
      audioCtxRef.current?.close();
    } catch {
      /* ignore */
    }
    if (streamRef.current) {
      for (const track of streamRef.current.getTracks()) track.stop();
    }
    workletRef.current = null;
    muteGainRef.current = null;
    audioCtxRef.current = null;
    streamRef.current = null;
    useSessionStore.getState().setMicLevel(0);
  }, []);

  const pause = useCallback(() => {
    pausedRef.current = true;
    useSessionStore.getState().setMicLevel(0);
  }, []);

  const resume = useCallback(() => {
    pausedRef.current = false;
  }, []);

  return { start, stop, pause, resume };
}
