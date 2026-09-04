"""Deterministic downstream MCP service state and transport seam."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx


@dataclass
class DownstreamState:
    call_count: int = 0
    requests: list[dict[str, Any]] = field(default_factory=list)
    headers: list[dict[str, str]] = field(default_factory=list)
    failure: str | None = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.call_count += 1
        import json

        body = json.loads(request.content)
        self.requests.append(body)
        self.headers.append(dict(request.headers))
        if self.failure == "timeout":
            raise httpx.ReadTimeout("synthetic timeout")
        if self.failure == "connection":
            raise httpx.ConnectError("synthetic refusal")
        if self.failure == "non_json":
            return httpx.Response(200, content=b"not-json")
        if self.failure == "non_2xx":
            return httpx.Response(503, json={"secret": "hidden"})
        if self.failure == "bad_rpc":
            return httpx.Response(
                200,
                json={"jsonrpc": "1.0", "result": {"ok": True}, "id": body.get("id")},
            )
        if self.failure == "mismatch_id":
            return httpx.Response(
                200, json={"jsonrpc": "2.0", "result": {"ok": True}, "id": "wrong"}
            )
        method = body.get("method")
        if method == "tools/list":
            result: dict[str, Any] = {
                "tools": [{"name": "echo"}, {"name": "admin_reset_key"}]
            }
        else:
            result = {
                "ok": True,
                "method": method,
                "name": body.get("params", {}).get("name"),
            }
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "result": result, "id": body.get("id")}
        )


def transport_for(state: DownstreamState) -> httpx.MockTransport:
    return httpx.MockTransport(state.handler)
