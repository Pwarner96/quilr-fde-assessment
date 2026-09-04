"""Authenticated, policy-enforcing MCP JSON-RPC reverse proxy."""

from __future__ import annotations

import json
import re
import secrets
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import httpx
import jwt
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

SERVICE_KEY = "task2-service-key-" + "0" * 32
SIGNING_KEY = secrets.token_urlsafe(48)
CORRELATION_RE = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")
UNAUTHORIZED = "Unauthorized Tool Call"
Forwarder = Callable[
    [httpx.AsyncClient, str, dict[str, Any], dict[str, str]], Awaitable[httpx.Response]
]


@dataclass(frozen=True)
class GatewaySettings:
    signing_key: str = SIGNING_KEY
    issuer: str = "quilr-task2"
    audience: str = "mcp-client"
    downstream_url: str = "http://mcp-downstream.invalid/mcp"
    service_credential: str | None = SERVICE_KEY


class GatewayError(Exception):
    def __init__(
        self, status: int, code: int, message: str, request_id: Any = None
    ) -> None:
        self.status, self.code, self.message, self.request_id = (
            status,
            code,
            message,
            request_id,
        )


def _error(code: int, message: str, request_id: Any = None) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "error": {"code": code, "message": message},
        "id": request_id,
    }


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def parse_jsonrpc(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_no_duplicates,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise GatewayError(400, -32700, "Parse error") from None
    if not isinstance(value, dict):
        raise GatewayError(400, -32600, "Invalid Request")
    if value.get("jsonrpc") != "2.0" or not isinstance(value.get("method"), str):
        raise GatewayError(400, -32600, "Invalid Request", value.get("id"))
    if (
        "id" not in value
        or value["id"] is None
        or isinstance(value["id"], (dict, list, bool))
    ):
        raise GatewayError(400, -32600, "Invalid Request", value.get("id"))
    if "params" in value and not isinstance(value["params"], (dict, list)):
        raise GatewayError(400, -32602, "Invalid params", value["id"])
    return value


def _claim_types_are_valid(claims: dict[str, Any]) -> bool:
    return (
        isinstance(claims.get("iss"), str)
        and isinstance(claims.get("aud"), str)
        and isinstance(claims.get("sub"), str)
        and isinstance(claims.get("role"), str)
        and isinstance(claims.get("iat"), int)
        and not isinstance(claims.get("iat"), bool)
        and isinstance(claims.get("exp"), int)
        and not isinstance(claims.get("exp"), bool)
    )


def authenticate(request: Request, settings: GatewaySettings) -> dict[str, Any]:
    header = request.headers.get("authorization")
    if (
        not header
        or not header.startswith("Bearer ")
        or not header[7:].strip()
        or header[7:].strip() != header[7:]
    ):
        raise GatewayError(401, -32000, "Authentication failed")
    token = header[7:]
    try:
        header_data = jwt.get_unverified_header(token)
        if header_data.get("alg") != "HS256":
            raise ValueError
        claims = jwt.decode(
            token,
            settings.signing_key,
            algorithms=["HS256"],
            issuer=settings.issuer,
            audience=settings.audience,
            options={"require": ["iss", "aud", "sub", "role", "iat", "exp"]},
        )
        if not _claim_types_are_valid(claims):
            raise ValueError
    except (jwt.PyJWTError, ValueError, TypeError, OverflowError):
        raise GatewayError(401, -32000, "Authentication failed") from None
    role = claims.get("role")
    if role not in {"admin", "viewer"}:
        raise GatewayError(403, -32003, "Forbidden")
    return claims


def authorize(message: dict[str, Any], claims: dict[str, Any]) -> None:
    if message["method"] != "tools/call":
        return
    params = message.get("params")
    if not isinstance(params, dict) or not isinstance(params.get("name"), str):
        raise GatewayError(400, -32602, "Invalid params", message["id"])
    if claims["role"] == "viewer" and params["name"].startswith("admin_"):
        raise GatewayError(200, -32001, UNAUTHORIZED, message["id"])


def create_app(
    settings: GatewaySettings | None = None, forwarder: Forwarder | None = None
) -> FastAPI:
    config = settings or GatewaySettings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.http_client = httpx.AsyncClient()
        try:
            yield
        finally:
            await app.state.http_client.aclose()

    app = FastAPI(lifespan=lifespan)

    async def default_forwarder(
        client: httpx.AsyncClient,
        url: str,
        payload: dict[str, Any],
        headers: dict[str, str],
    ) -> httpx.Response:
        return await client.post(url, json=payload, headers=headers)

    app.state.forwarder = forwarder or default_forwarder
    app.state.settings = config

    @app.post("/mcp")
    async def mcp(request: Request) -> JSONResponse:
        request_id: Any = None
        try:
            claims = authenticate(request, config)
            message = parse_jsonrpc(await request.body())
            request_id = message.get("id")
            authorize(message, claims)
            supplied_correlation = request.headers.get("x-correlation-id")
            correlation = (
                supplied_correlation
                if supplied_correlation
                and CORRELATION_RE.fullmatch(supplied_correlation)
                else secrets.token_hex(16)
            )
            headers = {
                "x-correlation-id": correlation,
                "content-type": "application/json",
            }
            if config.service_credential:
                headers["authorization"] = f"Bearer {config.service_credential}"
            response = await app.state.forwarder(
                app.state.http_client, config.downstream_url, message, headers
            )
            if response.status_code < 200 or response.status_code >= 300:
                raise GatewayError(
                    502, -32010, "Downstream service failure", request_id
                )
            try:
                downstream = response.json()
            except (ValueError, json.JSONDecodeError):
                raise GatewayError(
                    502, -32010, "Downstream service failure", request_id
                ) from None
            if (
                not isinstance(downstream, dict)
                or downstream.get("jsonrpc") != "2.0"
                or downstream.get("id") != request_id
                or ("result" in downstream and "error" in downstream)
                or ("result" not in downstream and "error" not in downstream)
            ):
                raise GatewayError(
                    502, -32010, "Downstream service failure", request_id
                )
            if "error" in downstream and (
                not isinstance(downstream["error"], dict)
                or not isinstance(downstream["error"].get("code"), int)
                or not isinstance(downstream["error"].get("message"), str)
            ):
                raise GatewayError(
                    502, -32010, "Downstream service failure", request_id
                )
            return JSONResponse(downstream)
        except GatewayError as exc:
            return JSONResponse(
                _error(exc.code, exc.message, exc.request_id), status_code=exc.status
            )
        except Exception:  # noqa: BLE001 - the HTTP boundary must sanitize all internal failures.
            return JSONResponse(
                _error(-32010, "Downstream service failure", request_id),
                status_code=502,
            )

    return app
