import { vi } from "vitest";

// A stand-in for the backend: answers "METHOD path" from a table and records
// every request the page makes.

export interface Call {
  method: string;
  url: string;
  body: unknown;
  signal?: AbortSignal | null;
}

export type Answer = Response | ((call: Call) => Response);

export function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

export function stubFetch(routes: Record<string, Answer | Answer[]>) {
  const calls: Call[] = [];
  const queues = new Map(Object.entries(routes).map(([key, answer]) => [key, Array.isArray(answer) ? [...answer] : [answer]]));
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      const method = init?.method ?? "GET";
      const call: Call = {
        method,
        url,
        body: init?.body === undefined ? undefined : JSON.parse(String(init.body)),
        signal: init?.signal,
      };
      calls.push(call);
      const queue = queues.get(`${method} ${url}`);
      if (!queue || queue.length === 0) return json({ detail: `no stub for ${method} ${url}` }, 500);
      // The last answer in a list repeats for any later request.
      const answer = queue.length > 1 ? queue.shift()! : queue[0]!;
      return typeof answer === "function" ? answer(call) : answer.clone();
    }),
  );
  return calls;
}

/** A response body the test writes to piece by piece, like a live SSE stream. */
export function controlledStream() {
  const encoder = new TextEncoder();
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  const stream = new ReadableStream<Uint8Array>({
    start(c) {
      controller = c;
    },
  });
  return {
    response(call: Call): Response {
      call.signal?.addEventListener("abort", () => controller.error(new DOMException("aborted", "AbortError")));
      return new Response(stream, { headers: { "content-type": "text/event-stream" } });
    },
    send(event: string, data: unknown) {
      controller.enqueue(encoder.encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`));
    },
    end() {
      controller.close();
    },
  };
}
