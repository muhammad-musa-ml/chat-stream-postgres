"""Relay one model reply to the browser as server-sent events, then store it."""

import json
import time
from collections.abc import AsyncIterator
from contextlib import aclosing
from typing import Any

import anyio
import asyncpg
import httpx

from . import db
from .config import Settings
from .upstream import Finished, TextDelta, UpstreamError, stream_reply


def sse(event: str, data: dict[str, Any]) -> str:
    # The payload is JSON, so a newline inside a token never breaks the event framing.
    return f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


async def relay_reply(
    *,
    pool: asyncpg.Pool,
    client: httpx.AsyncClient,
    settings: Settings,
    conversation_id: int,
    user_message_id: int,
) -> AsyncIterator[str]:
    """Yield the SSE events for one reply: meta, token..., then done or error.

    Time to first token and total time are measured here, from just before the
    request to the model server until its first text and its last event. The
    reply is stored whatever happens: finished, failed upstream, or cut off
    because the browser went away.
    """
    yield sse("meta", {"conversation_id": conversation_id, "user_message_id": user_message_id})
    history = await db.recent_history(pool, conversation_id, settings.history_messages)

    parts: list[str] = []
    ttft_ms: float | None = None
    output_tokens: int | None = None
    status, error = "cancelled", None
    started = time.perf_counter()
    try:
        # aclosing() closes the upstream stream (and its connection) even when
        # this generator is closed while paused at one of its own yields.
        async with aclosing(stream_reply(client, settings, history)) as upstream:
            async for event in upstream:
                if isinstance(event, TextDelta):
                    if ttft_ms is None:
                        ttft_ms = (time.perf_counter() - started) * 1000
                    parts.append(event.text)
                    yield sse("token", {"text": event.text})
                elif isinstance(event, Finished):
                    output_tokens = event.output_tokens
        status = "complete"
    except UpstreamError as exc:
        status, error = "error", str(exc)
    finally:
        total_ms = (time.perf_counter() - started) * 1000
        # When the browser disconnects, the server cancels this generator. The
        # partial reply is still saved, so the save is shielded from that cancel.
        with anyio.CancelScope(shield=True):
            message_id = await db.add_assistant_message(
                pool,
                conversation_id=conversation_id,
                content="".join(parts),
                status=status,
                model=settings.model,
                ttft_ms=ttft_ms,
                total_ms=total_ms,
                output_tokens=output_tokens,
            )

    if status == "complete":
        yield sse(
            "done",
            {
                "message_id": message_id,
                "model": settings.model,
                "ttft_ms": ttft_ms,
                "total_ms": total_ms,
                "output_tokens": output_tokens,
            },
        )
    else:
        yield sse("error", {"message_id": message_id, "error": error})
