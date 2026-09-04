from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from fde_assessment.task4_model_router import (
    AdmissionStatus,
    AttemptEvent,
    CompletionRequest,
    DeterministicTokenCounter,
    MockProvider,
    ProviderRequest,
    ProviderResult,
    ProviderRouter,
    ProviderUsage,
    RateLimiter,
    ReservationService,
    RouterOutcome,
)


class StubbornProvider:
    role = "primary"

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def complete(self, request: ProviderRequest) -> ProviderResult:
        self.calls.append("start")
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.calls.append("late-finish")
            return ProviderResult(200, "late-primary-sentinel")
        raise AssertionError("unreachable")

    async def cancel(self, request_id: str) -> None:
        self.calls.append("cleanup")


def service(path: Path, limit: int = 100) -> ReservationService:
    limiter = RateLimiter(path, quota_limit=limit, fingerprint_secret=b"x" * 32)
    limiter.initialize()
    return ReservationService(limiter, DeterministicTokenCounter())


def request(request_id: str = "req-1") -> CompletionRequest:
    return CompletionRequest(request_id, "one two three", 7)


def test_completion_contract_is_strict() -> None:
    assert (
        CompletionRequest.from_mapping(
            {"request_id": "r", "prompt": "hello", "max_output_tokens": 4}
        ).max_output_tokens
        == 4
    )
    for value in (
        {"request_id": "r", "prompt": "hello"},
        {"request_id": "r", "prompt": "hello", "max_output_tokens": 4, "extra": 1},
        {"request_id": "r", "prompt": "hello", "max_output_tokens": True},
    ):
        with pytest.raises(ValueError):
            CompletionRequest.from_mapping(value)


def test_token_count_and_reservation_formula(tmp_path: Path) -> None:
    reservations = service(tmp_path / "quota.sqlite")
    assert reservations.token_counter.count("one two three") == 3
    result = reservations.reserve("tenant", request())
    assert result.status is AdmissionStatus.ADMITTED
    assert result.charged_tokens == 10
    assert reservations.limiter.active_charge("tenant") == 10


def test_reconciliation_only_lowers_complete_trustworthy_usage(tmp_path: Path) -> None:
    reservations = service(tmp_path / "quota.sqlite")
    admitted = reservations.reserve("tenant", request())
    assert admitted.reservation_id is not None
    reservation_id = admitted.reservation_id
    for usage in (
        None,
        ProviderUsage(1, 1, complete=False),
        ProviderUsage(1, 1, trustworthy=False),
        ProviderUsage(10, 10),
        ProviderUsage(5, 5),
    ):
        assert reservations.reconcile(reservation_id, usage) is False
        assert reservations.limiter.reservation_charge(reservation_id) == 10
    assert reservations.reconcile(reservation_id, ProviderUsage(0, 2)) is True
    assert reservations.limiter.reservation_charge(reservation_id) == 2
    with pytest.raises(ValueError):
        ProviderUsage(-1, 1)


def test_usage_contract_rejects_wrong_token_and_flag_types(tmp_path: Path) -> None:
    for values in ((True, 1), (1.0, 1), ("1", 1), (1, True)):
        with pytest.raises(ValueError):
            ProviderUsage(*values)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        ProviderRouter(
            service(tmp_path / "timeout.sqlite"),
            MockProvider("primary"),
            MockProvider("secondary"),
            primary_timeout_ms=True,
        )


def test_provider_roles_are_logical_only() -> None:
    with pytest.raises(ValueError):
        MockProvider("https://credential@example.invalid")


@pytest.mark.asyncio
async def test_primary_success_has_one_primary_call_and_no_fallback(
    tmp_path: Path,
) -> None:
    reservations = service(tmp_path / "quota.sqlite")
    primary = MockProvider(
        "primary", ProviderResult(200, {"ok": True}, ProviderUsage(1, 1))
    )
    secondary = MockProvider("secondary", ProviderResult(200))
    result = await ProviderRouter(reservations, primary, secondary).complete(
        "tenant", request()
    )
    assert result.outcome is RouterOutcome.PRIMARY_SUCCESS
    assert [event.event for event in primary.calls] == [
        AttemptEvent.START,
        AttemptEvent.FINISH,
    ]
    assert secondary.calls == []


