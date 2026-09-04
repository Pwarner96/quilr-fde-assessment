from __future__ import annotations

import re
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from fde_assessment.task4_model_router import (
    AdmissionStatus,
    FakeClock,
    LimiterBusyError,
    RateLimiter,
    new_fingerprint_secret,
)


def make_limiter(
    path: Path, clock: FakeClock | None = None, limit: int = 100, timeout: int = 5_000
) -> RateLimiter:
    limiter = RateLimiter(
        path,
        quota_limit=limit,
        fingerprint_secret=b"s" * 32,
        clock=clock,
        busy_timeout_ms=timeout,
    )
    limiter.initialize()
    return limiter


def test_killer_race_exactly_admits_14_and_never_oversubscribes(tmp_path: Path) -> None:
    limiter = make_limiter(tmp_path / "quota.sqlite")
    gate = threading.Barrier(64)

    def attempt(index: int):
        gate.wait()
        try:
            return limiter.admit(
                "tenant-a",
                f"req-{index}",
                estimated_input_tokens=3,
                max_output_tokens=4,
            )
        except BaseException as error:  # noqa: BLE001 - classify every race failure
            return error

    with ThreadPoolExecutor(max_workers=64) as pool:
        results = list(pool.map(attempt, range(64)))
    failures = [result for result in results if isinstance(result, Exception)]
    assert len(failures) == 0
    statuses = [
        result.status for result in results if not isinstance(result, Exception)
    ]
    assert statuses.count(AdmissionStatus.ADMITTED) == 14
    assert statuses.count(AdmissionStatus.QUOTA_EXCEEDED) == 50
    assert statuses.count(AdmissionStatus.DUPLICATE_REQUEST) == 0
    assert sum(isinstance(result, LimiterBusyError) for result in results) == 0
    assert limiter.active_charge("tenant-a") == 98
    assert limiter.active_charge("tenant-a") <= limiter.quota_limit


def test_exact_focused_killer_race_is_named_for_evidence(tmp_path: Path) -> None:
    test_killer_race_exactly_admits_14_and_never_oversubscribes(tmp_path)


def test_persistence_and_tenant_isolation(tmp_path: Path) -> None:
    path = tmp_path / "quota.sqlite"
    first = make_limiter(path, limit=10)
    assert (
        first.admit("A", "a", estimated_input_tokens=2, max_output_tokens=3).status
        == AdmissionStatus.ADMITTED
    )
    reopened = make_limiter(path, limit=10)
    assert reopened.active_charge("A") == 5
    assert reopened.active_charge("B") == 0


def test_window_boundary_is_strictly_expired_at_60000(tmp_path: Path) -> None:
    clock = FakeClock(0)
    limiter = make_limiter(tmp_path / "quota.sqlite", clock, limit=5)
    assert (
        limiter.admit(
            "A", "at-zero", estimated_input_tokens=2, max_output_tokens=3
        ).status
        == AdmissionStatus.ADMITTED
    )
    clock.set(59_999)
    assert limiter.active_charge("A") == 5
    clock.set(60_000)
    assert limiter.active_charge("A") == 0


def test_active_duplicate_has_no_second_reservation(tmp_path: Path) -> None:
    limiter = make_limiter(tmp_path / "quota.sqlite", limit=10)
    assert (
        limiter.admit("A", "same", estimated_input_tokens=2, max_output_tokens=3).status
        == AdmissionStatus.ADMITTED
    )
    duplicate = limiter.admit(
        "A", "same", estimated_input_tokens=2, max_output_tokens=3
    )
    assert duplicate.status == AdmissionStatus.DUPLICATE_REQUEST
    assert limiter.active_charge("A") == 5


def test_request_id_is_tenant_scoped(tmp_path: Path) -> None:
    limiter = make_limiter(tmp_path / "quota.sqlite", limit=10)
    assert (
        limiter.admit(
            "A", "shared", estimated_input_tokens=2, max_output_tokens=3
        ).status
        == AdmissionStatus.ADMITTED
    )
    assert (
        limiter.admit(
            "B", "shared", estimated_input_tokens=2, max_output_tokens=3
        ).status
        == AdmissionStatus.ADMITTED
    )
    assert (
        limiter.admit(
            "A", "shared", estimated_input_tokens=2, max_output_tokens=3
        ).status
        == AdmissionStatus.DUPLICATE_REQUEST
    )
    assert limiter.active_charge("A") == 5
    assert limiter.active_charge("B") == 5


