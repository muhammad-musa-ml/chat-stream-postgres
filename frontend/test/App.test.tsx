import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { App } from "../src/App";
import { controlledStream, json, stubFetch } from "./fetch-stub";

const health = json({ ok: true, model: "llama3.2:3b", api_style: "responses" });

const conversation = (id: number, title: string) => ({
  id,
  title,
  created_at: "2026-11-02T20:00:00Z",
  updated_at: "2026-11-02T20:00:00Z",
});

describe("App", () => {
  it("streams a reply into the page token by token, then shows its timings", async () => {
    const live = controlledStream();
    const calls = stubFetch({
      "GET /api/health": health,
      "GET /api/conversations": [json([]), json([conversation(7, "hello there")])],
      "POST /api/chat": (call) => live.response(call),
    });
    const user = userEvent.setup();
    render(<App />);
    expect(await screen.findByText("No conversations yet.")).toBeTruthy();

    await user.type(screen.getByLabelText("Message"), "hello there{Enter}");
    expect(await screen.findByText("Waiting for the first token…")).toBeTruthy();
    expect(calls.find((c) => c.method === "POST")?.body).toEqual({ conversation_id: null, message: "hello there" });

    live.send("meta", { conversation_id: 7, user_message_id: 1 });
    live.send("token", { text: "Hel" });
    expect(await screen.findByText("Hel")).toBeTruthy();
    expect(screen.queryByText("Waiting for the first token…")).toBeNull();
    expect(screen.getByRole("button", { name: "Stop" })).toBeTruthy();

    live.send("token", { text: "lo" });
    live.send("done", { message_id: 2, model: "llama3.2:3b", ttft_ms: 120.4, total_ms: 1530, output_tokens: 2 });
    live.end();
    expect(await screen.findByText("Hello")).toBeTruthy();
    expect(screen.getByText("first token 120 ms")).toBeTruthy();
    expect(screen.getByText("total 1.53 s")).toBeTruthy();
    expect(screen.getByText("2 tokens")).toBeTruthy();

    const nav = screen.getByRole("navigation", { name: "Conversations" });
    const item = await within(nav).findByRole("button", { name: "hello there" });
    expect(item.getAttribute("aria-current")).toBe("true");
    expect(screen.getByRole("button", { name: "Send" })).toBeTruthy();
  });

  it("puts the text back in the box when the server refuses the message", async () => {
    stubFetch({
      "GET /api/health": health,
      "GET /api/conversations": json([]),
      "POST /api/chat": json({ detail: "database unavailable" }, 503),
    });
    const user = userEvent.setup();
    render(<App />);
    await user.type(screen.getByLabelText("Message"), "keep this text");
    await user.click(screen.getByRole("button", { name: "Send" }));

    expect((await screen.findByRole("alert")).textContent).toBe("Not sent: database unavailable");
    expect((screen.getByLabelText("Message") as HTMLTextAreaElement).value).toBe("keep this text");
    expect(screen.queryByText("keep this text", { selector: ".bubble" })).toBeNull();
  });

  it("Stop ends the reply, keeps what arrived and marks it stopped", async () => {
    const live = controlledStream();
    stubFetch({
      "GET /api/health": health,
      "GET /api/conversations": json([]),
      "POST /api/chat": (call) => live.response(call),
    });
    const user = userEvent.setup();
    render(<App />);
    await user.type(screen.getByLabelText("Message"), "tell me a story{Enter}");
    live.send("meta", { conversation_id: 1, user_message_id: 1 });
    live.send("token", { text: "Once upon" });
    expect(await screen.findByText("Once upon")).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "Stop" }));
    expect(await screen.findByText("stopped")).toBeTruthy();
    expect(screen.getByText("Once upon")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Send" })).toBeTruthy();
  });

  it("opens a stored conversation with each reply's timings and status", async () => {
    stubFetch({
      "GET /api/health": health,
      "GET /api/conversations": json([conversation(3, "Capital of France?")]),
      "GET /api/conversations/3/messages": json([
        { id: 1, role: "user", content: "Capital of France?", status: "complete", model: null, ttft_ms: null, total_ms: null, output_tokens: null, created_at: "" },
        { id: 2, role: "assistant", content: "Paris.", status: "complete", model: "llama3.2:3b", ttft_ms: 95, total_ms: 640, output_tokens: 3, created_at: "" },
        { id: 3, role: "user", content: "And Spain?", status: "complete", model: null, ttft_ms: null, total_ms: null, output_tokens: null, created_at: "" },
        { id: 4, role: "assistant", content: "Mad", status: "cancelled", model: "llama3.2:3b", ttft_ms: 80, total_ms: 210, output_tokens: null, created_at: "" },
      ]),
    });
    const user = userEvent.setup();
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "Capital of France?" }));

    expect(await screen.findByText("Paris.")).toBeTruthy();
    expect(screen.getByText("first token 95 ms")).toBeTruthy();
    expect(screen.getByText("total 640 ms")).toBeTruthy();
    expect(screen.getByText("stopped")).toBeTruthy();
  });

  it("shows a load error with a retry that recovers", async () => {
    stubFetch({
      "GET /api/health": health,
      "GET /api/conversations": [json({ detail: "database unavailable" }, 500), json([])],
    });
    const user = userEvent.setup();
    render(<App />);
    expect((await screen.findByRole("alert")).textContent).toContain("database unavailable");
    await user.click(screen.getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(screen.getByText("No conversations yet.")).toBeTruthy());
  });
});
