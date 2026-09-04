from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from fde_assessment.task4_model_router import (
    AppConfig,
    HttpProvider,
    MockProvider,
    ProviderRequest,
    ProviderResult,
    ProviderUsage,
    create_app,
)
from fde_assessment.task4_model_router.app import _safe_request_id


def config(path: Path, secret: bytes = b"z" * 32) -> AppConfig:
    return AppConfig(
        path, secret, 100, "https://primary.invalid", "https://secondary.invalid"
    )


def headers(request_id: str = "req-1") -> dict[str, str]:
    return {"X-Tenant-API-Key": "opaque-key", "X-Request-ID": request_id}


def test_success_envelope_and_strict_body(tmp_path: Path) -> None:
    primary = MockProvider(
        "primary",
        ProviderResult(
            200,
            {"text": "hello", "usage": ProviderUsage(1, 2, True, True)},
            ProviderUsage(1, 2, True, True),
        ),
    )
    app = create_app(
        config(tmp_path / "state.sqlite"),
        primary=primary,
        secondary=MockProvider("secondary"),
    )
    with TestClient(app) as client:
        response = client.post(
            "/v1/completions",
            headers=headers(),
            json={"prompt": "hello", "max_output_tokens": 4},
        )
    assert response.status_code == 200
    assert response.json() == {
        "request_id": "req-1",
        "provider": "primary",
        "completion": "hello",
        "usage": {
            "prompt_tokens": 1,
            "completion_tokens": 2,
            "complete": True,
            "trustworthy": True,
        },
    }
    assert primary.calls[0].event.value == "start"


@pytest.mark.parametrize(
    "headers_value, body",
    [
        ({}, {"prompt": "x", "max_output_tokens": 1}),
        (headers(), {"prompt": "", "max_output_tokens": 1}),
        (headers(), {"prompt": "x", "max_output_tokens": True}),
        (headers(), {"prompt": "x", "max_output_tokens": 1, "extra": 2}),
    ],
)
def test_invalid_request_is_exact_envelope(
    tmp_path: Path, headers_value: dict[str, str], body: dict[str, object]
) -> None:
    app = create_app(
        config(tmp_path / "state.sqlite"),
        primary=MockProvider("primary"),
        secondary=MockProvider("secondary"),
    )
    with TestClient(app) as client:
        response = client.post("/v1/completions", headers=headers_value, json=body)
    assert response.status_code == 400
    assert response.json() == {
        "error": {
            "code": "INVALID_REQUEST",
            "message": "invalid request",
            "request_id": headers_value.get("X-Request-ID", ""),
            "retryable": False,
        }
    }


@pytest.mark.parametrize("request_id", [" ", "bad/id", "x" * 129, "line\nfeed"])
def test_request_id_boundary_is_strict(tmp_path: Path, request_id: str) -> None:
    app = create_app(
        config(tmp_path / "state.sqlite"),
        primary=MockProvider("primary"),
        secondary=MockProvider("secondary"),
    )
    with TestClient(app) as client:
        response = client.post(
            "/v1/completions",
            headers=headers(request_id),
            json={"prompt": "x", "max_output_tokens": 1},
        )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_REQUEST"
    assert response.json()["error"]["request_id"] == ""


def test_unicode_request_id_is_rejected_by_boundary() -> None:
    assert _safe_request_id("ümlaut") == ""


@pytest.mark.parametrize(
    "field",
    ["primary_url", "secondary_url", "primary_credential", "secondary_credential"],
)
def test_blank_fixed_provider_configuration_fails_at_startup(
    tmp_path: Path, field: str
) -> None:
    values = {
        "database_path": tmp_path / "state.sqlite",
        "fingerprint_secret": b"v" * 32,
        "quota_limit": 10,
        "primary_url": "p",
        "secondary_url": "s",
    }
    values[field] = " "
    with pytest.raises(ValueError), TestClient(create_app(AppConfig(**values))):
        pass


def test_duplicate_and_quota_envelopes(tmp_path: Path) -> None:
    primary = MockProvider("primary", ProviderResult(200, {"text": "ok"}))
    app = create_app(
        AppConfig(tmp_path / "state.sqlite", b"q" * 32, 2, "p", "s"),
        primary=primary,
        secondary=MockProvider("secondary"),
    )
    with TestClient(app) as client:
        first = client.post(
            "/v1/completions",
            headers=headers("same"),
            json={"prompt": "x", "max_output_tokens": 1},
        )
        duplicate = client.post(
            "/v1/completions",
            headers=headers("same"),
            json={"prompt": "x", "max_output_tokens": 1},
        )
        quota = client.post(
            "/v1/completions",
            headers=headers("other"),
            json={"prompt": "x", "max_output_tokens": 1},
        )
    assert first.status_code == 200
    assert (
        duplicate.status_code == 409
        and duplicate.json()["error"]["code"] == "DUPLICATE_REQUEST"
    )
    assert (
        quota.status_code == 429 and quota.json()["error"]["code"] == "QUOTA_EXCEEDED"
    )
    assert [event.event.value for event in primary.calls].count("start") == 1
    assert [event.event.value for event in primary.calls].count("finish") == 1
    assert [event.event.value for event in primary.calls].count("cancel") == 0


