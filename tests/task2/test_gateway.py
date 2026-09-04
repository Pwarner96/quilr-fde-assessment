from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import jwt
import pytest

from fde_assessment.task2_mcp_gateway.gateway import (
    CORRELATION_RE,
    SERVICE_KEY,
    GatewaySettings,
    create_app,
)
from fde_assessment.task2_mcp_gateway.mocks import DownstreamState, transport_for

sys.path.insert(0, str(Path(__file__).parents[2] / "scripts"))
from mint_test_tokens import mint


def token(
    role: str = "viewer", settings: GatewaySettings | None = None, **changes: object
) -> str:
    s = settings or GatewaySettings()
    now = int(time.time())
    data: dict[str, object] = {
        "iss": s.issuer,
        "aud": s.audience,
        "sub": "u",
        "role": role,
        "iat": now,
        "exp": now + 300,
    }
    data.update(changes)
    return jwt.encode(data, s.signing_key, algorithm="HS256")


async def client_for(
    state: DownstreamState | None = None, forwarder: AsyncMock | None = None
):
    state = state or DownstreamState()

    async def forward(
        c: httpx.AsyncClient, u: str, p: dict[str, object], h: dict[str, str]
    ) -> httpx.Response:
        return await c.post(u, json=p, headers=h)

    forwarder = forwarder or AsyncMock(side_effect=forward)
    app = create_app(forwarder=forwarder)
    lifespan = app.router.lifespan_context(app)
    await lifespan.__aenter__()
    await app.state.http_client.aclose()
    app.state.http_client = httpx.AsyncClient(transport=transport_for(state))
    return (
        app,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://gateway"
        ),
        state,
        forwarder,
        lifespan,
    )


@pytest.mark.asyncio
async def test_viewer_admin_denial_is_zero_call() -> None:
    _app, client, state, forwarder, lifespan = await client_for()
    response = await client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {"name": "admin_reset_key"},
        },
        headers={"Authorization": f"Bearer {token()}"},
    )
    assert response.status_code == 200
    assert response.json() == {
        "jsonrpc": "2.0",
        "error": {"code": -32001, "message": "Unauthorized Tool Call"},
        "id": 7,
    }
    forwarder.assert_not_awaited()
    assert state.call_count == 0
    await client.aclose()
    await lifespan.__aexit__(None, None, None)


@pytest.mark.asyncio
async def test_auth_matrix_and_allowed_forwarding() -> None:
    _app, client, state, forwarder, lifespan = await client_for()
    body = {
        "jsonrpc": "2.0",
        "id": "x",
        "method": "tools/call",
        "params": {"name": "echo"},
    }
    assert (
        await client.post(
            "/mcp", json=body, headers={"Authorization": f"Bearer {token()}"}
        )
    ).status_code == 200
    assert forwarder.await_count == 1 and state.call_count == 1
    assert state.requests[0] == body
    assert "authorization" not in {
        k.lower() for k in state.headers[0]
    } or state.headers[0]["authorization"].endswith("0" * 32)
    for auth in (None, "Basic abc", "Bearer nope"):
        headers = {} if auth is None else {"Authorization": auth}
        assert (
            await client.post("/mcp", json=body, headers=headers)
        ).status_code == 401
    await client.aclose()
    await lifespan.__aexit__(None, None, None)


@pytest.mark.asyncio
async def test_raw_parser_duplicate_batch_notification_and_literal_policy() -> None:
    _app, client, _state, forwarder, lifespan = await client_for()
    h = {"Authorization": f"Bearer {token()}"}
    assert (
        await client.post(
            "/mcp",
            content=b'{"jsonrpc":"2.0","id":1,"id":2,"method":"tools/list"}',
            headers=h,
        )
    ).status_code == 400
    assert (
        await client.post(
            "/mcp",
            json=[{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}],
            headers=h,
        )
    ).status_code == 400
    assert (
        await client.post(
            "/mcp", json={"jsonrpc": "2.0", "method": "tools/list"}, headers=h
        )
    ).status_code == 400
    normal = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {"name": " ADMIN_reset_key"},
    }
    assert (await client.post("/mcp", json=normal, headers=h)).status_code == 200
    assert forwarder.await_count == 1
    await client.aclose()
    await lifespan.__aexit__(None, None, None)


