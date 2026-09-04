"""Runnable FastAPI completion boundary for the Task 4 core."""

from __future__ import annotations

import asyncio
import contextlib
import re
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, ValidationError

from .core import (
    CompletionRequest,
    DeterministicTokenCounter,
    Provider,
    ProviderRequest,
    ProviderResult,
    ProviderRouter,
    ProviderUsage,
    ReservationService,
    RouterOutcome,
)
from .limiter import LimiterBusyError, RateLimiter

_REQUEST_ID = re.compile(r"[A-Za-z0-9._-]{1,128}\Z")
_MAX_TENANT_KEY_LENGTH = 512


@dataclass(frozen=True)
class AppConfig:
    database_path: str | Path
    fingerprint_secret: bytes
    quota_limit: int
    primary_url: str
    secondary_url: str
    primary_credential: str = "configured-primary"
    secondary_credential: str = "configured-secondary"
    primary_timeout_ms: int = 3_000
    secondary_timeout_ms: int = 3_000

    def validate(self) -> None:
        if (
            type(self.fingerprint_secret) is not bytes
            or len(self.fingerprint_secret) < 32
        ):
            raise ValueError("fingerprint secret configuration is invalid")
        if type(self.quota_limit) is not int or self.quota_limit < 0:
            raise ValueError("quota configuration is invalid")
        if any(
            type(value) is not str or not value.strip()
            for value in (
                self.primary_url,
                self.secondary_url,
                self.primary_credential,
                self.secondary_credential,
            )
        ):
            raise ValueError("provider destination configuration is invalid")
        for timeout in (self.primary_timeout_ms, self.secondary_timeout_ms):
            if type(timeout) is not int or timeout <= 0:
                raise ValueError("timeout configuration is invalid")


class CompletionBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    prompt: StrictStr = Field(min_length=1)
    max_output_tokens: StrictInt = Field(gt=0)


class HttpProvider:
    """Fixed-destination adapter; it returns normalized core values only."""

    def __init__(
        self, role: str, url: str, credential: str, client: httpx.AsyncClient
    ) -> None:
        if role not in {"primary", "secondary"}:
            raise ValueError("invalid provider role")
        self.role = role
        self._url = url
        self._credential = credential
        self._client = client

    async def complete(self, request: ProviderRequest) -> ProviderResult:
        try:
            response = await self._client.post(
                self._url,
                headers={"Authorization": f"Bearer {self._credential}"},
                json={
                    "prompt": request.prompt,
                    "max_output_tokens": request.max_output_tokens,
                },
            )
        except httpx.HTTPError as error:
            raise RuntimeError("provider transport failed") from error
        if response.status_code == 429:
            return ProviderResult(429)
        if response.status_code != 200:
            return ProviderResult(response.status_code)
        try:
            body = response.json()
            if type(body) is not dict or type(body.get("text")) is not str:
                return ProviderResult(None)
            usage_data = body.get("usage")
            usage = None
            if usage_data is not None:
                if type(usage_data) is not dict:
                    return ProviderResult(None)
                if set(usage_data) != {
                    "prompt_tokens",
                    "completion_tokens",
                    "complete",
                    "trustworthy",
                }:
                    return ProviderResult(None)
                usage = ProviderUsage(
                    usage_data["prompt_tokens"],
                    usage_data["completion_tokens"],
                    usage_data["complete"],
                    usage_data["trustworthy"],
                )
            return ProviderResult(200, {"text": body["text"], "usage": usage}, usage)
        except (KeyError, TypeError, ValueError, ValidationError):
            return ProviderResult(None)

    async def cancel(self, request_id: str) -> None:
        return None


def _error(
    request_id: str, code: str, message: str, status: int, retryable: bool
) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={
            "error": {
                "code": code,
                "message": message,
                "request_id": request_id,
                "retryable": retryable,
            }
        },
    )


def _safe_request_id(value: str) -> str:
    return value if _REQUEST_ID.fullmatch(value) else ""


