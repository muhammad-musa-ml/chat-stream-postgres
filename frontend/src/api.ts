import { SSEParser } from "./sse";

export interface Conversation {
  id: number;
  title: string;
  created_at: string;
  updated_at: string;
}

export type MessageStatus = "complete" | "cancelled" | "error";

export interface Message {
  id: number;
  role: "user" | "assistant";
  content: string;
  status: MessageStatus;
  model: string | null;
  ttft_ms: number | null;
  total_ms: number | null;
  output_tokens: number | null;
  created_at: string;
}

export interface Health {
  ok: boolean;
  model: string;
  api_style: string;
}

export type StreamEvent =
  | { type: "meta"; conversation_id: number; user_message_id: number }
  | { type: "token"; text: string }
  | {
      type: "done";
      message_id: number;
      model: string;
      ttft_ms: number | null;
      total_ms: number;
      output_tokens: number | null;
    }
  | { type: "error"; message_id: number; error: string };

export class HttpError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

async function failure(response: Response): Promise<HttpError> {
  let detail = `HTTP ${response.status}`;
  try {
    const body = await response.json();
    if (typeof body?.detail === "string") detail = body.detail;
    else if (response.status === 422) detail = "the message must be 1 to 8000 characters";
  } catch {
    // Not JSON (a proxy error page, say): keep the status line.
  }
  return new HttpError(response.status, detail);
}

async function getJSON<T>(url: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(url, { signal });
  if (!response.ok) throw await failure(response);
  return (await response.json()) as T;
}

export const getHealth = (signal?: AbortSignal) => getJSON<Health>("/api/health", signal);

export const listConversations = (signal?: AbortSignal) =>
  getJSON<Conversation[]>("/api/conversations", signal);

export const listMessages = (conversationId: number, signal?: AbortSignal) =>
  getJSON<Message[]>(`/api/conversations/${conversationId}/messages`, signal);

/**
 * POST a message and call `onEvent` for each server-sent event of the reply,
 * as it arrives. Resolves when the server ends the stream; rejects on a
 * non-2xx answer, a network failure, or when `signal` aborts.
 */
export async function streamChat(
  body: { conversation_id: number | null; message: string },
  onEvent: (event: StreamEvent) => void,
  signal: AbortSignal,
): Promise<void> {
  const response = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify(body),
    signal,
  });
  if (!response.ok || response.body === null) throw await failure(response);

  const reader = response.body.getReader();
  // stream: true keeps a multi-byte character that is split across chunks.
  const decoder = new TextDecoder();
  const parser = new SSEParser();
  for (;;) {
    const { value, done } = await reader.read();
    const text = done ? decoder.decode() : decoder.decode(value, { stream: true });
    for (const event of parser.push(text)) {
      onEvent({ type: event.event, ...JSON.parse(event.data) } as StreamEvent);
    }
    if (done) return;
  }
}
