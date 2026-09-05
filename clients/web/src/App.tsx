import { useCallback, useEffect, useRef } from "react";
import { AnimatePresence, LayoutGroup } from "motion/react";

import { ChatPanel } from "@/components/ChatPanel";
import { MinutesPanel } from "@/components/MinutesPanel";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { SetupGate } from "@/components/SetupGate";
import { PreFlight } from "@/components/PreFlight";
import { ProgressOverlay } from "@/components/ProgressOverlay";
import { Sidebar } from "@/components/Sidebar";
import { SettingsDrawer } from "@/components/SettingsDrawer";
import { StatusBar } from "@/components/StatusBar";
import { TooltipLayer } from "@/components/Tooltip";
import { TopBar } from "@/components/TopBar";
import { Transcript } from "@/components/Transcript";
import { UpdateNotice } from "@/components/UpdateNotice";
import { TooltipProvider } from "@/components/ui/tooltip";
import { Toaster } from "@/components/ui/sonner";
import { useMicrophone } from "@/hooks/useMicrophone";
import { PlaybackProvider } from "@/hooks/playbackContext";
import { useWebSocket } from "@/hooks/useWebSocket";
import { initOverlayPublisher } from "@/lib/overlayBridge";
import { useJobsStore } from "@/store/jobsStore";
import { useSessionStore } from "@/store/sessionStore";

export default function App() {
  const { startSession, stopSession, pauseSession, resumeSession, feedAudio } = useWebSocket();
  const feedAudioRef = useRef(feedAudio);
  feedAudioRef.current = feedAudio;

  const onPcm = useCallback((chunk: ArrayBuffer) => {
    feedAudioRef.current(chunk);
  }, []);

  const mic = useMicrophone({ onPcm });
  const connection = useSessionStore((s) => s.connection);
  const segmentCount = useSessionStore((s) => s.segmentOrder.length);

  // Pull the catalog of past sessions from the backend on first mount.
  // Also rehydrate any in-flight jobs from the previous tab session so
  // a mid-upload refresh picks the progress bar back up.
  useEffect(() => {
    void useSessionStore.getState().refreshPastSessions();
    void useSessionStore.getState().refreshGroups();
    useJobsStore.getState().hydrateFromStorage();
  }, []);

  // Feed the floating subtitle overlay (if open) with live transcript state.
  useEffect(() => initOverlayPublisher(), []);
  // Pre-flight owns the canvas only while truly idle. The moment the user
  // clicks Start (connection → "connecting"), we hand off so the magic-move
  // animation fires immediately instead of waiting on the backend's model
  // load. The transcript area then shows a brief "Connecting…" state.
  const inPreFlight =
    segmentCount === 0 &&
    (connection === "disconnected" || connection === "error");

  // Mic lifecycle: start on first transition to "recording" / "paused"
  // (paused keeps the mic alive but gates PCM upstream — no permission
  // re-prompt on resume). Stop on terminal states only.
  useEffect(() => {
    if (connection === "recording" || connection === "paused") {
      mic.start().catch((err) => {
        console.error(err);
        useSessionStore.getState().setError(String(err.message ?? err));
      });
      if (connection === "paused") {
        mic.pause();
      } else {
        mic.resume();
      }
    } else if (connection === "disconnected" || connection === "error") {
      mic.stop();
    }
    // intentionally only on connection changes
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [connection]);

  // When a recording finishes (recording/stopping → disconnected) and the
  // title is still the placeholder, ask the LLM to summarize one.
  const prevConnRef = useRef(connection);
  useEffect(() => {
    const prev = prevConnRef.current;
    prevConnRef.current = connection;
    if (
      (prev === "recording" || prev === "stopping") &&
      connection === "disconnected"
    ) {
      void useSessionStore.getState().afterRecordingStopped();
      // The subtitle overlay is a live-recording companion — dismiss it
      // with the session instead of leaving it parked over the desktop.
      void window.wrenoteDesktop?.closeOverlay();
    }
  }, [connection]);

  const handleStart = useCallback(() => {
    startSession();
  }, [startSession]);

  const handleStop = useCallback(() => {
    stopSession();
  }, [stopSession]);

  const handlePause = useCallback(() => {
    pauseSession();
  }, [pauseSession]);

  const handleResume = useCallback(() => {
    resumeSession();
  }, [resumeSession]);

  return (
    <TooltipProvider delay={300}>
      <PlaybackProvider>
      <LayoutGroup>
        {/* App shell: the sidebar is its own full-height column; the top bar,
            main content and footer form a single unit to the right of it. */}
        <div className="flex h-screen bg-background text-foreground">
          <Sidebar />
          <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
            <TopBar
              onStop={handleStop}
              onPause={handlePause}
              onResume={handleResume}
              inPreFlight={inPreFlight}
            />
            <div className="flex flex-1 overflow-hidden">
              <main className="relative flex flex-1 flex-col overflow-hidden">
                {/* No `mode="wait"` — let pre-flight exit and transcript enter
                    overlap so the lang-strip layoutId handoff stays continuous
                    (otherwise the source unmounts before the target arrives). */}
                <AnimatePresence initial={false}>
                  {inPreFlight ? (
                    <PreFlight key="preflight" onStart={handleStart} />
                  ) : (
                    <Transcript key="transcript" />
                  )}
                </AnimatePresence>
              </main>
              <MinutesPanel />
              <ChatPanel />
            </div>
            <StatusBar />
          </div>
        </div>
        <SettingsDrawer />
        <ProgressOverlay />
      </LayoutGroup>
      </PlaybackProvider>
      <Toaster richColors closeButton />
      <UpdateNotice />
      <ConfirmDialog />
      <SetupGate />
      <TooltipLayer />
    </TooltipProvider>
  );
}
