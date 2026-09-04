"""Deterministic endpoint-level Task 4 demonstration; never uses live network."""

from __future__ import annotations

import asyncio
import sqlite3
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi.testclient import TestClient

from fde_assessment.task4_model_router import (
    AppConfig,
    MockProvider,
    ProviderResult,
    create_app,
)


def make_config(path: Path, quota: int = 100, timeout: int = 3_000) -> AppConfig:
    return AppConfig(
        path,
        b"demo-fingerprint-secret-32-bytes!",
        quota,
        "https://fixed.primary",
        "https://fixed.secondary",
        primary_timeout_ms=timeout,
    )


def call(client: TestClient, request_id: str):
    return client.post(
        "/v1/completions",
        headers={"X-Tenant-API-Key": "demo-opaque-key", "X-Request-ID": request_id},
        json={"prompt": "one", "max_output_tokens": 6},
    )


with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    primary = MockProvider(
        "primary",
        ProviderResult(
            200,
            {"text": "ok"},
            None,
        ),
    )
    secondary = MockProvider("secondary", ProviderResult(200, {"text": "fallback"}))
    app = create_app(
        make_config(root / "race.sqlite"), primary=primary, secondary=secondary
    )
    with TestClient(app) as client:

        def race(index: int) -> int:
            return call(client, f"race-{index}").status_code

        with ThreadPoolExecutor(max_workers=64) as pool:
            statuses = list(pool.map(race, range(64)))
        active_charge = app.state.limiter.active_charge("demo-opaque-key")
        busy_or_state = sum(status not in {200, 429} for status in statuses)
    print(
        "killer race:",
        "admitted=",
        statuses.count(200),
        "quota_rejected=",
        statuses.count(429),
        "busy_or_state=",
        busy_or_state,
        "active_charge=",
        active_charge,
    )
    print("killer race actual active_charge=", active_charge)
    assert statuses.count(200) == 14
    assert statuses.count(429) == 50
    assert busy_or_state == 0
    assert active_charge == 98

    for (
        label,
        primary_result,
        secondary_result,
        expected_status,
        expected_secondary,
    ) in (
        (
            "primary-success",
            ProviderResult(200, {"text": "ok"}),
            ProviderResult(200),
            200,
            0,
        ),
        (
            "429-fallback",
            ProviderResult(429),
            ProviderResult(200, {"text": "fallback"}),
            200,
            1,
        ),
        (
            "non-trigger-500",
            ProviderResult(500, "hidden-body"),
            ProviderResult(200),
            502,
            0,
        ),
    ):
        p = MockProvider("primary", primary_result)
        s = MockProvider("secondary", secondary_result)
        scenario = create_app(
            make_config(root / f"{label}.sqlite"), primary=p, secondary=s
        )
        with TestClient(scenario) as client:
            response = call(client, label)
        primary_attempts = [event.event.value for event in p.calls].count("start")
        secondary_attempts = [event.event.value for event in s.calls].count("start")
        assert response.status_code == expected_status
        assert primary_attempts == 1
        assert secondary_attempts == expected_secondary
        print(
            label,
            "status=",
            response.status_code,
            "primary_attempts=",
            primary_attempts,
            "secondary_attempts=",
            secondary_attempts,
        )

    deadline_primary = MockProvider("primary", wait_for=asyncio.Event())
    deadline_secondary = MockProvider(
        "secondary", ProviderResult(200, {"text": "deadline-fallback"})
    )
    deadline_app = create_app(
        make_config(root / "deadline.sqlite", timeout=1),
        primary=deadline_primary,
        secondary=deadline_secondary,
    )
    with TestClient(deadline_app) as client:
        response = call(client, "deadline")
    deadline_primary_attempts = [
        event.event.value for event in deadline_primary.calls
    ].count("start")
    deadline_secondary_attempts = [
        event.event.value for event in deadline_secondary.calls
    ].count("start")
    assert response.status_code == 200
    assert deadline_primary_attempts == 1
    assert deadline_secondary_attempts == 1
    print(
        "deadline-fallback",
        "status=",
        response.status_code,
        "primary_attempts=",
        deadline_primary_attempts,
        "secondary_attempts=",
        deadline_secondary_attempts,
    )

    failed_primary = MockProvider("primary", ProviderResult(429, "hidden-primary-body"))
    failed_secondary = MockProvider(
        "secondary", ProviderResult(500, "hidden-secondary-body")
    )
    failed_app = create_app(
        make_config(root / "both-failed.sqlite"),
        primary=failed_primary,
        secondary=failed_secondary,
    )
    with TestClient(failed_app) as client:
        response = call(client, "both-failed")
    failed_primary_attempts = [
        event.event.value for event in failed_primary.calls
    ].count("start")
    failed_secondary_attempts = [
        event.event.value for event in failed_secondary.calls
    ].count("start")
    assert response.status_code == 502
    assert failed_primary_attempts == 1
    assert failed_secondary_attempts == 1
    assert "hidden-primary-body" not in response.text
    assert "hidden-secondary-body" not in response.text
    print(
        "both-provider-failure",
        "status=",
        response.status_code,
        "primary_attempts=",
        failed_primary_attempts,
        "secondary_attempts=",
        failed_secondary_attempts,
        "sanitized=",
        "hidden-primary-body" not in response.text
        and "hidden-secondary-body" not in response.text,
    )

    database = root / "race.sqlite"
    evidence = database.read_bytes()
    for sidecar in (
        Path(f"{database}-wal"),
        Path(f"{database}-shm"),
        Path(f"{database}-journal"),
    ):
        if sidecar.exists():
            evidence += sidecar.read_bytes()
    assert b"demo-opaque-key" not in evidence
    with sqlite3.connect(database) as connection:
        print(
            "persisted HMAC fields:",
            list(
                connection.execute(
                    "SELECT length(tenant_fingerprint), charged_tokens FROM reservations LIMIT 1"
                )
            ),
        )
