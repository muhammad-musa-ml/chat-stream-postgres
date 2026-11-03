"""Postgres access: the connection pool, the schema and every query the app runs."""

from pathlib import Path
from typing import Any

import asyncpg

SCHEMA = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")

MESSAGE_COLUMNS = "id, role, content, status, model, ttft_ms, total_ms, output_tokens, created_at"


class ConversationNotFound(Exception):
    pass


async def create_pool(dsn: str, max_size: int = 10) -> asyncpg.Pool:
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=max_size)
    await pool.execute(SCHEMA)
    return pool


def make_title(text: str, limit: int = 80) -> str:
    words = " ".join(text.split())
    return words if len(words) <= limit else words[: limit - 3].rstrip() + "..."


async def list_conversations(pool: asyncpg.Pool, limit: int = 50) -> list[dict[str, Any]]:
    rows = await pool.fetch(
        """
        SELECT id, title, created_at, updated_at
        FROM conversations
        ORDER BY updated_at DESC, id DESC
        LIMIT $1
        """,
        limit,
    )
    return [dict(row) for row in rows]


async def list_messages(pool: asyncpg.Pool, conversation_id: int) -> list[dict[str, Any]] | None:
    """All messages of one conversation, oldest first, or None if it does not exist."""
    async with pool.acquire() as conn:
        if await conn.fetchval("SELECT 1 FROM conversations WHERE id = $1", conversation_id) is None:
            return None
        rows = await conn.fetch(
            f"SELECT {MESSAGE_COLUMNS} FROM messages WHERE conversation_id = $1 ORDER BY id",
            conversation_id,
        )
    return [dict(row) for row in rows]


async def add_user_message(pool: asyncpg.Pool, conversation_id: int | None, text: str) -> tuple[int, int]:
    """Store what the user typed, starting a conversation if needed.

    Returns (conversation_id, message_id).
    """
    async with pool.acquire() as conn, conn.transaction():
        if conversation_id is None:
            conversation_id = await conn.fetchval(
                "INSERT INTO conversations (title) VALUES ($1) RETURNING id", make_title(text)
            )
        elif (
            await conn.fetchval(
                "UPDATE conversations SET updated_at = now() WHERE id = $1 RETURNING id", conversation_id
            )
            is None
        ):
            raise ConversationNotFound(conversation_id)
        message_id = await conn.fetchval(
            "INSERT INTO messages (conversation_id, role, content) VALUES ($1, 'user', $2) RETURNING id",
            conversation_id,
            text,
        )
    return conversation_id, message_id


async def recent_history(pool: asyncpg.Pool, conversation_id: int, limit: int) -> list[dict[str, str]]:
    """The last `limit` finished messages, oldest first, in the shape the model API takes.

    Replies that were cancelled or failed are left out, so the model never sees
    half an answer as if it had said it.
    """
    rows = await pool.fetch(
        """
        SELECT role, content FROM (
            SELECT id, role, content
            FROM messages
            WHERE conversation_id = $1 AND status = 'complete'
            ORDER BY id DESC
            LIMIT $2
        ) recent
        ORDER BY id
        """,
        conversation_id,
        limit,
    )
    return [{"role": row["role"], "content": row["content"]} for row in rows]


async def add_assistant_message(
    pool: asyncpg.Pool,
    *,
    conversation_id: int,
    content: str,
    status: str,
    model: str,
    ttft_ms: float | None,
    total_ms: float,
    output_tokens: int | None,
) -> int:
    async with pool.acquire() as conn, conn.transaction():
        message_id = await conn.fetchval(
            """
            INSERT INTO messages
                (conversation_id, role, content, status, model, ttft_ms, total_ms, output_tokens)
            VALUES ($1, 'assistant', $2, $3, $4, $5, $6, $7)
            RETURNING id
            """,
            conversation_id,
            content,
            status,
            model,
            ttft_ms,
            total_ms,
            output_tokens,
        )
        await conn.execute("UPDATE conversations SET updated_at = now() WHERE id = $1", conversation_id)
    return message_id