def test_injected_precommit_failure_rolls_back(tmp_path: Path) -> None:
    limiter = make_limiter(tmp_path / "quota.sqlite")
    with pytest.raises(RuntimeError, match="injected"):
        limiter.admit(
            "A",
            "will-rollback",
            estimated_input_tokens=2,
            max_output_tokens=3,
            before_commit=lambda _: (_ for _ in ()).throw(RuntimeError("injected")),
        )
    assert limiter.active_charge("A") == 0


def test_busy_timeout_fails_closed_without_partial_state(tmp_path: Path) -> None:
    path = tmp_path / "quota.sqlite"
    limiter = make_limiter(path, timeout=50)
    lock = sqlite3.connect(path)
    lock.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(LimiterBusyError) as caught:
            limiter.admit("A", "busy", estimated_input_tokens=1, max_output_tokens=1)
    finally:
        lock.rollback()
        lock.close()
    assert limiter.active_charge("A") == 0
    assert str(caught.value) == ""


def test_transient_writer_conflict_waits_and_then_admits(tmp_path: Path) -> None:
    path = tmp_path / "quota.sqlite"
    limiter = make_limiter(path, timeout=1_000)
    lock = sqlite3.connect(path, check_same_thread=False)
    lock.execute("BEGIN IMMEDIATE")
    release = threading.Timer(0.12, lock.rollback)
    release.start()
    try:
        result = limiter.admit(
            "A", "waited", estimated_input_tokens=1, max_output_tokens=1
        )
    finally:
        release.join()
        lock.close()
    assert result.status == AdmissionStatus.ADMITTED


def test_only_hmac_fingerprint_is_persisted(tmp_path: Path) -> None:
    path = tmp_path / "quota.sqlite"
    raw_key = "opaque-tenant-secret-never-persisted"
    limiter = make_limiter(path)
    limiter.admit(raw_key, "safe", estimated_input_tokens=1, max_output_tokens=1)
    evidence = path.read_bytes()
    for sidecar in (Path(f"{path}-wal"), Path(f"{path}-shm"), Path(f"{path}-journal")):
        if sidecar.exists():
            evidence += sidecar.read_bytes()
    assert raw_key.encode() not in evidence
    assert limiter.fingerprint(raw_key) in evidence
    assert not re.search(re.escape(raw_key), evidence.decode("latin1"))


def test_secret_safety_covers_storage_logs_and_error_strings(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "quota.sqlite"
    raw_key = "raw-tenant-key-for-safety-evidence"
    secret = b"configured-fingerprint-secret-32b!"
    limiter = RateLimiter(
        path, quota_limit=2, fingerprint_secret=secret, busy_timeout_ms=20
    )
    limiter.initialize()
    limiter.admit(raw_key, "safe", estimated_input_tokens=1, max_output_tokens=1)
    lock = sqlite3.connect(path)
    lock.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(LimiterBusyError) as busy_error:
            limiter.admit(
                raw_key, "busy", estimated_input_tokens=1, max_output_tokens=1
            )
    finally:
        lock.rollback()
        lock.close()
    rollback_limiter = RateLimiter(
        path, quota_limit=4, fingerprint_secret=secret, busy_timeout_ms=20
    )
    rollback_limiter.initialize()
    with pytest.raises(RuntimeError) as rollback_error:
        rollback_limiter.admit(
            raw_key,
            "rollback",
            estimated_input_tokens=1,
            max_output_tokens=1,
            before_commit=lambda _: (_ for _ in ()).throw(RuntimeError("injected")),
        )
    evidence = path.read_bytes()
    for sidecar in (Path(f"{path}-wal"), Path(f"{path}-shm"), Path(f"{path}-journal")):
        if sidecar.exists():
            evidence += sidecar.read_bytes()
    strings = [str(busy_error.value), str(rollback_error.value), caplog.text]
    assert raw_key.encode() not in evidence
    assert secret not in evidence
    assert all(
        raw_key not in value and secret.decode() not in value for value in strings
    )


def test_fingerprint_secret_configuration_contract() -> None:
    with pytest.raises(ValueError):
        RateLimiter(":memory:", quota_limit=1, fingerprint_secret=b"too-short")
    assert len(new_fingerprint_secret()) == 32