@pytest.mark.asyncio
async def test_failure_responses_are_sanitized() -> None:
    for failure in (
        "timeout",
        "connection",
        "non_2xx",
        "non_json",
        "bad_rpc",
        "mismatch_id",
    ):
        state = DownstreamState(failure=failure)
        _app, client, _, _, lifespan = await client_for(state)
        response = await client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={"Authorization": f"Bearer {token()}"},
        )
        assert (
            response.status_code == 502
            and response.json()["error"]["message"] == "Downstream service failure"
        )
        assert "synthetic" not in response.text and "Traceback" not in response.text
        await client.aclose()
        await lifespan.__aexit__(None, None, None)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "auth",
    [
        None,
        "Basic abc",
        "Bearer",
        "Bearer nope",
        "Bearer " + "x" * 20,
    ],
)
async def test_authentication_failures_are_401(auth: str | None) -> None:
    _app, client, _state, _forwarder, lifespan = await client_for()
    headers = {} if auth is None else {"Authorization": auth}
    response = await client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers=headers,
    )
    assert response.status_code == 401
    await client.aclose()
    await lifespan.__aexit__(None, None, None)


@pytest.mark.asyncio
async def test_jwt_claim_matrix_and_algorithm_rejection() -> None:
    _app, client, _state, _forwarder, lifespan = await client_for()
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    settings = GatewaySettings()
    now = int(time.time())
    base: dict[str, object] = {
        "iss": settings.issuer,
        "aud": settings.audience,
        "sub": "u",
        "role": "viewer",
        "iat": now,
        "exp": now + 300,
    }
    cases = [
        {"iss": "wrong"},
        {"aud": "wrong"},
        {"sub": 3},
        {"role": 3},
        {"iat": "now"},
        {"exp": "later"},
        {"exp": now - 1},
        {"role": "operator"},
    ]
    for change in cases:
        claims = base | change
        signed = jwt.encode(claims, settings.signing_key, algorithm="HS256")
        response = await client.post(
            "/mcp", json=body, headers={"Authorization": f"Bearer {signed}"}
        )
        assert response.status_code == (403 if change == {"role": "operator"} else 401)
    invalid = jwt.encode(base, "wrong" + settings.signing_key, algorithm="HS256")
    hs384 = jwt.encode(base, settings.signing_key, algorithm="HS384")
    none_token = jwt.encode(base, key="", algorithm="none")
    for signed in (invalid, hs384, none_token):
        assert (
            await client.post(
                "/mcp", json=body, headers={"Authorization": f"Bearer {signed}"}
            )
        ).status_code == 401
    await client.aclose()
    await lifespan.__aexit__(None, None, None)


@pytest.mark.asyncio
async def test_strict_envelope_constants_and_no_forwarding() -> None:
    _app, client, _state, forwarder, lifespan = await client_for()
    headers = {"Authorization": f"Bearer {token()}"}
    invalid_bodies = [
        b"{",
        b'{"jsonrpc":"2.0","id":1,"method":"tools/list","x":NaN}',
    ]
    for raw in invalid_bodies:
        assert (
            await client.post("/mcp", content=raw, headers=headers)
        ).status_code == 400
    for payload in [
        {"jsonrpc": "1.0", "id": 1, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 1, "method": 4},
        {"jsonrpc": "2.0", "id": True, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": "bad"},
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": 4}},
    ]:
        assert (
            await client.post("/mcp", json=payload, headers=headers)
        ).status_code == 400
    assert forwarder.await_count == 0
    await client.aclose()
    await lifespan.__aexit__(None, None, None)


@pytest.mark.asyncio
async def test_forwarding_allowlist_destination_service_credential_and_safe_correlation() -> (
    None
):
    state = DownstreamState()
    _app, client, state, _forwarder, lifespan = await client_for(state)
    payload = {"jsonrpc": "2.0", "id": 9, "method": "tools/list"}
    headers = {
        "Authorization": f"Bearer {token()}",
        "X-Correlation-ID": "safe-9",
        "X-Ignore": "drop",
    }
    assert (await client.post("/mcp", json=payload, headers=headers)).status_code == 200
    received = state.headers[0]
    assert received["authorization"] == f"Bearer {SERVICE_KEY}"
    assert received["x-correlation-id"] == "safe-9"
    assert set(received) <= {
        "host",
        "content-length",
        "connection",
        "accept",
        "accept-encoding",
        "user-agent",
        "content-type",
        "x-correlation-id",
        "authorization",
    }
    assert all("caller" not in value for value in received.values())
    await client.aclose()
    await lifespan.__aexit__(None, None, None)


@pytest.mark.asyncio
async def test_unsafe_correlation_is_replaced_and_lifespan_client_reused_and_closed() -> (
    None
):
    calls: list[httpx.AsyncClient] = []
    outbound_headers: list[dict[str, str]] = []

    async def forward(
        client: httpx.AsyncClient,
        _url: str,
        payload: dict[str, object],
        headers: dict[str, str],
    ) -> httpx.Response:
        calls.append(client)
        outbound_headers.append(headers)
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "result": {}, "id": payload["id"]}
        )

    app = create_app(forwarder=forward)
    lifespan = app.router.lifespan_context(app)
    await lifespan.__aenter__()
    actual = app.state.http_client
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://gateway"
    ) as client:
        h = {
            "Authorization": f"Bearer {token()}",
            "X-Correlation-ID": "bad value/secret",
        }
        for i in (1, 2):
            assert (
                await client.post(
                    "/mcp",
                    json={"jsonrpc": "2.0", "id": i, "method": "tools/list"},
                    headers=h,
                )
            ).status_code == 200
    assert calls == [actual, actual]
    assert len(outbound_headers) == 2
    assert all(
        "bad value/secret" not in headers["x-correlation-id"]
        for headers in outbound_headers
    )
    assert all(
        CORRELATION_RE.fullmatch(headers["x-correlation-id"])
        for headers in outbound_headers
    )
    assert all(len(headers["x-correlation-id"]) == 32 for headers in outbound_headers)
    assert len({headers["x-correlation-id"] for headers in outbound_headers}) == 2
    assert not actual.is_closed
    await lifespan.__aexit__(None, None, None)
    assert actual.is_closed


