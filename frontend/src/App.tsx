import { useCallback, useEffect, useRef, useState } from "react";
import {
  type Conversation,
  type Health,
  type Message,
  getHealth,
  listConversations,
  listMessages,
  streamChat,
} from "./api";
import { Composer } from "./components/Composer";
import { ConversationList } from "./components/ConversationList";
import { MessageList, type Row } from "./components/MessageList";
import { type Load, messageOf } from "./load";

function toRow(message: Message): Row {
  return {
    key: `m${message.id}`,
    role: message.role,
    content: message.content,
    status: message.status,
    ttftMs: message.ttft_ms,
    totalMs: message.total_ms,
    outputTokens: message.output_tokens,
  };
}

export function App() {
  const [health, setHealth] = useState<Load<Health>>({ status: "loading" });
  const [conversations, setConversations] = useState<Load<Conversation[]>>({ status: "loading" });
  const [activeId, setActiveId] = useState<number | null>(null);
  const [thread, setThread] = useState<Load<Row[]>>({ status: "ready", data: [] });
  const [draft, setDraft] = useState("");
  const [sendError, setSendError] = useState<string | null>(null);
  const [streaming, setStreaming] = useState(false);
  const streamAbort = useRef<AbortController | null>(null);
  const threadAbort = useRef<AbortController | null>(null);
  const nextKey = useRef(0);

  const refreshConversations = useCallback(async () => {
    try {
      setConversations({ status: "ready", data: await listConversations() });
    } catch (error) {
      setConversations({ status: "error", error: messageOf(error) });
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    getHealth(controller.signal).then(
      (data) => setHealth({ status: "ready", data }),
      (error) => !controller.signal.aborted && setHealth({ status: "error", error: messageOf(error) }),
    );
    void refreshConversations();
    return () => controller.abort();
  }, [refreshConversations]);

  async function openConversation(id: number) {
    // A slow load for the previous click must not land over this one.
    threadAbort.current?.abort();
    const controller = new AbortController();
    threadAbort.current = controller;
    setActiveId(id);
    setSendError(null);
    setThread({ status: "loading" });
    try {
      const messages = await listMessages(id, controller.signal);
      if (!controller.signal.aborted) setThread({ status: "ready", data: messages.map(toRow) });
    } catch (error) {
      if (!controller.signal.aborted) setThread({ status: "error", error: messageOf(error) });
    }
  }

  function newConversation() {
    threadAbort.current?.abort();
    setActiveId(null);
    setSendError(null);
    setThread({ status: "ready", data: [] });
  }

  function updateRows(change: (rows: Row[]) => Row[]) {
    setThread((current) => (current.status === "ready" ? { status: "ready", data: change(current.data) } : current));
  }

  async function send() {
    const text = draft.trim();
    if (text === "" || streaming || thread.status !== "ready") return;

    const controller = new AbortController();
    streamAbort.current = controller;
    const key = nextKey.current++;
    const userKey = `local-user-${key}`;
    const replyKey = `local-reply-${key}`;
    const patchReply = (patch: (row: Row) => Row) =>
      updateRows((rows) => rows.map((row) => (row.key === replyKey ? patch(row) : row)));

    setDraft("");
    setSendError(null);
    setStreaming(true);
    updateRows((rows) => [
      ...rows,
      { key: userKey, role: "user", content: text, status: "complete", ttftMs: null, totalMs: null, outputTokens: null },
      { key: replyKey, role: "assistant", content: "", status: "streaming", ttftMs: null, totalMs: null, outputTokens: null },
    ]);

    let accepted = false;
    let finished = false;
    try {
      await streamChat(
        { conversation_id: activeId, message: text },
        (event) => {
          if (event.type === "meta") {
            accepted = true;
            setActiveId(event.conversation_id);
          } else if (event.type === "token") {
            patchReply((row) => ({ ...row, content: row.content + event.text }));
          } else if (event.type === "done") {
            finished = true;
            patchReply((row) => ({
              ...row,
              status: "complete",
              ttftMs: event.ttft_ms,
              totalMs: event.total_ms,
              outputTokens: event.output_tokens,
            }));
          } else if (event.type === "error") {
            finished = true;
            patchReply((row) => ({ ...row, status: "error", error: event.error }));
          }
        },
        controller.signal,
      );
      if (!finished) {
        patchReply((row) => ({ ...row, status: "error", error: "the connection closed before the reply finished" }));
      }
    } catch (error) {
      if (controller.signal.aborted) {
        patchReply((row) => ({ ...row, status: "cancelled" }));
      } else if (!accepted) {
        // The server stored nothing, so take both rows back out and return the
        // text to the box rather than lose what the user typed.
        updateRows((rows) => rows.filter((row) => row.key !== userKey && row.key !== replyKey));
        setDraft((current) => (current.trim() === "" ? text : current));
        setSendError(messageOf(error));
      } else {
        patchReply((row) => ({ ...row, status: "error", error: messageOf(error) }));
      }
    } finally {
      streamAbort.current = null;
      setStreaming(false);
      void refreshConversations();
    }
  }

  const model = health.status === "ready" ? health.data.model : null;

  return (
    <div className="app">
      <header className="topbar">
        <h1>chat-stream-postgres</h1>
        {health.status === "ready" && (
          <span className="model">
            {health.data.model} · {health.data.api_style.replace("_", " ")} API
          </span>
        )}
        {health.status === "error" && (
          <span className="notice error" role="alert">
            Backend not reachable: {health.error}
          </span>
        )}
      </header>
      <ConversationList
        conversations={conversations}
        activeId={activeId}
        disabled={streaming}
        onOpen={(id) => void openConversation(id)}
        onNew={newConversation}
        onRetry={() => void refreshConversations()}
      />
      <main className="chat">
        <MessageList thread={thread} model={model} busy={streaming} />
        <Composer
          value={draft}
          streaming={streaming}
          error={sendError}
          onChange={setDraft}
          onSend={() => void send()}
          onStop={() => streamAbort.current?.abort()}
        />
      </main>
    </div>
  );
}
