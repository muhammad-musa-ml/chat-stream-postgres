"""HTTP API. Run with: uvicorn app.main:create_app --factory"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, StringConstraints

from . import db
from .config import Settings
from .relay import relay_reply


class ChatRequest(BaseModel):
    conversation_id: int | None = None
    message: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=8000)]


def create_app(settings: Settings | None = None, transport: httpx.AsyncBaseTransport | None = None) -> FastAPI:
    """Build the app. Tests pass a transport that stands in for the model server."""
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.pool = await db.create_pool(settings.database_url)
        # The read timeout applies between chunks, so a long reply is fine as
        # long as tokens keep arriving.
        app.state.client = httpx.AsyncClient(
            transport=transport, timeout=httpx.Timeout(settings.upstream_timeout_s, connect=5.0)
        )
        try:
            yield
        finally:
            await app.state.client.aclose()
            await app.state.pool.close()

    app = FastAPI(title="chat-stream-postgres", lifespan=lifespan)

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return {"ok": True, "model": settings.model, "api_style": settings.api_style}

    @app.get("/api/conversations")
    async def conversations(request: Request) -> list[dict[str, Any]]:
        return await db.list_conversations(request.app.state.pool)

    @app.get("/api/conversations/{conversation_id}/messages")
    async def messages(conversation_id: int, request: Request) -> list[dict[str, Any]]:
        rows = await db.list_messages(request.app.state.pool, conversation_id)
        if rows is None:
            raise HTTPException(status_code=404, detail="conversation not found")
        return rows

    @app.post("/api/chat")
    async def chat(body: ChatRequest, request: Request) -> StreamingResponse:
        pool = request.app.state.pool
        try:
            conversation_id, user_message_id = await db.add_user_message(pool, body.conversation_id, body.message)
        except db.ConversationNotFound:
            raise HTTPException(status_code=404, detail="conversation not found") from None
        # No pool connection is held while the reply streams: the relay takes
        # one for the history read and one for the final insert, each briefly.
        events = relay_reply(
            pool=pool,
            client=request.app.state.client,
            settings=settings,
            conversation_id=conversation_id,
            user_message_id=user_message_id,
        )
        return StreamingResponse(
            events,
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return app