@pytest.mark.asyncio
async def test_each_missing_required_claim_is_401_and_not_forwarded() -> None:
    _app, client, _state, forwarder, lifespan = await client_for()
    settings = GatewaySettings()
    now = int(time.time())
    claims: dict[str, object] = {
        "iss": settings.issuer,
        "aud": settings.audience,
        "sub": "u",
        "role": "viewer",
        "iat": now,
        "exp": now + 300,
    }
    for missing in ("iss", "aud", "sub", "role", "iat", "exp"):
        incomplete = {key: value for key, value in claims.items() if key != missing}
        signed = jwt.encode(incomplete, settings.signing_key, algorithm="HS256")
        response = await client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": missing, "method": "tools/list"},
            headers={"Authorization": f"Bearer {signed}"},
        )
        assert response.status_code == 401
    assert forwarder.await_count == 0
    await client.aclose()
    await lifespan.__aexit__(None, None, None)


@pytest.mark.asyncio
async def test_supported_roles_and_literal_tool_predicate_counts() -> None:
    state = DownstreamState()
    _app, client, state, forwarder, lifespan = await client_for(state)
    requests = [
        ("viewer", "tools/list", None, 200),
        ("admin", "tools/list", None, 200),
        ("admin", "tools/call", "admin_reset_key", 200),
        ("viewer", "tools/call", "admin_reset_key", 200),
        ("viewer", "tools/call", "Admin_reset_key", 200),
        ("viewer", "tools/call", "ADMIN_reset_key", 200),
        ("viewer", "tools/call", " admin_reset_key", 200),
    ]
    for role, method, name, expected_status in requests:
        payload: dict[str, object] = {
            "jsonrpc": "2.0",
            "id": state.call_count + forwarder.await_count + 1,
            "method": method,
        }
        if name is not None:
            payload["params"] = {"name": name}
        response = await client.post(
            "/mcp", json=payload, headers={"Authorization": f"Bearer {token(role)}"}
        )
        assert response.status_code == expected_status
    assert forwarder.await_count == 6
    assert state.call_count == 6
    await client.aclose()
    await lifespan.__aexit__(None, None, None)


@pytest.mark.asyncio
async def test_forwarder_receives_fixed_url_and_canonical_object() -> None:
    calls: list[tuple[str, dict[str, object], dict[str, str]]] = []

    async def capture(
        _client: httpx.AsyncClient,
        url: str,
        payload: dict[str, object],
        headers: dict[str, str],
    ) -> httpx.Response:
        calls.append((url, payload, headers))
        return httpx.Response(200, json={"jsonrpc": "2.0", "result": {}, "id": 44})

    settings = GatewaySettings(downstream_url="http://fixed.invalid/mcp")
    app = create_app(settings=settings, forwarder=capture)
    lifespan = app.router.lifespan_context(app)
    await lifespan.__aenter__()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://gateway"
    ) as client:
        payload = {
            "jsonrpc": "2.0",
            "id": 44,
            "method": "tools/call",
            "params": {"name": "echo", "arguments": {"url": "http://caller.invalid"}},
        }
        response = await client.post(
            "/mcp",
            json=payload,
            headers={
                "Authorization": f"Bearer {token(settings=settings)}",
                "X-Correlation-ID": "good-44",
                "X-Destination": "http://attacker.invalid",
            },
        )
    assert response.status_code == 200
    assert calls[0][0] == settings.downstream_url
    assert calls[0][1] == payload
    assert calls[0][2]["x-correlation-id"] == "good-44"
    await lifespan.__aexit__(None, None, None)


