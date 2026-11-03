"""Stream a reply from an OpenAI-compatible model server.

Two request shapes are supported: the Responses API (POST /responses) and
Chat Completions (POST /chat/completions). Both stream server-sent events, and
both are reduced here to the same two events, so the relay does not care which
one the server speaks.
"""

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx

from .config import Settings


class UpstreamError(Exception):
    """The model server refused the request, failed, or broke off the stream."""


@dataclass(frozen=True)
class TextDelta:
    text: str


@dataclass(frozen=True)
class Finished:
    output_tokens: int | None


UpstreamEvent = TextDelta | Finished


async def iter_sse(lines: AsyncIterator[str]) -> AsyncIterator[tuple[str | None, str]]:
    """Yield (event name, data) for each event in a server-sent event stream."""
    event: str | None = None
    data: list[str] = []
    async for line in lines:
        if line == "":
            if data:
                yield event, "\n".join(data)
            event, data = None, []
            continue
        if line.startswith(":"):
            continue
        field, _, value = line.partition(":")
        if value.startswith(" "):
            value = value[1:]
        if field == "event":
            event = value
        elif field == "data":
            data.append(value)
    if data:
        yield event, "\n".join(data)


def build_request(settings: Settings, history: list[dict[str, str]]) -> tuple[str, dict[str, Any]]:
    """Return the path and JSON body for one streamed reply."""
    if settings.api_style == "responses":
        path = "/responses"
        body: dict[str, Any] = {
            "model": settings.model,
            "instructions": settings.instructions,
            "input": history,
            "max_output_tokens": settings.max_output_tokens,
            "stream": True,
        }
    else:
        path = "/chat/completions"
        body = {
            "model": settings.model,
            "messages": [{"role": "system", "content": settings.instructions}, *history],
            "max_tokens": settings.max_output_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
    if settings.temperature is not None:
        body["temperature"] = settings.temperature
    return path, body


async def stream_reply(
    client: httpx.AsyncClient, settings: Settings, history: list[dict[str, str]]
) -> AsyncIterator[UpstreamEvent]:
    """Send the history to the model server and yield the reply as it streams in.

    Leaving the loop early (the browser went away) closes the HTTP response,
    which closes the connection, so the server can stop generating.
    """
    path, body = build_request(settings, history)
    headers = {"Authorization": f"Bearer {settings.openai_api_key}", "Accept": "text/event-stream"}
    url = settings.openai_base_url + path
    try:
        async with client.stream("POST", url, json=body, headers=headers) as response:
            if response.status_code != 200:
                detail = (await response.aread()).decode("utf-8", "replace")[:300]
                raise UpstreamError(f"model server returned HTTP {response.status_code}: {detail}")
            parse = _parse_responses if settings.api_style == "responses" else _parse_chat_completions
            async for event in parse(iter_sse(response.aiter_lines())):
                yield event
    except httpx.HTTPError as exc:
        raise UpstreamError(f"lost the model server at {url}: {exc!r}") from exc


def _load(data: str) -> dict[str, Any]:
    try:
        payload = json.loads(data)
    except json.JSONDecodeError as exc:
        raise UpstreamError(f"model server sent an event that is not JSON: {data[:200]!r}") from exc
    if not isinstance(payload, dict):
        raise UpstreamError(f"model server sent an event that is not a JSON object: {data[:200]!r}")
    return payload


def _error_text(error: Any) -> str:
    if isinstance(error, dict):
        return str(error.get("message") or error.get("code") or error)
    return str(error)


async def _parse_responses(events: AsyncIterator[tuple[str | None, str]]) -> AsyncIterator[UpstreamEvent]:
    async for _name, data in events:
        payload = _load(data)
        kind = payload.get("type")
        if kind == "response.output_text.delta":
            if payload.get("delta"):
                yield TextDelta(payload["delta"])
        elif kind in ("response.completed", "response.incomplete"):
            usage = (payload.get("response") or {}).get("usage") or {}
            yield Finished(usage.get("output_tokens"))
            return
        elif kind == "response.failed":
            raise UpstreamError(_error_text((payload.get("response") or {}).get("error")))
        elif kind == "error":
            raise UpstreamError(_error_text(payload.get("error") or payload))
    raise UpstreamError("model server closed the stream before the response completed")


async def _parse_chat_completions(
    events: AsyncIterator[tuple[str | None, str]],
) -> AsyncIterator[UpstreamEvent]:
    output_tokens = None
    async for _name, data in events:
        if data == "[DONE]":
            yield Finished(output_tokens)
            return
        payload = _load(data)
        if "error" in payload:
            raise UpstreamError(_error_text(payload["error"]))
        usage = payload.get("usage")
        if usage:
            output_tokens = usage.get("completion_tokens")
        for choice in payload.get("choices") or []:
            text = (choice.get("delta") or {}).get("content")
            if text:
                yield TextDelta(text)
    raise UpstreamError("model server closed the stream before [DONE]")