def test_startup_secret_failure_and_lifespan_client_once(tmp_path: Path) -> None:
    with (
        pytest.raises(ValueError),
        TestClient(create_app(config(tmp_path / "bad.sqlite", b"short"))),
    ):
        pass
    counts = {"created": 0, "closed": 0}

    class CountingClient(httpx.AsyncClient):
        async def aclose(self) -> None:
            counts["closed"] += 1
            await super().aclose()

    def factory() -> httpx.AsyncClient:
        counts["created"] += 1
        return CountingClient()

    app = create_app(
        config(tmp_path / "good.sqlite"),
        primary=MockProvider("primary", ProviderResult(200, {"text": "ok"})),
        secondary=MockProvider("secondary"),
        client_factory=factory,
    )
    with TestClient(app):
        pass
    assert counts == {"created": 1, "closed": 1}


def test_provider_failure_response_is_sanitized(tmp_path: Path) -> None:
    sentinel = "provider-url-credential-body-sentinel"
    app = create_app(
        config(tmp_path / "state.sqlite"),
        primary=MockProvider("primary", ProviderResult(500, sentinel)),
        secondary=MockProvider("secondary"),
    )
    with TestClient(app) as client:
        response = client.post(
            "/v1/completions",
            headers=headers(),
            json={"prompt": "x", "max_output_tokens": 1},
        )
    assert response.status_code == 502
    assert response.json() == {
        "error": {
            "code": "PROVIDER_UNAVAILABLE",
            "message": "provider unavailable",
            "request_id": "req-1",
            "retryable": True,
        }
    }
    assert sentinel not in response.text


def test_exhausted_busy_is_state_error_and_makes_no_provider_call(
    tmp_path: Path,
) -> None:
    primary = MockProvider("primary", ProviderResult(200, {"text": "no"}))
    secondary = MockProvider("secondary", ProviderResult(200, {"text": "no"}))
    app = create_app(
        AppConfig(tmp_path / "state.sqlite", b"b" * 32, 100, "p", "s"),
        primary=primary,
        secondary=secondary,
    )
    with TestClient(app) as client:
        lock = sqlite3.connect(tmp_path / "state.sqlite")
        lock.execute("BEGIN IMMEDIATE")
        try:
            response = client.post(
                "/v1/completions",
                headers=headers(),
                json={"prompt": "x", "max_output_tokens": 1},
            )
        finally:
            lock.rollback()
            lock.close()
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "STATE_UNAVAILABLE"
    assert primary.calls == [] and secondary.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("content", [b"", b"not-json"])
async def test_http_adapter_classifies_429_before_body_parsing(content: bytes) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(429, content=content)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await HttpProvider(
            "primary", "https://fixed.invalid", "credential", client
        ).complete(ProviderRequest("request", "prompt", 3))
    assert result.status_code == 429 and result.body is None
    assert str(seen[0].url) == "https://fixed.invalid"
    assert seen[0].headers["authorization"] == "Bearer credential"


@pytest.mark.asyncio
async def test_http_adapter_malformed_200_is_normalized_to_failure() -> None:
    transport = httpx.MockTransport(
        lambda _: httpx.Response(200, json={"unexpected": "body-sentinel"})
    )
    async with httpx.AsyncClient(transport=transport) as client:
        result = await HttpProvider(
            "primary", "https://fixed.invalid", "credential", client
        ).complete(ProviderRequest("request", "prompt", 3))
    assert result.status_code is None and result.body is None and result.usage is None


def test_endpoint_429_falls_back_once_and_returns_success(tmp_path: Path) -> None:
    primary = MockProvider("primary", ProviderResult(429))
    secondary = MockProvider("secondary", ProviderResult(200, {"text": "fallback"}))
    app = create_app(
        config(tmp_path / "state.sqlite"), primary=primary, secondary=secondary
    )
    with TestClient(app) as client:
        response = client.post(
            "/v1/completions",
            headers=headers(),
            json={"prompt": "x", "max_output_tokens": 1},
        )
    assert response.status_code == 200 and response.json()["provider"] == "secondary"
    assert sum(event.event.value == "start" for event in primary.calls) == 1
    assert sum(event.event.value == "start" for event in secondary.calls) == 1


