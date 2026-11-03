"""The constraints in schema.sql reject rows the app should never write."""

import asyncpg
import pytest

from app.db import SCHEMA

pytestmark = pytest.mark.anyio

INSERT = """
    INSERT INTO messages (conversation_id, role, content, status, model, ttft_ms, total_ms)
    VALUES ($1, $2, $3, $4, $5, $6, $7)
"""


async def new_conversation(pool) -> int:
    return await pool.fetchval("INSERT INTO conversations (title) VALUES ('t') RETURNING id")


async def test_schema_can_be_applied_twice(pool):
    await pool.execute(SCHEMA)


@pytest.mark.parametrize(
    "row",
    [
        ("system", "x", "complete", None, None, None),  # unknown role
        ("assistant", "x", "done", "m", 1.0, 2.0),  # unknown status
        ("assistant", "x", "complete", "m", 5.0, 2.0),  # first token after the end
        ("assistant", "x", "complete", "m", -1.0, 2.0),  # negative time
        ("user", "x", "complete", None, 1.0, 2.0),  # timings on a user message
        ("user", "x", "cancelled", None, None, None),  # only replies get cut off
    ],
)
async def test_bad_message_rows_are_rejected(pool, row):
    conversation_id = await new_conversation(pool)
    with pytest.raises(asyncpg.CheckViolationError):
        await pool.execute(INSERT, conversation_id, *row)


async def test_a_message_needs_an_existing_conversation(pool):
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await pool.execute(INSERT, 404, "user", "x", "complete", None, None, None)


async def test_an_empty_title_is_rejected(pool):
    with pytest.raises(asyncpg.CheckViolationError):
        await pool.execute("INSERT INTO conversations (title) VALUES ('')")


async def test_deleting_a_conversation_deletes_its_messages(pool):
    keep, drop = await new_conversation(pool), await new_conversation(pool)
    for conversation_id in (keep, drop):
        await pool.execute(INSERT, conversation_id, "user", "x", "complete", None, None, None)
    await pool.execute("DELETE FROM conversations WHERE id = $1", drop)
    assert await pool.fetchval("SELECT array_agg(conversation_id) FROM messages") == [keep]
