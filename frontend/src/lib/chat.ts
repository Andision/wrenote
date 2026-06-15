// Chat HTTP helpers. Chat is organised into per-session "conversations"
// (threads); messages hang off a conversation. POST streams the assistant
// reply as text/plain chunks, GET lists history, DELETE clears.

// Same-origin: the SPA is served by the backend, so talk to our own origin
// (port included). Vite dev proxies these paths to the backend — see vite.config.ts.
const BASE =
  typeof window !== "undefined" ? window.location.origin : "http://localhost:8000";

export interface ChatMessage {
  ord: number;
  role: "user" | "assistant";
  content: string;
  createdAt: string;
}

export interface Conversation {
  id: string;
  title: string;
  createdAt: string;
  updatedAt: string;
  messageCount: number;
}

interface ChatRow {
  ord: number;
  role: string;
  content: string;
  created_at: string;
}

interface ConversationRow {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  message_count?: number;
}

function toChatMessage(row: ChatRow): ChatMessage {
  return {
    ord: row.ord,
    role: row.role === "user" ? "user" : "assistant",
    content: row.content,
    createdAt: row.created_at,
  };
}

function toConversation(row: ConversationRow): Conversation {
  return {
    id: row.id,
    title: row.title ?? "",
    createdAt: row.created_at,
    updatedAt: row.updated_at,
    messageCount: row.message_count ?? 0,
  };
}

const sid = (s: string) => encodeURIComponent(s);

// ---------- Conversations ----------

export async function listConversations(
  sessionId: string,
): Promise<Conversation[]> {
  try {
    const res = await fetch(`${BASE}/sessions/${sid(sessionId)}/conversations`);
    if (!res.ok) return [];
    const json = (await res.json()) as { conversations: ConversationRow[] };
    return json.conversations.map(toConversation);
  } catch (e) {
    console.warn("listConversations: network failure", e);
    return [];
  }
}

export async function createConversation(
  sessionId: string,
  title = "",
): Promise<Conversation | null> {
  try {
    const res = await fetch(`${BASE}/sessions/${sid(sessionId)}/conversations`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title }),
    });
    if (!res.ok) return null;
    const json = (await res.json()) as { conversation: ConversationRow };
    return toConversation(json.conversation);
  } catch (e) {
    console.warn("createConversation: network failure", e);
    return null;
  }
}

export async function renameConversation(
  sessionId: string,
  conversationId: string,
  title: string,
): Promise<void> {
  try {
    await fetch(
      `${BASE}/sessions/${sid(sessionId)}/conversations/${sid(conversationId)}`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title }),
      },
    );
  } catch (e) {
    console.warn("renameConversation: network failure", e);
  }
}

export async function deleteConversation(
  sessionId: string,
  conversationId: string,
): Promise<void> {
  try {
    await fetch(
      `${BASE}/sessions/${sid(sessionId)}/conversations/${sid(conversationId)}`,
      { method: "DELETE" },
    );
  } catch (e) {
    console.warn("deleteConversation: network failure", e);
  }
}

// ---------- Messages (scoped to a conversation) ----------

export async function listChatMessages(
  sessionId: string,
  conversationId: string,
): Promise<ChatMessage[]> {
  try {
    const res = await fetch(
      `${BASE}/sessions/${sid(sessionId)}/conversations/${sid(conversationId)}/chat`,
    );
    if (!res.ok) return [];
    const json = (await res.json()) as { messages: ChatRow[] };
    return json.messages.map(toChatMessage);
  } catch (e) {
    console.warn("listChatMessages: network failure", e);
    return [];
  }
}

export async function clearChat(
  sessionId: string,
  conversationId: string,
): Promise<void> {
  try {
    await fetch(
      `${BASE}/sessions/${sid(sessionId)}/conversations/${sid(conversationId)}/chat`,
      { method: "DELETE" },
    );
  } catch (e) {
    console.warn("clearChat: network failure", e);
  }
}

export interface StreamChatOptions {
  sessionId: string;
  conversationId: string;
  text: string;
  signal?: AbortSignal;
  onChunk: (piece: string) => void;
  onDone: (fullText: string) => void;
  onError: (err: Error) => void;
}

/**
 * Send a chat message, stream the reply chunks via onChunk, finish with
 * onDone(fullText) or onError. Aborting the signal cancels the stream
 * cleanly (browser stops reading; server logs the disconnect).
 */
export async function streamChat(opts: StreamChatOptions): Promise<void> {
  const { sessionId, conversationId, text, signal, onChunk, onDone, onError } =
    opts;
  try {
    const res = await fetch(
      `${BASE}/sessions/${sid(sessionId)}/conversations/${sid(conversationId)}/chat`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
        signal,
      },
    );
    if (!res.ok || !res.body) {
      const errText = await res.text().catch(() => res.statusText);
      throw new Error(`HTTP ${res.status}: ${errText}`);
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let acc = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      const piece = decoder.decode(value, { stream: true });
      if (piece) {
        acc += piece;
        onChunk(piece);
      }
    }
    // Flush any remaining buffered bytes.
    const tail = decoder.decode();
    if (tail) {
      acc += tail;
      onChunk(tail);
    }
    onDone(acc);
  } catch (e) {
    if ((e as Error).name === "AbortError") {
      onDone(""); // treat as voluntary cancel
      return;
    }
    onError(e as Error);
  }
}
