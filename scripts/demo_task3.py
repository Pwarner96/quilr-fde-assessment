"""Run the local Task 3 endpoint demonstration without a live provider."""

import asyncio

from fde_assessment.task3_stream_guardrail.app import MockProvider, create_app


async def main() -> None:
    provider = MockProvider(pause_before_remainder=True)
    app = create_app(provider)
    safe_seen = asyncio.Event()
    response_done = asyncio.Event()
    request_used = False
    bodies: list[bytes] = []

    async def receive() -> dict[str, object]:
        nonlocal request_used
        if request_used:
            await response_done.wait()
            return {"type": "http.disconnect"}
        request_used = True
        return {
            "type": "http.request",
            "body": b'{"model":"mock","messages":[{"role":"user","content":"demo"}],"stream":true}',
            "more_body": False,
        }

    async def send(message: dict[str, object]) -> None:
        if message["type"] != "http.response.body":
            return
        body = message.get("body", b"")
        assert isinstance(body, bytes)
        bodies.append(body)
        if b"Contact" in body:
            safe_seen.set()
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
                "server": ("demo", 80),
                "client": ("demo", 1),
                "http_version": "1.1",
            },
            receive,
            send,
        )
    )
    await provider.first_text_sent.wait()
    await asyncio.wait_for(safe_seen.wait(), timeout=2)
    assert not provider.release_remainder.is_set()
    print("provider first: Contact paul@exa")
    print("client observed before release: Contact ")
    provider.release_remainder.set()
    await task
    text = b"".join(bodies).decode()
    assert "[REDACTED]" in text and "now" in text and "[DONE]" in text
    print("provider remainder: mple.com now")
    print("final semantic text: Contact [REDACTED] now")
    assert provider.closed


if __name__ == "__main__":
    asyncio.run(main())
