"""The HTTP API end to end: the real app and Postgres, with a scripted model server."""

import pytest
from fakes import (
    FakeModelServer,
    chat_script,
    parse_sse,
    responses_created,
    responses_delta,
    responses_script,
    running_app,
)

pytestmark = pytest.mark.anyio


async def test_chat_relays_each_token_in_order_and_stores_both_messages_with_timings(pool):
    server = FakeModelServer(responses_script(["Hel", "lo", "!"]))
    async with running_app(server) as (_, client):
        response = await client.post("/api/chat", json={"message": "  say hello  "})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")

    events = parse_sse(response.text)
    assert [name for name, _ in events] == ["meta", "token", "token", "token", "done"]
    assert [data["text"] for name, data in events if name == "token"] == ["Hel", "lo", "!"]
    meta, done = events[0][1], events[-1][1]
    assert done["output_tokens"] == 3
    assert 0 <= done["ttft_ms"] <= done["total_ms"]

    rows = await pool.fetch("SELECT * FROM messages ORDER BY id")
    assert [(r["role"], r["content"], r["status"]) for r in rows] == [
        ("user", "say hello", "complete"),
        ("assistant", "Hello!", "complete"),
    ]
    reply = rows[1]
    assert reply["id"] == done["message_id"]
    assert reply["conversation_id"] == meta["conversation_id"]
    assert (reply["model"], reply["ttft_ms"], reply["total_ms"], reply["output_tokens"]) == (
        "test-model",
        done["ttft_ms"],
        done["total_ms"],
        3,
    )
    assert await pool.fetchval("SELECT title FROM conversations") == "say hello"


async def test_timings_measure_the_wait_for_the_first_token_and_the_whole_reply(pool):
    # 120 ms before the first token, then 3 more tokens 40 ms apart.
    script = [responses_created(), 0.12, responses_delta("a")]
    for token in ["b", "c", "d"]:
        script += [0.04, responses_delta(token)]
    script.append(responses_script([])[-1])
    server = FakeModelServer(script)
    async with running_app(server) as (_, client):
        response = await client.post("/api/chat", json={"message": "go"})
    done = parse_sse(response.text)[-1][1]
    assert 110 <= done["ttft_ms"] < 400
    assert done["total_ms"] - done["ttft_ms"] >= 110


async def test_a_follow_up_sends_the_stored_history_to_the_model(pool):
    server = FakeModelServer(responses_script(["Paris."]), responses_script(["About 2 million."]))
    async with running_app(server) as (_, client):
        first = parse_sse((await client.post("/api/chat", json={"message": "Capital of France?"})).text)
        conversation_id = first[0][1]["conversation_id"]
        await client.post("/api/chat", json={"conversation_id": conversation_id, "message": "Population?"})
    assert server.requests[1]["input"] == [
        {"role": "user", "content": "Capital of France?"},
        {"role": "assistant", "content": "Paris."},
        {"role": "user", "content": "Population?"},
    ]


async def test_history_is_capped_and_skips_replies_that_did_not_finish(pool):
    server = FakeModelServer(responses_script(["fine"]))
    async with running_app(server, history_messages=3) as (_, client):
        conversation_id = await pool.fetchval("INSERT INTO conversations (title) VALUES ('t') RETURNING id")
        for role, content, status in [
            ("user", "one", "complete"),
            ("assistant", "reply one", "complete"),
            ("user", "two", "complete"),
            ("assistant", "half a rep", "cancelled"),
        ]:
            await pool.execute(
                "INSERT INTO messages (conversation_id, role, content, status) VALUES ($1, $2, $3, $4)",
                conversation_id,
                role,
                content,
                status,
            )
        await client.post("/api/chat", json={"conversation_id": conversation_id, "message": "three"})
    assert server.requests[0]["input"] == [
        {"role": "assistant", "content": "reply one"},
        {"role": "user", "content": "two"},
        {"role": "user", "content": "three"},
    ]