@pytest.mark.asyncio
async def test_reservation_is_visible_at_provider_start_and_admission_gates_calls(
    tmp_path: Path,
) -> None:
    reservations = service(tmp_path / "quota.sqlite", limit=10)
    observed: list[int] = []
    primary = MockProvider(
        "primary",
        ProviderResult(200),
        on_start=lambda _: observed.append(
            reservations.limiter.active_charge("tenant")
        ),
    )
    secondary = MockProvider("secondary", ProviderResult(200))
    await ProviderRouter(reservations, primary, secondary).complete(
        "tenant", request("first")
    )
    assert observed == [10]
    duplicate = await ProviderRouter(reservations, primary, secondary).complete(
        "tenant", request("first")
    )
    assert duplicate.outcome is RouterOutcome.DUPLICATE_REQUEST
    assert len(primary.calls) == 2
    quota = await ProviderRouter(reservations, primary, secondary).complete(
        "tenant", request("second")
    )
    assert quota.outcome is RouterOutcome.QUOTA_EXCEEDED
    assert len(primary.calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "elapsed_ms, wins", [(2_999, True), (3_000, False), (3_001, False)]
)
async def test_deadline_ownership_is_strict(
    tmp_path: Path, elapsed_ms: int, wins: bool
) -> None:
    reservations = service(tmp_path / f"quota-{elapsed_ms}.sqlite")
    primary = MockProvider("primary", ProviderResult(200, {}, ProviderUsage(0, 1)))
    secondary = MockProvider("secondary", ProviderResult(200))
    clock_values = iter((0, elapsed_ms))
    router = ProviderRouter(
        reservations,
        primary,
        secondary,
        monotonic_ms=lambda: next(clock_values),
    )
    result = await router.complete("tenant", request())
    assert (result.outcome is RouterOutcome.PRIMARY_SUCCESS) is wins
    assert len(secondary.calls) == (0 if wins else 2)


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [{"error": "rate"}, None, "malformed"])
async def test_every_429_body_falls_back_once(tmp_path: Path, body: object) -> None:
    reservations = service(tmp_path / f"quota-{body!s}.sqlite")
    primary = MockProvider("primary", ProviderResult(429, body))
    secondary = MockProvider("secondary", ProviderResult(200, {}, ProviderUsage(0, 1)))
    result = await ProviderRouter(reservations, primary, secondary).complete(
        "tenant", request()
    )
    assert result.outcome is RouterOutcome.SECONDARY_SUCCESS
    assert len(primary.calls) == 2
    assert len(secondary.calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 401, 403, 500, None])
async def test_non_trigger_failures_do_not_fallback(
    tmp_path: Path, status: int | None
) -> None:
    reservations = service(tmp_path / f"quota-{status}.sqlite")
    primary = MockProvider("primary", ProviderResult(status, "ignored"))
    secondary = MockProvider("secondary", ProviderResult(200))
    result = await ProviderRouter(reservations, primary, secondary).complete(
        "tenant", request()
    )
    assert result.outcome is RouterOutcome.PRIMARY_FAILURE
    assert "ignored" not in repr(result)
    assert len(primary.calls) == 2
    assert secondary.calls == []


@pytest.mark.asyncio
async def test_transport_exception_is_sanitized_without_fallback(
    tmp_path: Path,
) -> None:
    reservations = service(tmp_path / "quota.sqlite")
    primary = MockProvider("primary")
    secondary = MockProvider("secondary", ProviderResult(200, "transport-secret"))
    result = await ProviderRouter(reservations, primary, secondary).complete(
        "tenant", request()
    )
    assert result.outcome is RouterOutcome.PRIMARY_FAILURE
    assert result.result is None
    assert "provider failed" not in repr(result)
    assert secondary.calls == []


