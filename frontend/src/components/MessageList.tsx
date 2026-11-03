import { useEffect, useRef } from "react";
import type { MessageStatus } from "../api";
import { formatMs } from "../format";
import type { Load } from "../load";

export interface Row {
  key: string;
  role: "user" | "assistant";
  content: string;
  status: MessageStatus | "streaming";
  ttftMs: number | null;
  totalMs: number | null;
  outputTokens: number | null;
  error?: string;
}

interface Props {
  thread: Load<Row[]>;
  model: string | null;
  busy: boolean;
}

export function MessageList({ thread, model, busy }: Props) {
  const end = useRef<HTMLDivElement>(null);
  const rows = thread.status === "ready" ? thread.data : null;

  useEffect(() => {
    end.current?.scrollIntoView?.({ block: "end" });
  }, [rows]);

  return (
    <div className="thread" role="log" aria-label="Messages" aria-busy={busy}>
      {thread.status === "loading" && <p className="muted center">Loading messages…</p>}
      {thread.status === "error" && (
        <p className="notice error center" role="alert">
          Could not load this conversation: {thread.error}
        </p>
      )}
      {rows !== null && rows.length === 0 && (
        <div className="empty center">
          <p>Ask something to start a conversation.</p>
          {model && <p className="muted">Replies stream from {model}.</p>}
        </div>
      )}
      {rows?.map((row) => (row.role === "user" ? <UserMessage key={row.key} row={row} /> : <Reply key={row.key} row={row} />))}
      <div ref={end} />
    </div>
  );
}

function UserMessage({ row }: { row: Row }) {
  return (
    <article className="message user">
      <div className="bubble">{row.content}</div>
    </article>
  );
}

function Reply({ row }: { row: Row }) {
  const waiting = row.status === "streaming" && row.content === "";
  return (
    <article className="message assistant">
      <div className="bubble">
        {waiting ? <span className="muted">Waiting for the first token…</span> : row.content}
        {row.status === "streaming" && !waiting && <span className="caret" aria-hidden="true" />}
      </div>
      <footer className="timings">
        {row.ttftMs !== null && <span>first token {formatMs(row.ttftMs)}</span>}
        {row.totalMs !== null && <span>total {formatMs(row.totalMs)}</span>}
        {row.outputTokens !== null && <span>{row.outputTokens} tokens</span>}
        {row.status === "cancelled" && <span className="badge">stopped</span>}
        {row.status === "error" && <span className="badge error">failed{row.error ? `: ${row.error}` : ""}</span>}
      </footer>
    </article>
  );
}
