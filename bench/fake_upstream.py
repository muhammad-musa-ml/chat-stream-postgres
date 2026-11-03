"""A stand-in model server that speaks both OpenAI streaming shapes.

It sends a fixed text at a fixed pace, so latency measured through the backend
against it is the backend's own cost (HTTP, SSE relay, Postgres writes) with
no model in the way. CI runs the end-to-end check against it.

    python bench/fake_upstream.py --port 9100 --first-token-ms 50 --token-ms 10
"""

import argparse
import asyncio
import json

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

WORDS = "the quick brown fox jumps over the lazy dog".split()


def sse(data: dict | str, event: str | None = None) -> str:
    body = data if isinstance(data, str) else json.dumps(data)
    return (f"event: {event}\n" if event else "") + f"data: {body}\n\n"


def create_app(tokens: int, first_token_ms: float, token_ms: float) -> FastAPI:
    app = FastAPI()

    def words(cap: int | None) -> list[str]:
        count = min(tokens, cap) if cap else tokens
        return [(" " if i else "") + WORDS[i % len(WORDS)] for i in range(count)]

    async def paced(pieces: list[str], start: list[str], piece, end: list[str]):
        for event in start:
            yield event
        sent = 0
        try:
            await asyncio.sleep(first_token_ms / 1000)
            for i, text in enumerate(pieces):
                if i:
                    await asyncio.sleep(token_ms / 1000)
                yield piece(text)
                sent += 1
            for event in end:
                yield event
        except asyncio.CancelledError:
            print(f"client closed the stream after {sent} of {len(pieces)} tokens", flush=True)
            raise

    @app.post("/v1/responses")
    async def responses(request: Request) -> StreamingResponse:
        body = await request.json()
        pieces = words(body.get("max_output_tokens"))
        created = sse({"type": "response.created", "response": {"status": "in_progress"}}, "response.created")
        completed = sse(
            {"type": "response.completed", "response": {"status": "completed", "usage": {"output_tokens": len(pieces)}}},
            "response.completed",
        )
        stream = paced(
            pieces,
            [created],
            lambda text: sse({"type": "response.output_text.delta", "delta": text}, "response.output_text.delta"),
            [completed],
        )
        return StreamingResponse(stream, media_type="text/event-stream")

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request) -> StreamingResponse:
        body = await request.json()
        pieces = words(body.get("max_tokens"))
        usage = sse({"choices": [], "usage": {"completion_tokens": len(pieces)}})
        stream = paced(
            pieces,
            [],
            lambda text: sse({"choices": [{"index": 0, "delta": {"content": text}}]}),
            [usage, sse("[DONE]")],
        )
        return StreamingResponse(stream, media_type="text/event-stream")

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9100)
    parser.add_argument("--tokens", type=int, default=64, help="tokens per reply, before the request's own cap")
    parser.add_argument("--first-token-ms", type=float, default=50)
    parser.add_argument("--token-ms", type=float, default=10)
    args = parser.parse_args()
    app = create_app(args.tokens, args.first_token_ms, args.token_ms)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
