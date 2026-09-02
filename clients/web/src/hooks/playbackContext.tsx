// Shares the playback singleton (audio element lives in usePlayback) with
// both the transcript and the master playback bar. App calls usePlayback
// once and wraps everything below; consumers read via usePlaybackControls.
import { createContext, useContext, type ReactNode } from "react";

import { usePlayback, type UsePlayback } from "@/hooks/usePlayback";

const PlaybackContext = createContext<UsePlayback | null>(null);

export function PlaybackProvider({ children }: { children: ReactNode }) {
  const ctrls = usePlayback();
  return (
    <PlaybackContext.Provider value={ctrls}>{children}</PlaybackContext.Provider>
  );
}

export function usePlaybackControls(): UsePlayback {
  const ctx = useContext(PlaybackContext);
  if (!ctx) {
    throw new Error("usePlaybackControls must be used inside <PlaybackProvider>");
  }
  return ctx;
}
