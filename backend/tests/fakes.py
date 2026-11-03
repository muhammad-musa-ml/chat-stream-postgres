"""A scripted stand-in for the model server, plus small helpers the tests share."""

import asyncio
import json
import os
from contextlib import asynccontextmanager
from typing import Any

import httpx

from app.config import Settings
from app.main import create_app

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "postgresql://chat:chat@127.0.0.1:5432/chat_test")


def make_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "database_url": TEST_DATABASE_URL,
        "openai_base_url": "http://model.test/v1",
        "openai_api_key": "test-key",
        "model": "test-model",
        "api_style": "responses",
        "instructions": "Be brief.",
        "max_output_tokens": 64,
        "temperature": None,
        "history_messages": 20,
        "upstream_timeout_s": 5.0,
    }
    values.update(overrides)
    return Settings(**values)


def sse_event(name: str | None, data: dict[str, Any] | str) -> str:
    body = data if isinstance(data, str) else json.dumps(data)
    head = f"event: {name}\n" if name else ""
    return f"{head}data: {body}\n\n"


def responses_delta(text: str) -> str:
    return sse_event("response.output_text.delta", {"type": "response.output_text.delta", "delta": text})


def responses_created() -> str:
    return sse_event("response.created", {"type": "response.created", "response": {"status": "in_progress"}})


def responses_completed(output_tokens: int) -> str:
    return sse_event(
        "response.completed",
        {"type": "response.completed", "response": {"status": "completed", "usage": {"output_tokens": output_tokens}}},
    )


def responses_script(tokens: list[str]) -> list[str]:
    return [responses_created(), *[responses_delta(t) for t in tokens], responses_completed(len(tokens))]


def chat_chunk(text: str) -> str:
    return sse_event(None, {"object": "chat.completion.chunk", "choices": [{"index": 0, "delta": {"content": text}}]})


def chat_script(tokens: list[str]) -> list[str]:
    usage = {"object": "chat.completion.chunk", "choices": [], "usage": {"completion_tokens": len(tokens)}}
    return [*[chat_chunk(t) for t in tokens], sse_event(None, usage), sse_event(None, "[DONE]")]


class ScriptedStream(httpx.AsyncByteStream):
    """Plays a script: a str is sent, a float is a pause in seconds, an Event is waited on."""

    def __init__(self, server: "FakeModelServer", script: list[Any]) -> None:
        self.server = server
        self.script = script

    async def __aiter__(self):
        for item in self.script:
            if isinstance(item, (int, float)):
                await asyncio.sleep(item)
            elif isinstance(item, asyncio.Event):
                await item.wait()
            else:
                self.server.sent += 1
                yield item.encode()

    async def aclose(self) -> None:
        self.server.closed += 1


class FakeModelServer:
    """Records every request and answers each with the next scripted stream."""

    def __init__(self, *scripts: list[Any], status: int = 200, error: Exception | None = None) -> None:
        self.scripts = list(scripts)
        self.status = status
        self.error = error
        self.requests: list[dict[str, Any]] = []
        self.paths: list[str] = []
        self.auth: list[str | None] = []
        self.sent = 0
        self.closed = 0

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    async def handle(self, request: httpx.Request) -> httpx.Response:
        self.paths.append(request.url.path)
        self.auth.append(request.headers.get("authorization"))
        self.requests.append(json.loads(request.content))
        if self.error is not None:
            raise self.error
        if self.status != 200:
            return httpx.Response(self.status, text='{"error":"model not found"}')
        script = self.scripts.pop(0)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=ScriptedStream(self, script))


def parse_sse(text: str) -> list[tuple[str, dict[str, Any]]]:
    """Split the app's SSE output into (event, data) pairs."""
    events = []
    for block in text.split("\n\n"):
        if not block.strip():
            continue
        fields = dict(line.split(": ", 1) for line in block.split("\n"))
        events.append((fields["event"], json.loads(fields["data"])))
    return events


@asynccontextmanager
async def running_app(server: FakeModelServer, **overrides: Any):
    """The real app, with its lifespan run, talking to the fake model server."""
    app = create_app(make_settings(**overrides), transport=server.transport)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://app.test") as client:
            yield app, client
