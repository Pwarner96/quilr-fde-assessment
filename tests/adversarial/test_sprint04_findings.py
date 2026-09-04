from __future__ import annotations

import concurrent.futures
import json
import time
from pathlib import Path

import httpx
import jwt
import pytest

from fde_assessment.task2_mcp_gateway.gateway import (
    GatewaySettings,
    create_app,
)
from fde_assessment.task3_stream_guardrail.core import REDACTION, stream_core
from fde_assessment.task4_model_router.limiter import (
    AdmissionStatus,
    RateLimiter,
)


def _token(settings: GatewaySettings, role: str = "viewer") -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "iss": settings.issuer,
            "aud": settings.audience,
            "sub": "qa-synthetic-subject",
            "role": role,
            "iat": now,
            "exp": now + 300,
        },
        settings.signing_key,
        algorithm="HS256",
    )


@pytest.mark.asyncio
async def test_task2_auth_failure_forwards_zero_bytes() -> None:
    calls = 0

    async def spy(*_args: object, **_kwargs: object) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {}})

    settings = GatewaySettings(signing_key="qa-synthetic-signing-key-" + "x" * 32)
    app = create_app(settings=settings, forwarder=spy)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://qa"
        ) as client,
    ):
        response = await client.post(
            "/mcp",
            content=b'{"jsonrpc":"2.0","id":1,"method":"tools/list"}',
            headers={"Authorization": "Bearer invalid-synthetic-token"},
        )
    assert response.status_code == 401
    assert calls == 0


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=True,
    reason="Task 2 duplicate JSON keys currently normalize to parse error -32700",
)
async def test_task2_duplicate_keys_are_invalid_request_and_forward_zero_bytes() -> (
    None
):
    calls = 0

    async def spy(*_args: object, **_kwargs: object) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {}})

    settings = GatewaySettings(signing_key="qa-synthetic-signing-key-" + "x" * 32)
    app = create_app(settings=settings, forwarder=spy)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://qa"
        ) as client,
    ):
        response = await client.post(
            "/mcp",
            content=b'{"jsonrpc":"2.0","id":1,"id":2,"method":"tools/list"}',
            headers={"Authorization": "Bearer " + _token(settings)},
        )
    body = response.json()
    assert response.status_code == 400
    assert body["error"]["code"] == -32600
    assert calls == 0


def test_adversarial_fixture_has_no_live_credentials_or_pii() -> None:
    fixture = json.dumps(
        {
            "subject": "qa-synthetic-subject",
            "token": "invalid-synthetic-token",
            "email": "qa@example.invalid",
        }
    )
    assert "sk-" not in fixture
    assert "BEGIN PRIVATE KEY" not in fixture


def test_task3_every_two_way_sse_split_redacts_email() -> None:
    event = (
        b'data: {"choices":[{"delta":{"content":"Contact '
        b'paul@example.com now"}}]}\n\n'
        b"data: [DONE]\n\n"
    )
    for split in range(1, len(event)):
        output = "".join(stream_core([event[:split], event[split:]]))
        assert "paul@example.com" not in output
        assert REDACTION in output


def test_task4_exact_concurrent_boundary_has_atomic_admission(tmp_path: Path) -> None:
    limiter = RateLimiter(
        tmp_path / "qa-quota.sqlite",
        quota_limit=100,
        fingerprint_secret=b"q" * 32,
    )
    limiter.initialize()

    def reserve(index: int) -> AdmissionStatus:
        return limiter.admit(
            "qa-tenant",
            f"qa-request-{index}",
            estimated_input_tokens=3,
            max_output_tokens=4,
        ).status

    with concurrent.futures.ThreadPoolExecutor(max_workers=64) as pool:
        statuses = list(pool.map(reserve, range(64)))
    assert statuses.count(AdmissionStatus.ADMITTED) == 14
    assert statuses.count(AdmissionStatus.QUOTA_EXCEEDED) == 50
    assert limiter.active_charge("qa-tenant") == 98
