import { describe, expect, it } from "vitest";
import { SSEParser, type SSEEvent } from "../src/sse";

function parseInChunks(chunks: string[]): SSEEvent[] {
  const parser = new SSEParser();
  return chunks.flatMap((chunk) => parser.push(chunk));
}

describe("SSEParser", () => {
  const stream = 'event: meta\ndata: {"conversation_id":1}\n\nevent: token\ndata: {"text":"Hi"}\n\n';
  const expected = [
    { event: "meta", data: '{"conversation_id":1}' },
    { event: "token", data: '{"text":"Hi"}' },
  ];

  it("returns the same events wherever the network splits the stream", () => {
    for (let cut = 0; cut <= stream.length; cut++) {
      expect(parseInChunks([stream.slice(0, cut), stream.slice(cut)])).toEqual(expected);
    }
  });

  it("returns the same events when every character arrives on its own", () => {
    expect(parseInChunks([...stream])).toEqual(expected);
  });

  it("waits for the blank line before returning an event", () => {
    const parser = new SSEParser();
    expect(parser.push('event: token\ndata: {"text":"a"}\n')).toEqual([]);
    expect(parser.push("\n")).toEqual([{ event: "token", data: '{"text":"a"}' }]);
  });

  it("treats a CRLF split across two chunks as one line ending", () => {
    expect(parseInChunks(["data: one\r", "\n\r", "\n"])).toEqual([{ event: "message", data: "one" }]);
  });

  it("skips comments, joins multi-line data and defaults the event name", () => {
    expect(parseInChunks([": ping\ndata: a\ndata: b\n\n"])).toEqual([{ event: "message", data: "a\nb" }]);
  });
});