@pytest.mark.asyncio
async def test_primary_timeout_cleans_up_before_secondary(tmp_path: Path) -> None:
    reservations = service(tmp_path / "quota.sqlite")
    primary = MockProvider("primary", wait_for=asyncio.Event())
    secondary = MockProvider("secondary", ProviderResult(200))
    result = await ProviderRouter(
        reservations, primary, secondary, primary_timeout_ms=1
    ).complete("tenant", request())
    assert result.outcome is RouterOutcome.SECONDARY_SUCCESS
    assert primary.calls[0].event is AttemptEvent.START
    assert primary.calls[1].event is AttemptEvent.CANCEL
    assert secondary.calls[0].event is AttemptEvent.START


@pytest.mark.asyncio
async def test_secondary_timeout_is_one_attempt_with_no_tertiary(
    tmp_path: Path,
) -> None:
    reservations = service(tmp_path / "quota.sqlite")
    primary = MockProvider("primary", ProviderResult(429, None))
    started = asyncio.Event()
    secondary = MockProvider(
        "secondary", wait_for=asyncio.Event(), on_start=lambda _: started.set()
    )
    result = await ProviderRouter(
        reservations,
        primary,
        secondary,
        secondary_timeout_ms=1,
    ).complete("tenant", request())
    assert result.outcome is RouterOutcome.SECONDARY_FAILURE
    assert [event.event for event in secondary.calls] == [
        AttemptEvent.START,
        AttemptEvent.CANCEL,
    ]


@pytest.mark.asyncio
async def test_late_primary_cannot_win_or_emit_second_result(tmp_path: Path) -> None:
    reservations = service(tmp_path / "quota.sqlite")
    primary = StubbornProvider()
    secondary = MockProvider("secondary", ProviderResult(200))
    clock_values = iter((0, 3_000))
    result = await ProviderRouter(
        reservations,
        primary,
        secondary,
        primary_timeout_ms=1,
        monotonic_ms=lambda: next(clock_values),
    ).complete("tenant", request())
    assert result.outcome is RouterOutcome.SECONDARY_SUCCESS
    assert primary.calls == ["start", "late-finish", "cleanup"]
    assert secondary.calls[0].event is AttemptEvent.START


@pytest.mark.asyncio
async def test_caller_cancellation_during_secondary_propagates_without_retry(
    tmp_path: Path,
) -> None:
    reservations = service(tmp_path / "quota.sqlite")
    primary = MockProvider("primary", ProviderResult(429, None))
    started = asyncio.Event()
    secondary = MockProvider(
        "secondary", wait_for=asyncio.Event(), on_start=lambda _: started.set()
    )
    task = asyncio.create_task(
        ProviderRouter(reservations, primary, secondary).complete("tenant", request())
    )
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert [event.event for event in secondary.calls] == [
        AttemptEvent.START,
        AttemptEvent.CANCEL,
    ]


@pytest.mark.asyncio
async def test_both_provider_failure_is_typed_and_stable(tmp_path: Path) -> None:
    reservations = service(tmp_path / "quota.sqlite")
    primary = MockProvider("primary", ProviderResult(429, "untrusted upstream body"))
    secondary = MockProvider(
        "secondary", ProviderResult(500, "untrusted upstream body")
    )
    result = await ProviderRouter(reservations, primary, secondary).complete(
        "tenant", request()
    )
    assert result.outcome is RouterOutcome.SECONDARY_FAILURE
    assert result.provider == "secondary"
    assert "untrusted upstream body" not in repr(result)
    assert len(primary.calls) == 2
    assert len(secondary.calls) == 2


@pytest.mark.asyncio
async def test_caller_cancellation_does_not_fallback(tmp_path: Path) -> None:
    reservations = service(tmp_path / "quota.sqlite")
    primary = MockProvider("primary", wait_for=asyncio.Event())
    secondary = MockProvider("secondary", ProviderResult(200))
    task = asyncio.create_task(
        ProviderRouter(reservations, primary, secondary).complete("tenant", request())
    )
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert secondary.calls == []
    assert primary.calls[-1].event is AttemptEvent.CANCEL
