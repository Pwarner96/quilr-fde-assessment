from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import pytest

from fde_assessment.task3_stream_guardrail.app import (
    ChatRequest,
    HTTPXProvider,
    MockProvider,
    create_app,
)


def _request_body() -> bytes:
    return b'{"model":"mock","messages":[{"role":"user","content":"x"}],"stream":true}'


class PayloadProvider:
    def __init__(self, payload: bytes | BaseException) -> None:
        self.payload = payload
        self.closed = False

    @asynccontextmanager
    async def owned_stream(
        self, request: object
    ) -> AsyncIterator[AsyncIterator[bytes]]:
        try:

            async def stream() -> AsyncIterator[bytes]:
                if isinstance(self.payload, BaseException):
                    raise self.payload
                yield self.payload

            yield stream()
        finally:
            self.closed = True


@pytest.mark.asyncio
async def test_streaming_route_redacts_and_closes_provider() -> None:
    provider = MockProvider()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(provider)), base_url="http://test"
    ) as client:
        response = await client.post(
            "/v1/chat/completions",
            json={
                "model": "mock",
                "messages": [{"role": "user", "content": "x"}],
                "stream": True,
            },
        )
    assert response.status_code == 200
    assert "paul@example.com" not in response.text
    assert response.text.count("[DONE]") == 1
    assert provider.closed


@pytest.mark.asyncio
async def test_strict_body_rejection() -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="http://test"
    ) as client:
        response = await client.post(
            "/v1/chat/completions", json={"model": "mock", "stream": True}
        )
    assert response.status_code == 400
    assert "paul@example.com" not in response.text


@pytest.mark.asyncio
async def test_httpx_adapter_uses_fixed_destination_and_streams_bytes() -> None:
    seen: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, content=b"data: x\n\n")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = HTTPXProvider(client, "https://fixed.invalid/chat")
    request = ChatRequest(
        model="m", messages=[{"role": "user", "content": "x"}], stream=True
    )
    async with provider.owned_stream(request) as upstream:
        assert [part async for part in upstream] == [b"data: x\n\n"]
    await client.aclose()
    assert seen == ["https://fixed.invalid/chat"]


@pytest.mark.asyncio
async def test_early_barrier_is_before_provider_remainder() -> None:
    provider = MockProvider(pause_before_remainder=True)
    app = create_app(provider)
    seen: list[bytes] = []
    safe_chunk_seen = asyncio.Event()
    request_sent = False
    response_done = asyncio.Event()

    async def receive() -> dict[str, object]:
        nonlocal request_sent
        if request_sent:
            await response_done.wait()
            return {"type": "http.disconnect"}
        request_sent = True
        return {
            "type": "http.request",
            "body": b'{"model":"mock","messages":[{"role":"user","content":"x"}],"stream":true}',
            "more_body": False,
        }

    async def send(message: dict[str, object]) -> None:
        if message["type"] == "http.response.body":
            body = message.get("body", b"")
            assert isinstance(body, bytes)
            seen.append(body)
            if b"Contact" in body:
                safe_chunk_seen.set()
            if not message.get("more_body", False):
                response_done.set()

    task = asyncio.create_task(
        app(
            {
                "type": "http",
                "method": "POST",
                "path": "/v1/chat/completions",
                "headers": [],
                "query_string": b"",
                "scheme": "http",
                "server": ("test", 80),
                "client": ("test", 1),
                "http_version": "1.1",
            },
            receive,
            send,
        )
    )
    await provider.first_text_sent.wait()
    await asyncio.sleep(0)
    await asyncio.wait_for(safe_chunk_seen.wait(), timeout=2)
    assert not provider.release_remainder.is_set()
    assert not task.done()
    provider.release_remainder.set()
    await task
    assert "Contact" in b"".join(seen).decode()