def test_endpoint_timeout_cleans_primary_before_secondary(tmp_path: Path) -> None:
    primary = MockProvider("primary", wait_for=asyncio.Event())
    secondary = MockProvider("secondary", ProviderResult(200, {"text": "fallback"}))
    app = create_app(
        AppConfig(
            tmp_path / "state.sqlite", b"t" * 32, 100, "p", "s", primary_timeout_ms=1
        ),
        primary=primary,
        secondary=secondary,
    )
    with TestClient(app) as client:
        response = client.post(
            "/v1/completions",
            headers=headers(),
            json={"prompt": "x", "max_output_tokens": 1},
        )
    assert response.status_code == 200
    assert [event.event.value for event in primary.calls] == ["start", "cancel"]
    assert secondary.calls[0].event.value == "start"


@pytest.mark.parametrize("status", [400, 401, 403, 500])
def test_endpoint_non_trigger_failures_are_exact_502(
    tmp_path: Path, status: int
) -> None:
    primary = MockProvider("primary", ProviderResult(status, "upstream-secret-body"))
    secondary = MockProvider("secondary", ProviderResult(200, {"text": "not-used"}))
    app = create_app(
        config(tmp_path / f"state-{status}.sqlite"),
        primary=primary,
        secondary=secondary,
    )
    with TestClient(app) as client:
        response = client.post(
            "/v1/completions",
            headers=headers(),
            json={"prompt": "x", "max_output_tokens": 1},
        )
    assert response.status_code == 502
    assert response.json() == {
        "error": {
            "code": "PROVIDER_UNAVAILABLE",
            "message": "provider unavailable",
            "request_id": "req-1",
            "retryable": True,
        }
    }
    assert secondary.calls == [] and "upstream-secret-body" not in response.text


@pytest.mark.parametrize(
    "primary_result", [None, ProviderResult(None, "malformed-result-sentinel")]
)
def test_endpoint_transport_and_malformed_results_are_exact_502(
    tmp_path: Path, primary_result: ProviderResult | None
) -> None:
    primary = MockProvider("primary", primary_result)
    secondary = MockProvider("secondary", ProviderResult(200, {"text": "unused"}))
    app = create_app(
        config(tmp_path / "state.sqlite"), primary=primary, secondary=secondary
    )
    with TestClient(app) as client:
        response = client.post(
            "/v1/completions",
            headers=headers(),
            json={"prompt": "x", "max_output_tokens": 1},
        )
    assert response.status_code == 502
    assert response.json() == {
        "error": {
            "code": "PROVIDER_UNAVAILABLE",
            "message": "provider unavailable",
            "request_id": "req-1",
            "retryable": True,
        }
    }
    assert sum(event.event.value == "start" for event in primary.calls) == 1
    assert secondary.calls == []
    assert "malformed-result-sentinel" not in response.text


def test_endpoint_both_provider_failure_is_exact_sanitized_502(tmp_path: Path) -> None:
    primary = MockProvider("primary", ProviderResult(429, "primary-body-sentinel"))
    secondary = MockProvider(
        "secondary", ProviderResult(500, "secondary-body-sentinel")
    )
    app = create_app(
        config(tmp_path / "both-failed.sqlite"),
        primary=primary,
        secondary=secondary,
    )
    with TestClient(app) as client:
        response = client.post(
            "/v1/completions",
            headers=headers(),
            json={"prompt": "x", "max_output_tokens": 1},
        )
    assert response.status_code == 502
    assert response.json() == {
        "error": {
            "code": "PROVIDER_UNAVAILABLE",
            "message": "provider unavailable",
            "request_id": "req-1",
            "retryable": True,
        }
    }
    assert sum(event.event.value == "start" for event in primary.calls) == 1
    assert sum(event.event.value == "start" for event in secondary.calls) == 1
    assert "primary-body-sentinel" not in response.text
    assert "secondary-body-sentinel" not in response.text


@pytest.mark.parametrize(
    "usage",
    [
        None,
        ProviderUsage(1, 1, False, True),
        ProviderUsage(1, 1, True, False),
        ProviderUsage(0, 2, True, True),
    ],
)
def test_endpoint_usage_reconciliation_only_lowers_complete_trustworthy(
    tmp_path: Path, usage: ProviderUsage | None
) -> None:
    body = {"text": "ok"} if usage is None else {"text": "ok", "usage": usage}
    primary = MockProvider("primary", ProviderResult(200, body, usage))
    app = create_app(
        config(tmp_path / f"usage-{usage is None}.sqlite"),
        primary=primary,
        secondary=MockProvider("secondary"),
    )
    with TestClient(app) as client:
        response = client.post(
            "/v1/completions",
            headers=headers(),
            json={"prompt": "x", "max_output_tokens": 4},
        )
    assert response.status_code == 200
    assert app.state.limiter.active_charge("opaque-key") == (
        5 if usage is None or not usage.complete or not usage.trustworthy else 2
    )