@pytest.mark.asyncio
async def test_strict_invalid_requests_include_notification_batch_duplicate_and_infinity() -> (
    None
):
    _app, client, _state, forwarder, lifespan = await client_for()
    headers = {"Authorization": f"Bearer {token()}"}
    invalid = [
        b'{"jsonrpc":"2.0","method":"tools/list"}',
        b'[{"jsonrpc":"2.0","id":1,"method":"tools/list"}]',
        b'{"jsonrpc":"2.0","id":1,"id":2,"method":"tools/list"}',
        b'{"jsonrpc":"2.0","id":1,"method":"tools/list","x":Infinity}',
    ]
    for raw in invalid:
        assert (
            await client.post("/mcp", content=raw, headers=headers)
        ).status_code == 400
    assert forwarder.await_count == 0
    await client.aclose()
    await lifespan.__aexit__(None, None, None)


@pytest.mark.asyncio
async def test_duplicate_keys_are_invalid_requests_with_null_id_and_zero_forwarding() -> (
    None
):
    _app, client, state, forwarder, lifespan = await client_for()
    headers = {"Authorization": f"Bearer {token()}"}
    duplicate_requests = [
        b'{"jsonrpc":"2.0","id":1,"id":2,"method":"tools/list"}',
        b'{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"echo","name":"admin_reset_key"}}',
    ]
    for raw in duplicate_requests:
        response = await client.post("/mcp", content=raw, headers=headers)
        assert response.status_code == 400
        assert response.json() == {
            "jsonrpc": "2.0",
            "error": {"code": -32600, "message": "Invalid Request"},
            "id": None,
        }
    malformed = await client.post("/mcp", content=b'{"jsonrpc":"2.0",', headers=headers)
    assert malformed.status_code == 400
    assert malformed.json()["error"]["code"] == -32700
    assert forwarder.await_count == 0
    assert state.call_count == 0
    await client.aclose()
    await lifespan.__aexit__(None, None, None)


@pytest.mark.asyncio
async def test_unexpected_exception_and_malformed_error_are_fully_sanitized() -> None:
    sentinels = [
        "INTERNAL_EXCEPTION_SENTINEL",
        "/private/secret/task2.py",
        "downstream.secret.invalid/mcp",
        "credential-secret-sentinel",
        "raw-request-body-sentinel",
    ]

    async def explode(*_args: object, **_kwargs: object) -> httpx.Response:
        raise RuntimeError(" ".join(sentinels))

    async def malformed(*_args: object, **_kwargs: object) -> httpx.Response:
        return httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": 1, "error": {"message": "bad"}, "result": {}},
        )

    for forwarder in (explode, malformed):
        app = create_app(forwarder=forwarder)
        lifespan = app.router.lifespan_context(app)
        await lifespan.__aenter__()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://gateway"
        ) as client:
            response = await client.post(
                "/mcp",
                content=b'{"jsonrpc":"2.0","id":1,"method":"tools/list","x":"raw-request-body-sentinel"}',
                headers={"Authorization": f"Bearer {token()}"},
            )
        assert response.status_code == 502
        assert "Downstream service failure" in response.text
        assert "Traceback" not in response.text
        assert all(sentinel not in response.text for sentinel in sentinels)
        await lifespan.__aexit__(None, None, None)


@pytest.mark.asyncio
async def test_token_helper_round_trip_uses_explicit_synthetic_settings() -> None:
    settings = GatewaySettings(signing_key="explicit-synthetic-key-" + "x" * 32)

    async def forward(*_args: object, **_kwargs: object) -> httpx.Response:
        return httpx.Response(200, json={"jsonrpc": "2.0", "result": {}, "id": 1})

    app = create_app(settings=settings, forwarder=forward)
    lifespan = app.router.lifespan_context(app)
    await lifespan.__aenter__()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://gateway"
    ) as client:
        response = await client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={"Authorization": f"Bearer {mint('viewer', settings)}"},
        )
    assert response.status_code == 200
    await lifespan.__aexit__(None, None, None)
