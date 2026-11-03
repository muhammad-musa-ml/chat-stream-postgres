"""The model-server client, against a scripted fake. No database needed."""

import httpx
import pytest
from fakes import FakeModelServer, chat_script, make_settings, responses_delta, responses_script, sse_event

from app.upstream import Finished, TextDelta, UpstreamError, iter_sse, stream_reply

pytestmark = pytest.mark.anyio

HISTORY = [{"role": "user", "content": "hi"}]


async def collect(server: FakeModelServer, **overrides):
    settings = make_settings(**overrides)
    async with httpx.AsyncClient(transport=server.transport) as client:
        return [event async for event in stream_reply(client, settings, HISTORY)]


async def lines(*items: str):
    for item in items:
        yield item


async def test_iter_sse_handles_comments_multiline_data_and_a_missing_final_blank_line():
    raw = [": keep-alive", "event: a", "data: one", "data: two", "", "data:no-space", ""]
    raw += ["event: b", "data: last"]
    events = [e async for e in iter_sse(lines(*raw))]
    assert events == [("a", "one\ntwo"), (None, "no-space"), ("b", "last")]


async def test_responses_stream_yields_each_delta_in_order_then_usage():
    server = FakeModelServer(responses_script(["Hel", "lo", " there"]))
    assert await collect(server) == [TextDelta("Hel"), TextDelta("lo"), TextDelta(" there"), Finished(3)]


async def test_responses_request_carries_history_instructions_and_token_cap():
    server = FakeModelServer(responses_script(["ok"]))
    await collect(server, temperature=0.0)
    assert server.paths == ["/v1/responses"]
    assert server.auth == ["Bearer test-key"]
    assert server.requests == [
        {
            "model": "test-model",
            "instructions": "Be brief.",
            "input": HISTORY,
            "max_output_tokens": 64,
            "stream": True,
            "temperature": 0.0,
        }
    ]


async def test_chat_completions_stream_yields_deltas_and_reads_usage_before_done():
    server = FakeModelServer(chat_script(["a", "b"]))
    events = await collect(server, api_style="chat_completions")
    assert events == [TextDelta("a"), TextDelta("b"), Finished(2)]
    assert server.paths == ["/v1/chat/completions"]
    body = server.requests[0]
    assert body["messages"] == [{"role": "system", "content": "Be brief."}, *HISTORY]
    assert body["stream_options"] == {"include_usage": True}
    assert body["max_tokens"] == 64


async def test_an_http_error_status_becomes_an_upstream_error_with_the_status():
    server = FakeModelServer(status=404)
    with pytest.raises(UpstreamError, match="HTTP 404"):
        await collect(server)


async def test_an_unreachable_server_becomes_an_upstream_error():
    server = FakeModelServer(error=httpx.ConnectError("connection refused"))
    with pytest.raises(UpstreamError, match="connection refused"):
        await collect(server)


async def test_a_stream_that_ends_before_completed_is_an_error_not_a_short_answer():
    server = FakeModelServer([responses_delta("half an")])
    with pytest.raises(UpstreamError, match="before the response completed"):
        await collect(server)


async def test_a_failed_response_event_carries_the_server_message():
    failed = sse_event(
        "response.failed",
        {"type": "response.failed", "response": {"error": {"code": "server_error", "message": "out of memory"}}},
    )
    server = FakeModelServer([responses_delta("x"), failed])
    with pytest.raises(UpstreamError, match="out of memory"):
        await collect(server)


async def test_an_event_that_is_not_json_is_an_error():
    server = FakeModelServer([sse_event("response.output_text.delta", "{not json")])
    with pytest.raises(UpstreamError, match="not JSON"):
        await collect(server)


async def test_leaving_early_closes_the_upstream_response():
    server = FakeModelServer(responses_script(["a", "b", "c", "d"]))
    async with httpx.AsyncClient(transport=server.transport) as client:
        stream = stream_reply(client, make_settings(), HISTORY)
        assert await anext(stream) == TextDelta("a")
        await stream.aclose()
    assert server.closed == 1
    assert server.sent < 6
