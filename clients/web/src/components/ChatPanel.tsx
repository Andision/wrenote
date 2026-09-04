import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import {
  ArrowDown,
  Check,
  ChevronDown,
  History,
  Loader2,
  MessageSquare,
  Pencil,
  Plus,
  Send,
  Sparkles,
  Trash2,
  X,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Markdown } from "@/components/Markdown";
import { confirmDialog } from "@/lib/confirm";
import { useAutoScroll } from "@/hooks/useAutoScroll";
import {
  clearChat as clearChatRemote,
  createConversation,
  deleteConversation as deleteConversationRemote,
  listChatMessages,
  listConversations,
  renameConversation,
  streamChat,
  type ChatMessage,
  type Conversation,
} from "@/lib/chat";
import { useSessionStore } from "@/store/sessionStore";
import { useI18n, useT } from "@/i18n";

/**
 * Right-side, push-in chat panel. The transcript stays visible to the left.
 * Chat is scoped to the current session and split into threads
 * ("conversations") so you can keep several lines of questioning apart.
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
  const t = useT();
  const toggleChat = useSessionStore((s) => s.toggleChat);

  const [conversations, setConversations] = useState<Conversation[]>([]);
  // null = a fresh, not-yet-persisted thread (created on first send).
  const [currentId, setCurrentId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [showList, setShowList] = useState(false);

  const streamProgress = messages.reduce(
    (n, m) => n + (m.role === "assistant" ? m.content.length : 0),
    0,
  );
  const { ref: scrollRef, pinned, scrollToBottom } = useAutoScroll<HTMLDivElement>(
    [messages.length, streamProgress, streaming],
  );

  const streamingOrdRef = useRef<number | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const loadMessages = useCallback(
    async (sid: string, convId: string) => {
      setLoadingHistory(true);
      try {
        const list = await listChatMessages(sid, convId);
        setMessages(list);
      } finally {
        setLoadingHistory(false);
      }
    },
    [],
  );

  // (Re)load the thread list when the session changes, and open the most
  // recent thread. A session with no threads starts on a blank "New chat".
  useEffect(() => {
    abortRef.current?.abort();
    if (!sessionId) {
      setConversations([]);
      setCurrentId(null);
      setMessages([]);
      return;
    }
    let cancelled = false;
    setShowList(false);
    void listConversations(sessionId).then((list) => {
      if (cancelled) return;
      setConversations(list);
      const first = list[0]?.id ?? null;
      setCurrentId(first);
      if (first) void loadMessages(sessionId, first);
      else setMessages([]);
    });
    return () => {
      cancelled = true;
    };
  }, [sessionId, loadMessages]);

  const refreshConversations = useCallback(async () => {
    if (!sessionId) return;
    const list = await listConversations(sessionId);
    setConversations(list);
  }, [sessionId]);

  const selectConversation = useCallback(
    (id: string) => {
      if (!sessionId) return;
      abortRef.current?.abort();
      setCurrentId(id);
      setShowList(false);
      void loadMessages(sessionId, id);
    },
    [sessionId, loadMessages],
  );

  const newConversation = useCallback(() => {
    abortRef.current?.abort();
    setCurrentId(null);
    setMessages([]);
    setShowList(false);
  }, []);

  const canSend = useMemo(
    () => Boolean(sessionId) && draft.trim().length > 0 && !streaming,
    [sessionId, draft, streaming],
  );

  const send = useCallback(async () => {
    const text = draft.trim();
    if (!text || !sessionId) return;
    setDraft("");

    // Lazily create the thread on the first message so we don't litter the
    // list with empty conversations. The server auto-titles it from this text.
    let convId = currentId;
    if (!convId) {
      const conv = await createConversation(sessionId);
      if (!conv) {
        useSessionStore.getState().setError(t("chat.startFailed"));
        return;
      }
      convId = conv.id;
      setCurrentId(conv.id);
      setConversations((prev) => [conv, ...prev]);
    }

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
      conversationId: convId,
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
        // Pick up the server-side auto-title + re-ordering + counts.
        void refreshConversations();
      },
      onError: (err) => {
        setStreaming(false);
        streamingOrdRef.current = null;
        abortRef.current = null;
        setMessages((prev) => {
          const idx = prev.findIndex((m) => m.ord === assistantOrd);
          if (idx < 0) return prev;
          const next = prev.slice();
          next[idx] = { ...next[idx], content: `⚠ ${err.message}` };
          return next;
        });
      },
    });
  }, [draft, sessionId, currentId, messages.length, refreshConversations, t]);

  const cancel = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  const renameCurrent = useCallback(
    async (id: string, title: string) => {
      if (!sessionId) return;
      setConversations((prev) =>
        prev.map((c) => (c.id === id ? { ...c, title } : c)),
      );
      await renameConversation(sessionId, id, title);
    },
    [sessionId],
  );

  const deleteConversation = useCallback(
    async (id: string) => {
      if (!sessionId) return;
      const conv = conversations.find((c) => c.id === id);
      const ok = await confirmDialog({
        title: t("chat.deleteTitle"),
        description: t("chat.deleteBody", { title: conv?.title || t("chat.newChat") }),
        confirmLabel: t("common.delete"),
        destructive: true,
      });
      if (!ok) return;
      if (currentId === id) abortRef.current?.abort();
      await deleteConversationRemote(sessionId, id);
      const remaining = conversations.filter((c) => c.id !== id);
      setConversations(remaining);
      if (currentId === id) {
        const next = remaining[0]?.id ?? null;
        setCurrentId(next);
        if (next) void loadMessages(sessionId, next);
        else setMessages([]);
      }
    },
    [sessionId, conversations, currentId, loadMessages, t],
  );

  const clearCurrent = useCallback(async () => {
    if (!sessionId || !currentId) return;
    const ok = await confirmDialog({
      title: t("chat.clearTitle"),
      description: t("chat.clearBody"),
      confirmLabel: t("chat.clear"),
      destructive: true,
    });
    if (!ok) return;
    abortRef.current?.abort();
    await clearChatRemote(sessionId, currentId);
    setMessages([]);
    void refreshConversations();
  }, [sessionId, currentId, refreshConversations, t]);

  const currentTitle =
    conversations.find((c) => c.id === currentId)?.title || "";

  return (
    <div className="flex h-full w-[400px] flex-col">
      <header className="flex h-12 items-center gap-1 border-b px-3">
        <button
          onClick={() => sessionId && setShowList((v) => !v)}
          disabled={!sessionId}
          data-tip={t("chat.switch")}
          aria-expanded={showList}
          className="flex min-w-0 flex-1 items-center gap-1.5 rounded-md px-1.5 py-1 text-sm font-semibold text-foreground transition-colors hover:bg-accent disabled:opacity-50"
        >
          <Sparkles className="size-4 shrink-0 text-brand-600 dark:text-brand-400" />
          <span className="truncate">{currentTitle || "New chat"}</span>
          <ChevronDown
            className={`size-3.5 shrink-0 text-muted-foreground transition-transform ${
              showList ? "rotate-180" : ""
            }`}
          />
        </button>
        <Button
          variant="ghost"
          size="icon"
          className={`size-7 shrink-0 ${showList ? "bg-accent text-foreground" : ""}`}
          onClick={() => sessionId && setShowList((v) => !v)}
          disabled={!sessionId}
          aria-pressed={showList}
          data-tip={t("chat.history")}
        >
          <History className="size-4" />
        </Button>
        <Button
          variant="ghost"
          size="icon"
          className="size-7 shrink-0"
          onClick={newConversation}
          disabled={!sessionId}
          data-tip={t("chat.newConversation")}
        >
          <Plus className="size-4" />
        </Button>
        {currentId && messages.length > 0 && (
          <Button
            variant="ghost"
            size="icon"
            className="size-7 shrink-0"
            onClick={() => void clearCurrent()}
            data-tip={t("chat.clearTip")}
          >
            <Trash2 className="size-3.5" />
          </Button>
        )}
        <Button
          variant="ghost"
          size="icon"
          className="size-7 shrink-0"
          onClick={() => toggleChat(false)}
          data-tip={t("common.close")}
        >
          <X className="size-3.5" />
        </Button>
      </header>

      <div className="relative min-h-0 flex-1">
        <div
          ref={scrollRef}
          className="absolute inset-0 overflow-y-auto px-4 py-4"
        >
          {!sessionId ? (
            <EmptyState
              icon={<MessageSquare className="size-7 text-muted-foreground" />}
              title={t("chat.noSessionTitle")}
              hint={t("chat.noSessionHint")}
            />
          ) : loadingHistory && messages.length === 0 ? (
            <div className="flex h-full min-h-[40vh] items-center justify-center text-[12px] text-muted-foreground">
              <Loader2 className="mr-2 size-3.5 animate-spin" />
              {t("chat.loading")}
            </div>
          ) : messages.length === 0 ? (
            <EmptyState
              icon={<Sparkles className="size-7 text-brand-500/80" />}
              title={segmentCount === 0 ? t("chat.readyTitle") : t("chat.askTitle")}
              hint={
                segmentCount === 0
                  ? t("chat.readyHint")
                  : t("chat.askHint")
              }
            />
          ) : (
            <ul className="space-y-3">
              {messages.map((m) => (
                <li
                  key={m.ord}
                  className={
                    m.role === "user"
                      ? "ml-8 rounded-2xl rounded-tr-sm bg-brand-600 px-3 py-2 text-[13.5px] leading-relaxed text-white shadow-sm"
                      : "mr-8 rounded-2xl rounded-tl-sm bg-muted/60 px-3 py-2 text-[13.5px] leading-relaxed text-foreground"
                  }
                >
                  {m.role === "assistant" ? (
                    m.content ? (
                      <Markdown text={m.content} />
                    ) : streaming ? (
                      <span className="inline-flex items-center gap-1.5 text-muted-foreground">
                        <Loader2 className="size-3 animate-spin" /> thinking…
                      </span>
                    ) : null
                  ) : (
                    <span className="whitespace-pre-wrap">{m.content}</span>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>

        {/* Jump-to-latest while streaming and the user has scrolled up. */}
        {!showList && !pinned && messages.length > 0 && (
          <Button
            onClick={scrollToBottom}
            size="sm"
            className="absolute bottom-3 left-1/2 -translate-x-1/2 gap-1.5 shadow-md"
          >
            <ArrowDown className="size-3.5" />
            Latest
          </Button>
        )}

        <AnimatePresence>
          {showList && (
            <ConversationList
              conversations={conversations}
              currentId={currentId}
              onSelect={selectConversation}
              onNew={newConversation}
              onRename={renameCurrent}
              onDelete={deleteConversation}
              onClose={() => setShowList(false)}
            />
          )}
        </AnimatePresence>
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
            placeholder={sessionId ? t("chat.placeholder") : t("chat.placeholderNoSession")}
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
              data-tip={t("common.stop")}
            >
              <X className="size-4" />
            </Button>
          ) : (
            <Button
              size="icon"
              className="size-9 bg-brand-600 text-white hover:bg-brand-700 dark:bg-brand-500 dark:hover:bg-brand-600"
              onClick={() => void send()}
              disabled={!canSend}
              data-tip={t("chat.send")}
            >
              <Send className="size-4" />
            </Button>
          )}
        </div>
      </footer>
    </div>
  );
}