def create_app(
    config: AppConfig,
    *,
    primary: Provider | None = None,
    secondary: Provider | None = None,
    client_factory: Callable[[], httpx.AsyncClient] = httpx.AsyncClient,
) -> FastAPI:
    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        config.validate()
        limiter = RateLimiter(
            config.database_path,
            quota_limit=config.quota_limit,
            fingerprint_secret=config.fingerprint_secret,
        )
        limiter.initialize()
        client = client_factory()
        app.state.http_client = client
        app.state.limiter = limiter
        selected_primary = primary or HttpProvider(
            "primary", config.primary_url, config.primary_credential, client
        )
        selected_secondary = secondary or HttpProvider(
            "secondary", config.secondary_url, config.secondary_credential, client
        )
        app.state.router = ProviderRouter(
            ReservationService(limiter, DeterministicTokenCounter()),
            selected_primary,
            selected_secondary,
            primary_timeout_ms=config.primary_timeout_ms,
            secondary_timeout_ms=config.secondary_timeout_ms,
        )
        try:
            yield
        finally:
            await client.aclose()

    app = FastAPI(lifespan=lifespan)

    @app.exception_handler(RequestValidationError)
    async def validation_error(
        request: Request, _: RequestValidationError
    ) -> JSONResponse:
        return _error(
            _safe_request_id(request.headers.get("x-request-id", "")),
            "INVALID_REQUEST",
            "invalid request",
            400,
            False,
        )

    @app.post("/v1/completions")
    async def completions(
        request: Request,
        body: CompletionBody,
        x_tenant_api_key: str | None = Header(default=None),
        x_request_id: str | None = Header(default=None),
    ) -> Any:
        request_id = _safe_request_id(x_request_id or "")
        if (
            not x_tenant_api_key
            or not x_request_id
            or len(x_tenant_api_key) > _MAX_TENANT_KEY_LENGTH
            or not _REQUEST_ID.fullmatch(x_request_id)
            or not x_tenant_api_key.strip()
            or not x_request_id.strip()
            or not body.prompt.strip()
        ):
            return _error(request_id, "INVALID_REQUEST", "invalid request", 400, False)
        try:
            result = await app.state.router.complete(
                x_tenant_api_key,
                CompletionRequest(request_id, body.prompt, body.max_output_tokens),
            )
        except LimiterBusyError:
            return _error(
                request_id, "STATE_UNAVAILABLE", "state unavailable", 503, True
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - sanitize all core/provider failures
            return _error(
                request_id, "PROVIDER_UNAVAILABLE", "provider unavailable", 502, True
            )
        if result.outcome is RouterOutcome.DUPLICATE_REQUEST:
            return _error(
                request_id, "DUPLICATE_REQUEST", "duplicate request", 409, False
            )
        if result.outcome is RouterOutcome.QUOTA_EXCEEDED:
            return _error(request_id, "QUOTA_EXCEEDED", "quota exceeded", 429, True)
        if result.outcome in {
            RouterOutcome.PRIMARY_FAILURE,
            RouterOutcome.SECONDARY_FAILURE,
        }:
            return _error(
                request_id, "PROVIDER_UNAVAILABLE", "provider unavailable", 502, True
            )
        payload = result.result.body if result.result is not None else None
        if type(payload) is not dict or type(payload.get("text")) is not str:
            return _error(
                request_id, "PROVIDER_UNAVAILABLE", "provider unavailable", 502, True
            )
        usage = payload.get("usage")
        usage_payload = None
        if isinstance(usage, ProviderUsage):
            usage_payload = {
                "prompt_tokens": usage.prompt_tokens,
                "completion_tokens": usage.completion_tokens,
                "complete": usage.complete,
                "trustworthy": usage.trustworthy,
            }
        return {
            "request_id": request_id,
            "provider": result.provider,
            "completion": payload["text"],
            "usage": usage_payload,
        }

    return app
