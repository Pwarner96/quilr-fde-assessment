"""Mock-first FastAPI streaming endpoint for the Task 3 compatibility subset."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Protocol

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictStr,
    ValidationError,
)

from .core import StreamingGuardrailCore


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: StrictStr
    messages: list[dict[str, object]] = Field(min_length=1)
    stream: StrictBool


class Provider(Protocol):
    def owned_stream(
        self, request: ChatRequest
    ) -> AbstractAsyncContextManager[AsyncIterator[bytes]]: ...


class MockProvider:
    """Deterministic provider with an explicit early-output synchronization barrier."""

    def __init__(self, pause_before_remainder: bool = False) -> None:
        self.first_text_sent = asyncio.Event()
        self.release_remainder = asyncio.Event()
        self.closed = False
        self.pause_before_remainder = pause_before_remainder
        self.pull_count = 0

    @asynccontextmanager
    async def owned_stream(
        self, request: ChatRequest
    ) -> AsyncIterator[AsyncIterator[bytes]]:
        try:
            yield self._bytes()
        finally:
            self.closed = True

    async def _bytes(self) -> AsyncIterator[bytes]:
        first = _packet("Contact paul@exa")
        second = _packet("mple.com now", "stop") + b"data: [DONE]\r\n\r\n"
        self.first_text_sent.set()
        for fragment in (first[:7], first[7:]):
            self.pull_count += 1
            yield fragment
        if self.pause_before_remainder:
            await self.release_remainder.wait()
        for fragment in (second[:3], second[3:19], second[19:]):
            self.pull_count += 1
            yield fragment


class HTTPXProvider:
    """Fixed-destination adapter using an application-owned AsyncClient."""

    def __init__(self, client: httpx.AsyncClient, destination: str) -> None:
        self._client = client
        self._destination = destination

    @asynccontextmanager
    async def owned_stream(
        self, request: ChatRequest
    ) -> AsyncIterator[AsyncIterator[bytes]]:
        async with self._client.stream(
            "POST", self._destination, json=request.model_dump()
        ) as response:
            response.raise_for_status()
            yield response.aiter_bytes()


def _packet(content: str, finish_reason: str | None = None) -> bytes:
    choice: dict[str, object] = {"delta": {"content": content}}
    if finish_reason is not None:
        choice["finish_reason"] = finish_reason
    return ("data: " + json.dumps({"choices": [choice]}) + "\n\n").encode()


def create_app(
    provider: MockProvider | HTTPXProvider | None = None,
    client_factory: Callable[[], httpx.AsyncClient] | None = None,
    destination: str | None = None,
) -> FastAPI:
    """Create an app with one strict, mockable streaming route."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        client: httpx.AsyncClient | None = None
        selected = provider
        if selected is None and (destination is not None or client_factory is not None):
            client = (
                client_factory() if client_factory is not None else httpx.AsyncClient()
            )
            selected = HTTPXProvider(
                client, destination or "https://provider.invalid/v1/chat/completions"
            )
        if selected is None:
            selected = MockProvider()
        app.state.provider = selected
        try:
            yield
        finally:
            if client is not None:
                await client.aclose()

    app = FastAPI(lifespan=lifespan)
    app.state.provider = provider or MockProvider()

    @app.post("/v1/chat/completions", response_model=None)
    async def completions(request: Request) -> StreamingResponse | JSONResponse:
        try:
            body = await request.json()
            parsed = ChatRequest.model_validate(body)
            if not parsed.stream:
                raise ValueError("stream must be true")
            if not parsed.model.strip():
                raise ValueError("model must not be blank")
            for message in parsed.messages:
                if (
                    set(message) != {"role", "content"}
                    or not isinstance(message["role"], str)
                    or not isinstance(message["content"], str)
                ):
                    raise ValueError(
                        "messages must contain only string role and content"
                    )
                if message["role"] not in {"user", "assistant", "system"}:
                    raise ValueError("unsupported message role")
                if not message["content"].strip():
                    raise ValueError("message content must not be blank")
        except (ValueError, ValidationError, json.JSONDecodeError):
            return JSONResponse(
                {"detail": "invalid streaming request"}, status_code=400
            )

        async def body_stream() -> AsyncIterator[str]:
            core = StreamingGuardrailCore()
            try:
                async with app.state.provider.owned_stream(parsed) as upstream:
                    async for fragment in upstream:
                        for output in core.feed(fragment):
                            yield output
                        if core.failed:
                            return
                    for output in core.finish():
                        yield output
            except asyncio.CancelledError:
                core.finish(normal=False)
                raise
            except (httpx.HTTPError, OSError):
                core.finish(normal=False)
                return
            except Exception:  # noqa: BLE001 - provider failures are fail-closed
                core.finish(normal=False)
                return
            finally:
                if not core.failed:
                    core.finish(normal=False)

        return StreamingResponse(body_stream(), media_type="text/event-stream")

    return app
