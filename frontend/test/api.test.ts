import { describe, expect, it } from "vitest";
import { HttpError, type StreamEvent, streamChat } from "../src/api";
import { json, stubFetch } from "./fetch-stub";

function bytesStream(parts: Uint8Array[]): ReadableStream<Uint8Array> {
  return new ReadableStream({
    start(controller) {
      for (const part of parts) controller.enqueue(part);
      controller.close();
    },
  });
}

describe("streamChat", () => {
  it("keeps a multi-byte character that is split across two network chunks", async () => {
    const bytes = new TextEncoder().encode('event: token\ndata: {"text":"café ☕"}\n\n');
    const cut = bytes.indexOf(0xe2) + 1; // inside the three bytes of the cup
    stubFetch({
      "POST /api/chat": new Response(bytesStream([bytes.slice(0, cut), bytes.slice(cut)])),
    });
    const events: StreamEvent[] = [];
    await streamChat({ conversation_id: null, message: "hi" }, (e) => events.push(e), new AbortController().signal);
    expect(events).toEqual([{ type: "token", text: "café ☕" }]);
  });

  it("sends the conversation id and the message as JSON", async () => {
    const calls = stubFetch({ "POST /api/chat": new Response("") });
    await streamChat({ conversation_id: 4, message: "next" }, () => {}, new AbortController().signal);
    expect(calls[0]?.body).toEqual({ conversation_id: 4, message: "next" });
  });

  it("rejects with the server's reason when the request is refused", async () => {
    stubFetch({ "POST /api/chat": json({ detail: "conversation not found" }, 404) });
    const attempt = streamChat({ conversation_id: 9, message: "hi" }, () => {}, new AbortController().signal);
    await expect(attempt).rejects.toThrow(HttpError);
    await expect(attempt).rejects.toThrow("conversation not found");
  });
});
