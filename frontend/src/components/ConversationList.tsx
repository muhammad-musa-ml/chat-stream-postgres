import type { Conversation } from "../api";
import type { Load } from "../load";

interface Props {
  conversations: Load<Conversation[]>;
  activeId: number | null;
  disabled: boolean;
  onOpen: (id: number) => void;
  onNew: () => void;
  onRetry: () => void;
}

export function ConversationList({ conversations, activeId, disabled, onOpen, onNew, onRetry }: Props) {
  return (
    <nav className="sidebar" aria-label="Conversations">
      <button type="button" className="new-chat" onClick={onNew} disabled={disabled}>
        New chat
      </button>
      {conversations.status === "loading" && <p className="muted">Loading conversations…</p>}
      {conversations.status === "error" && (
        <div className="notice error" role="alert">
          <p>Could not load conversations: {conversations.error}</p>
          <button type="button" onClick={onRetry}>
            Try again
          </button>
        </div>
      )}
      {conversations.status === "ready" && conversations.data.length === 0 && (
        <p className="muted">No conversations yet.</p>
      )}
      {conversations.status === "ready" && conversations.data.length > 0 && (
        <ul>
          {conversations.data.map((c) => (
            <li key={c.id}>
              <button
                type="button"
                className="conversation"
                aria-current={c.id === activeId ? "true" : undefined}
                onClick={() => onOpen(c.id)}
                disabled={disabled}
                title={c.title}
              >
                {c.title}
              </button>
            </li>
          ))}
        </ul>
      )}
    </nav>
  );
}
