// Chat HTTP helpers — POST streams the assistant reply as text/plain chunks,
// GET lists history, DELETE clears. All scoped by session id.

const BACKEND_HOST =
  typeof window !== "undefined" ? window.location.hostname : "localhost";
const BASE = `http://${BACKEND_HOST}:8000`;

export interface ChatMessage {
  ord: number;
  role: "user" | "assistant";
  content: string;
  createdAt: string;
}

interface ChatRow {
  ord: number;
  role: string;
  content: string;
  created_at: string;
}

function toChatMessage(row: ChatRow): ChatMessage {
  return {
    ord: row.ord,
    role: row.role === "user" ? "user" : "assistant",
    content: row.content,
    createdAt: row.created_at,
  };
}

export async function listChatMessages(sessionId: string): Promise<ChatMessage[]> {
  try {
    const res = await fetch(`${BASE}/sessions/${encodeURIComponent(sessionId)}/chat`);
    if (!res.ok) return [];
    const json = (await res.json()) as { messages: ChatRow[] };
    return json.messages.map(toChatMessage);
  } catch (e) {
    console.warn("listChatMessages: network failure", e);
    return [];
  }
}

export async function clearChat(sessionId: string): Promise<void> {
  try {
    await fetch(`${BASE}/sessions/${encodeURIComponent(sessionId)}/chat`, {
      method: "DELETE",
    });
  } catch (e) {
    console.warn("clearChat: network failure", e);
  }
}

export interface StreamChatOptions {
  sessionId: string;
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
  const { sessionId, text, signal, onChunk, onDone, onError } = opts;
  try {
    const res = await fetch(
      `${BASE}/sessions/${encodeURIComponent(sessionId)}/chat`,
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
