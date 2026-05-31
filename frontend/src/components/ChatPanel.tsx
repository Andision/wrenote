import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import {
  ArrowDown,
  Loader2,
  MessageSquare,
  Send,
  Sparkles,
  Trash2,
  X,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { useAutoScroll } from "@/hooks/useAutoScroll";
import {
  clearChat as clearChatRemote,
  listChatMessages,
  streamChat,
  type ChatMessage,
} from "@/lib/chat";
import { useSessionStore } from "@/store/sessionStore";

/**
 * Right-side, push-in chat panel. The transcript stays visible to the left.
 * Chat is scoped to the current session: backend snapshots the transcript
 * at each turn and feeds it as the system context.
 */
export function ChatPanel() {
  const open = useSessionStore((s) => s.chatOpen);
  const sessionId = useSessionStore((s) => s.sessionId);
  const segmentCount = useSessionStore((s) => s.segmentOrder.length);

  return (
    <AnimatePresence initial={false}>
      {open && (
        <motion.aside
          key="chat-panel"
          initial={{ width: 0, opacity: 0 }}
          animate={{ width: 400, opacity: 1 }}
          exit={{ width: 0, opacity: 0 }}
          transition={{ duration: 0.22, ease: [0.32, 0.72, 0, 1] }}
          className="flex shrink-0 flex-col overflow-hidden border-l bg-card"
        >
          <ChatBody sessionId={sessionId} segmentCount={segmentCount} />
        </motion.aside>
      )}
    </AnimatePresence>
  );
}

function ChatBody({
  sessionId,
  segmentCount,
}: {
  sessionId: string | null;
  segmentCount: number;
}) {
  const toggleChat = useSessionStore((s) => s.toggleChat);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [loadingHistory, setLoadingHistory] = useState(false);
  // Bumps every time we tell the chat to re-fetch from backend; the
  // streaming text trailing-edge is also a dep so the "pinned to bottom"
  // logic re-runs as chunks arrive.
  const streamProgress = messages.reduce(
    (n, m) => n + (m.role === "assistant" ? m.content.length : 0),
    0,
  );
  const { ref: scrollRef, pinned, scrollToBottom } = useAutoScroll<HTMLDivElement>(
    [messages.length, streamProgress, streaming],
  );

  // Holds the in-flight assistant message id (a synthetic ord); the same
  // object is mutated as chunks arrive so React re-renders the latest text.
  const streamingOrdRef = useRef<number | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  // Load history whenever the session id changes OR the panel re-mounts
  // (which happens every time `open` flips false→true via AnimatePresence).
  // Both signals end up here: ChatBody only mounts when the panel is open.
  useEffect(() => {
    if (!sessionId) {
      setMessages([]);
      return;
    }
    let cancelled = false;
    setLoadingHistory(true);
    void listChatMessages(sessionId)
      .then((list) => {
        if (!cancelled) setMessages(list);
      })
      .finally(() => {
        if (!cancelled) setLoadingHistory(false);
      });
    return () => {
      cancelled = true;
    };
  }, [sessionId]);

  const canSend = useMemo(
    () => Boolean(sessionId) && draft.trim().length > 0 && !streaming,
    [sessionId, draft, streaming],
  );

  const send = useCallback(async () => {
    const text = draft.trim();
    if (!text || !sessionId) return;
    setDraft("");

    // Optimistic user message (the backend persists it too; the next
    // history fetch on panel re-open will reconcile.)
    const optimisticOrd = messages.length;
    const userMsg: ChatMessage = {
      ord: optimisticOrd,
      role: "user",
      content: text,
      createdAt: new Date().toISOString(),
    };
    const assistantOrd = optimisticOrd + 1;
    const assistantStub: ChatMessage = {
      ord: assistantOrd,
      role: "assistant",
      content: "",
      createdAt: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, userMsg, assistantStub]);
    streamingOrdRef.current = assistantOrd;
    setStreaming(true);

    const controller = new AbortController();
    abortRef.current = controller;

    await streamChat({
      sessionId,
      text,
      signal: controller.signal,
      onChunk: (piece) => {
        setMessages((prev) => {
          const idx = prev.findIndex((m) => m.ord === assistantOrd);
          if (idx < 0) return prev;
          const next = prev.slice();
          next[idx] = { ...next[idx], content: next[idx].content + piece };
          return next;
        });
      },
      onDone: () => {
        setStreaming(false);
        streamingOrdRef.current = null;
        abortRef.current = null;
      },
      onError: (err) => {
        setStreaming(false);
        streamingOrdRef.current = null;
        abortRef.current = null;
        setMessages((prev) => {
          const idx = prev.findIndex((m) => m.ord === assistantOrd);
          if (idx < 0) return prev;
          const next = prev.slice();
          next[idx] = {
            ...next[idx],
            content: `⚠ ${err.message}`,
          };
          return next;
        });
      },
    });
  }, [draft, sessionId, messages.length]);

  const cancel = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  const clear = useCallback(async () => {
    if (!sessionId) return;
    if (!confirm("Clear this chat?")) return;
    abortRef.current?.abort();
    await clearChatRemote(sessionId);
    setMessages([]);
  }, [sessionId]);

  return (
    <div className="flex h-full w-[400px] flex-col">
      <header className="flex h-12 items-center justify-between border-b px-3">
        <div className="flex items-center gap-2 text-sm font-semibold text-foreground">
          <Sparkles className="size-4 text-blue-600 dark:text-blue-400" />
          Ask about this session
        </div>
        <div className="flex items-center gap-0.5">
          <Button
            variant="ghost"
            size="icon"
            className="size-7"
            onClick={clear}
            disabled={!sessionId || messages.length === 0}
            title="Clear chat"
          >
            <Trash2 className="size-3.5" />
          </Button>
          <Button
            variant="ghost"
            size="icon"
            className="size-7"
            onClick={() => toggleChat(false)}
            title="Close"
          >
            <X className="size-3.5" />
          </Button>
        </div>
      </header>

      <div ref={scrollRef} className="relative flex-1 overflow-y-auto px-4 py-4">
        {!sessionId ? (
          <EmptyState
            icon={<MessageSquare className="size-7 text-muted-foreground" />}
            title="Start a session first"
            hint="Chat is anchored to the active recording. Hit Record to begin."
          />
        ) : loadingHistory && messages.length === 0 ? (
          <div className="flex h-full min-h-[40vh] items-center justify-center text-[12px] text-muted-foreground">
            <Loader2 className="mr-2 size-3.5 animate-spin" />
            Loading chat…
          </div>
        ) : messages.length === 0 ? (
          <EmptyState
            icon={<Sparkles className="size-7 text-blue-500/80" />}
            title={segmentCount === 0 ? "Ready when you are" : "Ask anything"}
            hint={
              segmentCount === 0
                ? "Start talking — your questions can reference whatever gets captured."
                : "Summaries, action items, decisions, who-said-what — try it."
            }
          />
        ) : (
          <ul className="space-y-3">
            {messages.map((m) => (
              <li
                key={m.ord}
                className={
                  m.role === "user"
                    ? "ml-8 rounded-2xl rounded-tr-sm bg-blue-600 px-3 py-2 text-[13.5px] leading-relaxed text-white shadow-sm"
                    : "mr-8 rounded-2xl rounded-tl-sm bg-muted/60 px-3 py-2 text-[13.5px] leading-relaxed text-foreground"
                }
              >
                {m.content || (m.role === "assistant" && streaming ? (
                  <span className="inline-flex items-center gap-1.5 text-muted-foreground">
                    <Loader2 className="size-3 animate-spin" /> thinking…
                  </span>
                ) : null)}
              </li>
            ))}
          </ul>
        )}

        {/* Jump-to-latest while streaming and the user has scrolled up. */}
        {!pinned && messages.length > 0 && (
          <Button
            onClick={scrollToBottom}
            size="sm"
            className="absolute bottom-3 right-4 gap-1.5 shadow-md"
          >
            <ArrowDown className="size-3.5" />
            Latest
          </Button>
        )}
      </div>

      <footer className="border-t bg-background/40 px-3 py-3">
        <div className="flex items-end gap-2">
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                if (canSend) void send();
              }
            }}
            placeholder={sessionId ? "Ask about this session…" : "Start a session to chat"}
            disabled={!sessionId}
            rows={2}
            className="flex-1 resize-none rounded-lg border border-border bg-background px-3 py-2 text-[13.5px] leading-relaxed text-foreground outline-none placeholder:text-muted-foreground/70 focus:border-ring focus:ring-2 focus:ring-ring/40 disabled:opacity-50"
          />
          {streaming ? (
            <Button
              variant="outline"
              size="icon"
              className="size-9"
              onClick={cancel}
              title="Stop"
            >
              <X className="size-4" />
            </Button>
          ) : (
            <Button
              size="icon"
              className="size-9 bg-blue-600 text-white hover:bg-blue-700 dark:bg-blue-500 dark:hover:bg-blue-600"
              onClick={() => void send()}
              disabled={!canSend}
              title="Send (Enter)"
            >
              <Send className="size-4" />
            </Button>
          )}
        </div>
      </footer>
    </div>
  );
}

function EmptyState({
  icon,
  title,
  hint,
}: {
  icon: React.ReactNode;
  title: string;
  hint: string;
}) {
  return (
    <div className="flex h-full min-h-[40vh] flex-col items-center justify-center text-center">
      <div className="flex size-12 items-center justify-center rounded-xl bg-muted/50 ring-1 ring-inset ring-border">
        {icon}
      </div>
      <div className="mt-4 text-sm font-medium text-foreground">{title}</div>
      <p className="mt-1 max-w-[16rem] text-[12px] leading-relaxed text-muted-foreground">
        {hint}
      </p>
    </div>
  );
}
