// The playback singleton's context and reader. The provider that fills it is
// a component and lives in its own file, so this one stays Fast-Refresh-safe.
import { createContext, useContext } from "react";

import { type UsePlayback } from "@/hooks/usePlayback";

export const PlaybackContext = createContext<UsePlayback | null>(null);

export function usePlaybackControls(): UsePlayback {
  const ctx = useContext(PlaybackContext);
  if (!ctx) {
    throw new Error("usePlaybackControls must be used inside <PlaybackProvider>");
  }
  return ctx;
}
