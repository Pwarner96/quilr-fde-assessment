"""Run a deterministic permitted and denied gateway call."""

from __future__ import annotations

import asyncio

import httpx
from mint_test_tokens import mint

from fde_assessment.task2_mcp_gateway.gateway import create_app
from fde_assessment.task2_mcp_gateway.mocks import DownstreamState, transport_for


async def main() -> None:
    state = DownstreamState()
    app = create_app()
    app.state.http_client = httpx.AsyncClient(transport=transport_for(state))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://gateway"
    ) as client:
        permitted = await client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "echo"},
            },
            headers={"Authorization": f"Bearer {mint('viewer')}"},
        )
        denied = await client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "admin_reset_key"},
            },
            headers={"Authorization": f"Bearer {mint('viewer')}"},
        )
        print(permitted.json())
        print(denied.json(), "downstream_calls=", state.call_count)
    await app.state.http_client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
