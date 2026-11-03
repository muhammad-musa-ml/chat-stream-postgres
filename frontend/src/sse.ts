export interface SSEEvent {
  event: string;
  data: string;
}

/**
 * Incremental parser for a text/event-stream body. Network chunks can split an
 * event, a line or even a CRLF anywhere, so it keeps the unfinished tail and
 * only returns events whose closing blank line has arrived.
 */
export class SSEParser {
  private tail = "";
  private event = "";
  private data: string[] = [];

  push(chunk: string): SSEEvent[] {
    let text = this.tail + chunk;
    // A CR at the very end may be the first half of a CRLF in the next chunk.
    let heldCR = "";
    if (text.endsWith("\r")) {
      heldCR = "\r";
      text = text.slice(0, -1);
    }
    const lines = text.split(/\r\n|\r|\n/);
    this.tail = (lines.pop() ?? "") + heldCR;

    const events: SSEEvent[] = [];
    for (const line of lines) {
      if (line === "") {
        if (this.data.length > 0) {
          events.push({ event: this.event || "message", data: this.data.join("\n") });
        }
        this.event = "";
        this.data = [];
        continue;
      }
      if (line.startsWith(":")) continue;
      const colon = line.indexOf(":");
      const field = colon === -1 ? line : line.slice(0, colon);
      let value = colon === -1 ? "" : line.slice(colon + 1);
      if (value.startsWith(" ")) value = value.slice(1);
      if (field === "event") this.event = value;
      else if (field === "data") this.data.push(value);
    }
    return events;
  }
}
