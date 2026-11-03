"""When the browser goes away mid-reply, the partial reply is kept and the model stream is closed.

Starlette stops a streaming response in one of two ways, and both are covered:
it cancels the task while the relay waits on the model, or it stops iterating
while the relay is paused at a yield and the generator gets closed.
"""

import asyncio

import anyio
import httpx
import pytest
from fakes import FakeModelServer, make_settings, responses_completed, responses_created, responses_delta

from app import db
from app.relay import relay_reply

pytestmark = pytest.mark.anyio


def stalling_script(hold: asyncio.Event) -> list:
    """Two tokens, then the model stalls until `hold` is set."""
    return [
        responses_created(),
        responses_delta("Hal"),
        responses_delta("f"),
        hold,
        responses_delta(" more"),
        responses_completed(3),
    ]


async def stored_reply(pool):
    return await pool.fetchrow(
        "SELECT content, status, ttft_ms, total_ms, output_tokens FROM messages WHERE role = 'assistant'"
    )


async def test_cancel_while_waiting_on_the_model_stores_the_partial_reply(pool):
    server = FakeModelServer(stalling_script(asyncio.Event()))
    conversation_id, user_message_id = await db.add_user_message(pool, None, "hi")
    received: list[str] = []
    async with httpx.AsyncClient(transport=server.transport) as client:
        events = relay_reply(
            pool=pool,
            client=client,
            settings=make_settings(),
            conversation_id=conversation_id,
            user_message_id=user_message_id,
        )

        async def consume():
            async for chunk in events:
                received.append(chunk)

        async with anyio.create_task_group() as tg:
            tg.start_soon(consume)
            with anyio.fail_after(5):
                while sum(chunk.startswith("event: token") for chunk in received) < 2:
                    await anyio.sleep(0.01)
            # What Starlette does when it sees http.disconnect.
            tg.cancel_scope.cancel()

    row = await stored_reply(pool)
    assert (row["content"], row["status"], row["output_tokens"]) == ("Half", "cancelled", None)
    assert row["ttft_ms"] is not None and row["total_ms"] >= row["ttft_ms"]
    assert server.closed == 1
    assert not any(chunk.startswith(("event: done", "event: error")) for chunk in received)


async def test_closing_the_generator_at_a_yield_stores_the_partial_reply(pool):
    server = FakeModelServer(stalling_script(asyncio.Event()))
    conversation_id, user_message_id = await db.add_user_message(pool, None, "hi")
    async with httpx.AsyncClient(transport=server.transport) as client:
        events = relay_reply(
            pool=pool,
            client=client,
            settings=make_settings(),
            conversation_id=conversation_id,
            user_message_id=user_message_id,
        )
        assert (await anext(events)).startswith("event: meta")
        assert (await anext(events)).startswith("event: token")
        await events.aclose()
        # Checked before anything else awaits: the upstream stream must be
        # closed by the relay itself, not later by garbage collection.
        assert server.closed == 1

    row = await stored_reply(pool)
    assert (row["content"], row["status"]) == ("Hal", "cancelled")