async def test_an_upstream_failure_mid_reply_sends_an_error_event_and_stores_the_partial_reply(pool):
    server = FakeModelServer([responses_created(), responses_delta("Part")])
    async with running_app(server) as (_, client):
        events = parse_sse((await client.post("/api/chat", json={"message": "go"})).text)
    assert [name for name, _ in events] == ["meta", "token", "error"]
    assert "before the response completed" in events[-1][1]["error"]
    row = await pool.fetchrow("SELECT content, status, ttft_ms, total_ms FROM messages WHERE role = 'assistant'")
    assert (row["content"], row["status"]) == ("Part", "error")
    assert row["ttft_ms"] is not None and row["total_ms"] >= row["ttft_ms"]


async def test_a_model_server_that_refuses_the_request_is_reported_and_stored_as_an_error(pool):
    server = FakeModelServer(status=404)
    async with running_app(server) as (_, client):
        events = parse_sse((await client.post("/api/chat", json={"message": "go"})).text)
    assert [name for name, _ in events] == ["meta", "error"]
    assert "HTTP 404" in events[-1][1]["error"]
    row = await pool.fetchrow("SELECT content, status, ttft_ms FROM messages WHERE role = 'assistant'")
    assert (row["content"], row["status"], row["ttft_ms"]) == ("", "error", None)


async def test_chat_rejects_an_unknown_conversation_and_stores_nothing(pool):
    server = FakeModelServer()
    async with running_app(server) as (_, client):
        response = await client.post("/api/chat", json={"conversation_id": 999, "message": "hi"})
    assert response.status_code == 404
    assert await pool.fetchval("SELECT count(*) FROM messages") == 0
    assert server.requests == []


@pytest.mark.parametrize("message", ["", "   ", "x" * 8001])
async def test_chat_rejects_empty_or_oversized_messages(pool, message):
    async with running_app(FakeModelServer()) as (_, client):
        response = await client.post("/api/chat", json={"message": message})
    assert response.status_code == 422


async def test_conversation_list_puts_the_most_recently_active_first(pool):
    server = FakeModelServer(*[responses_script(["ok"]) for _ in range(3)])
    async with running_app(server) as (_, client):
        ids = []
        for text in ["first", "second"]:
            events = parse_sse((await client.post("/api/chat", json={"message": text})).text)
            ids.append(events[0][1]["conversation_id"])
        await client.post("/api/chat", json={"conversation_id": ids[0], "message": "again"})
        listed = (await client.get("/api/conversations")).json()
    assert [c["id"] for c in listed] == [ids[0], ids[1]]
    assert [c["title"] for c in listed] == ["first", "second"]


async def test_messages_endpoint_returns_the_conversation_in_order_with_timings(pool):
    server = FakeModelServer(responses_script(["4"]))
    async with running_app(server) as (_, client):
        events = parse_sse((await client.post("/api/chat", json={"message": "2+2?"})).text)
        conversation_id = events[0][1]["conversation_id"]
        messages = (await client.get(f"/api/conversations/{conversation_id}/messages")).json()
        missing = await client.get("/api/conversations/12345/messages")
    assert [(m["role"], m["content"]) for m in messages] == [("user", "2+2?"), ("assistant", "4")]
    assert messages[0]["ttft_ms"] is None
    assert messages[1]["ttft_ms"] == events[-1][1]["ttft_ms"]
    assert missing.status_code == 404


async def test_chat_completions_style_works_through_the_whole_app(pool):
    server = FakeModelServer(chat_script(["Hi", "!"]))
    async with running_app(server, api_style="chat_completions") as (_, client):
        events = parse_sse((await client.post("/api/chat", json={"message": "hello"})).text)
    assert [d["text"] for n, d in events if n == "token"] == ["Hi", "!"]
    assert events[-1][1]["output_tokens"] == 2
    assert await pool.fetchval("SELECT content FROM messages WHERE role = 'assistant'") == "Hi!"