function ConversationList({
  conversations,
  currentId,
  onSelect,
  onNew,
  onRename,
  onDelete,
  onClose,
}: {
  conversations: Conversation[];
  currentId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
  onRename: (id: string, title: string) => void;
  onDelete: (id: string) => void;
  onClose: () => void;
}) {
  const { locale } = useI18n();
  const t = useT();
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameDraft, setRenameDraft] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  const startRename = (c: Conversation) => {
    setRenamingId(c.id);
    setRenameDraft(c.title || "");
    queueMicrotask(() => inputRef.current?.select());
  };
  const commitRename = () => {
    const id = renamingId;
    setRenamingId(null);
    const title = renameDraft.trim();
    if (id && title) onRename(id, title);
  };

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.14 }}
      className="absolute inset-0 z-10 flex flex-col bg-card"
    >
      <div className="flex items-center justify-between px-3 py-2.5">
        <span className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
          Conversations
        </span>
        <Button
          variant="ghost"
          size="sm"
          className="h-7 gap-1.5 text-[12px]"
          onClick={onNew}
        >
          <Plus className="size-3.5" />
          New chat
        </Button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-2">
        {conversations.length === 0 ? (
          <p className="px-2 py-8 text-center text-[12px] text-muted-foreground">
            No conversations yet. Send a message to start one.
          </p>
        ) : (
          <ul className="space-y-0.5">
            {conversations.map((c) => {
              const isActive = c.id === currentId;
              if (renamingId === c.id) {
                return (
                  <li key={c.id} className="px-1 py-0.5">
                    <div className="flex items-center gap-1">
                      <input
                        ref={inputRef}
                        value={renameDraft}
                        onChange={(e) => setRenameDraft(e.target.value)}
                        onBlur={commitRename}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") commitRename();
                          if (e.key === "Escape") setRenamingId(null);
                        }}
                        className="h-7 flex-1 rounded-md border border-brand-500/40 bg-background px-2 text-[13px] outline-none focus:ring-2 focus:ring-brand-500/30"
                      />
                      <button
                        onClick={commitRename}
                        className="rounded p-1 text-muted-foreground hover:bg-accent hover:text-foreground"
                        data-tip={t("common.save")}
                      >
                        <Check className="size-3.5" />
                      </button>
                    </div>
                  </li>
                );
              }
              return (
                <li key={c.id}>
                  <button
                    onClick={() => onSelect(c.id)}
                    className={`group flex w-full items-center gap-2 rounded-md px-2 py-2 text-left transition-colors ${
                      isActive ? "bg-accent" : "hover:bg-accent/60"
                    }`}
                  >
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-[13px] font-medium text-foreground">
                        {c.title || t("chat.newChat")}
                      </div>
                      <div className="mt-0.5 text-[11px] text-muted-foreground">
                        {whenLabel(c.updatedAt, locale)} · {c.messageCount} msg
                      </div>
                    </div>
                    <span className="invisible flex shrink-0 items-center gap-0.5 group-hover:visible">
                      <span
                        role="button"
                        tabIndex={0}
                        aria-label={t("common.rename")}
                        onClick={(e) => {
                          e.stopPropagation();
                          startRename(c);
                        }}
                        className="rounded p-1 text-muted-foreground hover:bg-accent hover:text-foreground"
                      >
                        <Pencil className="size-3.5" />
                      </span>
                      <span
                        role="button"
                        tabIndex={0}
                        aria-label={t("common.delete")}
                        onClick={(e) => {
                          e.stopPropagation();
                          onDelete(c.id);
                        }}
                        className="rounded p-1 text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
                      >
                        <Trash2 className="size-3.5" />
                      </span>
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </div>

      <button
        onClick={onClose}
        className="border-t py-2 text-center text-[12px] text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
      >
        Close
      </button>
    </motion.div>
  );
}

function whenLabel(iso: string, locale: string): string {
  const ms = new Date(iso).getTime();
  if (!isFinite(ms)) return "";
  const s = Math.max(0, (Date.now() - ms) / 1000);
  // Intl knows every language's phrasing for "3 minutes ago"; no keys needed.
  const rel = new Intl.RelativeTimeFormat(locale, { numeric: "auto" });
  if (s < 60) return rel.format(0, "second");
  if (s < 3600) return rel.format(-Math.floor(s / 60), "minute");
  if (s < 86400) return rel.format(-Math.floor(s / 3600), "hour");
  if (s < 604800) return rel.format(-Math.floor(s / 86400), "day");
  return new Date(iso).toLocaleDateString(locale);
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
