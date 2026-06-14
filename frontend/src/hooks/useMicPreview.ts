// PreFlight-only microphone preview: enumerate input devices and report a live
// RMS level so the user can confirm "my mic is picking up sound" before hitting
// record. Deliberately separate from useMicrophone (the recording path with the
// PCM worklet + WS feed) — this runs only while idle on PreFlight and tears down
// the instant recording starts, so the two never hold the device at once.
import { useEffect, useRef, useState } from "react";

import { listAudioInputs } from "@/lib/devices";

export function useMicPreview(deviceId: string, enabled: boolean) {
  const [devices, setDevices] = useState<MediaDeviceInfo[]>([]);
  const [level, setLevel] = useState(0);
  const rafRef = useRef<number | null>(null);

  // Device list (+ refresh on hot-plug). Labels stay blank until mic permission
  // is granted; the preview stream below grants it and re-enumerates.
  useEffect(() => {
    const refresh = () => void listAudioInputs().then(setDevices);
    refresh();
    navigator.mediaDevices?.addEventListener?.("devicechange", refresh);
    return () => navigator.mediaDevices?.removeEventListener?.("devicechange", refresh);
  }, []);

  // Live level meter: open a preview stream on the chosen device, restart when
  // the device changes, stop entirely when disabled (recording about to start).
  useEffect(() => {
    // Disabled / unsupported: leave the level alone. It's 0 initially, and the
    // cleanup below resets it on every enabled→disabled transition, so there's
    // no synchronous setState in the effect body to trigger cascading renders.
    if (!enabled || typeof navigator === "undefined" || !navigator.mediaDevices?.getUserMedia) {
      return;
    }
    let cancelled = false;
    let stream: MediaStream | null = null;
    let ctx: AudioContext | null = null;

    void (async () => {
      try {
        stream = await navigator.mediaDevices.getUserMedia({
          audio: deviceId ? { deviceId: { exact: deviceId } } : true,
        });
      } catch {
        // Chosen device gone (unplugged) → fall back to default so the meter
        // still works; if that fails too, just leave the level at 0.
        try {
          stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        } catch {
          return;
        }
      }
      if (cancelled || !stream) {
        stream?.getTracks().forEach((t) => t.stop());
        return;
      }
      // Permission is now granted → device labels are available, re-enumerate.
      void listAudioInputs().then(setDevices);

      ctx = new AudioContext();
      if (ctx.state === "suspended") await ctx.resume();
      const src = ctx.createMediaStreamSource(stream);
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 512;
      src.connect(analyser);
      const buf = new Float32Array(analyser.fftSize);

      const tick = () => {
        analyser.getFloatTimeDomainData(buf);
        let sum = 0;
        for (let i = 0; i < buf.length; i++) sum += buf[i] * buf[i];
        setLevel(Math.sqrt(sum / buf.length));
        rafRef.current = requestAnimationFrame(tick);
      };
      tick();
    })();

    return () => {
      cancelled = true;
      if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
      stream?.getTracks().forEach((t) => t.stop());
      void ctx?.close();
      setLevel(0);
    };
  }, [deviceId, enabled]);

  return { devices, level };
}