@pytest.mark.asyncio
async def test_blocked_send_does_not_pull_provider_remainder() -> None:
    provider = MockProvider(pause_before_remainder=True)
    app = create_app(provider)
    send_started = asyncio.Event()
    release_send = asyncio.Event()
    request_used = False
    response_done = asyncio.Event()

    async def receive() -> dict[str, object]:
        nonlocal request_used
        if request_used:
            await response_done.wait()
            return {"type": "http.disconnect"}
        request_used = True
        return {"type": "http.request", "body": _request_body(), "more_body": False}

    async def send(message: dict[str, object]) -> None:
        if message["type"] == "http.response.body" and b"Contact" in message.get(
            "body", b""
        ):
            send_started.set()
            await release_send.wait()
        if message["type"] == "http.response.body" and not message.get(
            "more_body", False
        ):
            response_done.set()

    task = asyncio.create_task(
        app(
            {
                "type": "http",
                "method": "POST",
                "path": "/v1/chat/completions",
                "headers": [],
                "query_string": b"",
                "scheme": "http",
                "server": ("test", 80),
                "client": ("test", 1),
                "http_version": "1.1",
            },
            receive,
            send,
        )
    )
    await asyncio.wait_for(send_started.wait(), timeout=2)
    pulls_while_blocked = provider.pull_count
    await asyncio.sleep(0)
    assert provider.pull_count == pulls_while_blocked == 2
    assert not hasattr(app.state, "queue")
    release_send.set()
    provider.release_remainder.set()
    await task


@pytest.mark.asyncio
async def test_default_lifespan_remains_mock_first() -> None:
    app = create_app()
    async with app.router.lifespan_context(app):
        assert isinstance(app.state.provider, MockProvider)


@pytest.mark.asyncio
async def test_cancel_after_safe_body_closes_provider_without_terminal_frames() -> None:
    provider = MockProvider(pause_before_remainder=True)
    app = create_app(provider)
    safe = asyncio.Event()
    done = asyncio.Event()
    request_used = False
    body: list[bytes] = []

    async def receive() -> dict[str, object]:
        nonlocal request_used
        if request_used:
            await done.wait()
            return {"type": "http.disconnect"}
        request_used = True
        return {"type": "http.request", "body": _request_body(), "more_body": False}

    async def send(message: dict[str, object]) -> None:
        if message["type"] == "http.response.body":
            value = message.get("body", b"")
            assert isinstance(value, bytes)
            body.append(value)
            if b"Contact" in value:
                safe.set()

    task = asyncio.create_task(
        app(
            {
                "type": "http",
                "method": "POST",
                "path": "/v1/chat/completions",
                "headers": [],
                "query_string": b"",
                "scheme": "http",
                "server": ("test", 80),
                "client": ("test", 1),
                "http_version": "1.1",
            },
            receive,
            send,
        )
    )
    await asyncio.wait_for(safe.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert provider.closed
    assert b"[DONE]" not in b"".join(body)
    assert b"finish_reason" not in b"".join(body)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        b'data: {"choices":[{"delta":{"content":"hidden@sample.com"}}]}\n\n',
        b"data: {bad}\n\n",
        b"\xff",
        b"data: " + b"x" * 70_000 + b"\n\n",
    ],
)
async def test_abnormal_provider_inputs_close_and_emit_no_terminal_or_error(
    payload: bytes,
) -> None:
    provider = PayloadProvider(payload)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(provider)), base_url="http://test"
    ) as client:
        response = await client.post(
            "/v1/chat/completions",
            json={
                "model": "mock",
                "messages": [{"role": "user", "content": "x"}],
                "stream": True,
            },
        )
    assert provider.closed
    assert "[DONE]" not in response.text
    assert "event:error" not in response.text
    assert "hidden@sample.com" not in response.text


@pytest.mark.asyncio
async def test_provider_sentinel_failure_is_normalized() -> None:
    provider = PayloadProvider(RuntimeError("SENTINEL_PROVIDER_FAILURE"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(provider)), base_url="http://test"
    ) as client:
        response = await client.post(
            "/v1/chat/completions",
            json={
                "model": "mock",
                "messages": [{"role": "user", "content": "x"}],
                "stream": True,
            },
        )
    assert provider.closed
    assert "SENTINEL_PROVIDER_FAILURE" not in response.text
    assert "[DONE]" not in response.text


@pytest.mark.asyncio
async def test_lifespan_creates_and_closes_one_client() -> None:
    class CountingClient(httpx.AsyncClient):
        created = 0
        closed_count = 0

        def __init__(self) -> None:
            type(self).created += 1
            super().__init__()

        async def aclose(self) -> None:
            type(self).closed_count += 1
            await super().aclose()

    app = create_app(client_factory=CountingClient)
    async with app.router.lifespan_context(app):
        assert CountingClient.created == 1
    assert CountingClient.closed_count == 1
