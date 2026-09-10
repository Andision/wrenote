// Shares the playback singleton (audio element lives in usePlayback) with
// both the transcript and the master playback bar. App calls usePlayback
// once and wraps everything below; consumers read via usePlaybackControls.
import { type ReactNode } from "react";

import { PlaybackContext } from "@/hooks/playbackContext";
import { usePlayback } from "@/hooks/usePlayback";

export function PlaybackProvider({ children }: { children: ReactNode }) {
  const ctrls = usePlayback();
  return (
    <PlaybackContext.Provider value={ctrls}>{children}</PlaybackContext.Provider>
  );
}
