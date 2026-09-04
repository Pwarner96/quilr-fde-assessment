"""Mint synthetic local HS256 tokens for the Task 2 demo."""

from __future__ import annotations

import argparse
import time

import jwt

from fde_assessment.task2_mcp_gateway.gateway import GatewaySettings


def mint(role: str, settings: GatewaySettings | None = None) -> str:
    config = settings or GatewaySettings()
    now = int(time.time())
    return jwt.encode(
        {
            "iss": config.issuer,
            "aud": config.audience,
            "sub": "synthetic-user",
            "role": role,
            "iat": now,
            "exp": now + 3600,
        },
        config.signing_key,
        algorithm="HS256",
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("role", choices=("admin", "viewer"))
    args = parser.parse_args()
    print(mint(args.role))
