"""Pure completion, reservation, provider, and fallback contracts for Task 4."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol, cast

from .limiter import AdmissionResult, AdmissionStatus, RateLimiter


def _strict_text(value: object, name: str) -> str:
    if type(value) is not str or not value:
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _strict_int(value: object, name: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


@dataclass(frozen=True)
class CompletionRequest:
    request_id: str
    prompt: str
    max_output_tokens: int

    def __post_init__(self) -> None:
        _strict_text(self.request_id, "request_id")
        _strict_text(self.prompt, "prompt")
        _strict_int(self.max_output_tokens, "max_output_tokens")

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> CompletionRequest:
        if type(value) is not dict or set(value) != {
            "request_id",
            "prompt",
            "max_output_tokens",
        }:
            raise ValueError("completion request has an invalid shape")
        return cls(
            cast(str, value["request_id"]),
            cast(str, value["prompt"]),
            cast(int, value["max_output_tokens"]),
        )


class TokenCounter(Protocol):
    def count(self, text: str) -> int: ...


class DeterministicTokenCounter:
    def count(self, text: str) -> int:
        _strict_text(text, "text")
        return len(text.split())


@dataclass(frozen=True)
class ProviderRequest:
    request_id: str
    prompt: str
    max_output_tokens: int


@dataclass(frozen=True)
class ProviderUsage:
    prompt_tokens: int
    completion_tokens: int
    complete: bool
    trustworthy: bool

    def __post_init__(self) -> None:
        _strict_int(self.prompt_tokens, "prompt_tokens")
        _strict_int(self.completion_tokens, "completion_tokens")
        if type(self.complete) is not bool or type(self.trustworthy) is not bool:
            raise ValueError("usage flags must be bools")

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass(frozen=True)
class ProviderResult:
    status_code: int | None
    body: object = None
    usage: ProviderUsage | None = None

    def __post_init__(self) -> None:
        if self.status_code is not None and type(self.status_code) is not int:
            raise ValueError("status_code must be an integer or None")


class AttemptEvent(StrEnum):
    START = "start"
    FINISH = "finish"
    CANCEL = "cancel"


@dataclass(frozen=True)
class ProviderCall:
    role: str
    event: AttemptEvent
    request_id: str
    payload: ProviderRequest


class Provider(Protocol):
    role: str

    async def complete(self, request: ProviderRequest) -> ProviderResult: ...

    async def cancel(self, request_id: str) -> None: ...


@dataclass
class MockProvider:
    role: str
    result: ProviderResult | None = None
    wait_for: asyncio.Event | None = None
    on_start: Callable[[ProviderRequest], None] | None = None
    calls: list[ProviderCall] = field(default_factory=list)
    _active: dict[str, ProviderRequest] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.role not in {"primary", "secondary"}:
            raise ValueError("provider role must be primary or secondary")

    async def complete(self, request: ProviderRequest) -> ProviderResult:
        self._active[request.request_id] = request
        self.calls.append(
            ProviderCall(self.role, AttemptEvent.START, request.request_id, request)
        )
        if self.on_start is not None:
            self.on_start(request)
        try:
            if self.wait_for is not None:
                await self.wait_for.wait()
            if self.result is None:
                raise RuntimeError("provider failed")
            self.calls.append(
                ProviderCall(
                    self.role, AttemptEvent.FINISH, request.request_id, request
                )
            )
            return self.result
        except asyncio.CancelledError:
            self.calls.append(
                ProviderCall(
                    self.role, AttemptEvent.CANCEL, request.request_id, request
                )
            )
            raise
        finally:
            self._active.pop(request.request_id, None)

    async def cancel(self, request_id: str) -> None:
        request = self._active.pop(request_id, None)
        if request is not None:
            self.calls.append(
                ProviderCall(self.role, AttemptEvent.CANCEL, request_id, request)
            )


class RouterOutcome(StrEnum):
    PRIMARY_SUCCESS = "PRIMARY_SUCCESS"
    SECONDARY_SUCCESS = "SECONDARY_SUCCESS"
    PRIMARY_FAILURE = "PRIMARY_FAILURE"
    SECONDARY_FAILURE = "SECONDARY_FAILURE"
    DUPLICATE_REQUEST = "DUPLICATE_REQUEST"
    QUOTA_EXCEEDED = "QUOTA_EXCEEDED"


@dataclass(frozen=True)
class RouterResult:
    outcome: RouterOutcome
    provider: str | None
    result: ProviderResult | None = None


class ReservationService:
    def __init__(self, limiter: RateLimiter, token_counter: TokenCounter) -> None:
        self.limiter = limiter
        self.token_counter = token_counter

    def reserve(self, tenant_key: str, request: CompletionRequest) -> AdmissionResult:
        return self.limiter.admit(
            tenant_key,
            request.request_id,
            estimated_input_tokens=self.token_counter.count(request.prompt),
            max_output_tokens=request.max_output_tokens,
        )

    def reconcile(self, reservation_id: int, usage: ProviderUsage | None) -> bool:
        if usage is None or not usage.complete or not usage.trustworthy:
            return False
        return self.limiter.reconcile(reservation_id, usage.total_tokens)


class ProviderRouter:
    def __init__(
        self,
        reservations: ReservationService,
        primary: Provider,
        secondary: Provider,
        *,
        monotonic_ms: Callable[[], int] | None = None,
        primary_timeout_ms: int = 3_000,
        secondary_timeout_ms: int = 3_000,
    ) -> None:
        self.reservations = reservations
        self.primary = primary
        self.secondary = secondary
        self.monotonic_ms = monotonic_ms or (lambda: time.monotonic_ns() // 1_000_000)
        self.primary_timeout_ms = primary_timeout_ms
        self.secondary_timeout_ms = secondary_timeout_ms
        for timeout in (primary_timeout_ms, secondary_timeout_ms):
            if type(timeout) is not int or timeout <= 0:
                raise ValueError("timeouts must be positive integers")
        if self.primary.role != "primary" or self.secondary.role != "secondary":
            raise ValueError("provider roles must be primary and secondary")

    async def complete(
        self, tenant_key: str, request: CompletionRequest
    ) -> RouterResult:
        admission = self.reservations.reserve(tenant_key, request)
        if admission.status is AdmissionStatus.DUPLICATE_REQUEST:
            return RouterResult(RouterOutcome.DUPLICATE_REQUEST, None)
        if admission.status is AdmissionStatus.QUOTA_EXCEEDED:
            return RouterResult(RouterOutcome.QUOTA_EXCEEDED, None)
        provider_request = ProviderRequest(
            request.request_id, request.prompt, request.max_output_tokens
        )
        primary_deadline_ms = self.monotonic_ms() + self.primary_timeout_ms
        primary_task = asyncio.create_task(self.primary.complete(provider_request))
        try:
            primary_result = await asyncio.wait_for(
                primary_task, self.primary_timeout_ms / 1000
            )
        except asyncio.CancelledError:
            await self.primary.cancel(request.request_id)
            raise
        except TimeoutError:
            await self.primary.cancel(request.request_id)
            return await self._secondary(provider_request, admission.reservation_id)
        except Exception:  # noqa: BLE001 - normalize provider failures
            return RouterResult(RouterOutcome.PRIMARY_FAILURE, self.primary.role)
        if not isinstance(primary_result, ProviderResult):
            return RouterResult(RouterOutcome.PRIMARY_FAILURE, self.primary.role)
        if self.monotonic_ms() >= primary_deadline_ms:
            await self.primary.cancel(request.request_id)
            return await self._secondary(provider_request, admission.reservation_id)
        if primary_result.status_code == 429:
            return await self._secondary(provider_request, admission.reservation_id)
        if primary_result.status_code == 200:
            self.reservations.reconcile(
                admission.reservation_id or 0, primary_result.usage
            )
            return RouterResult(
                RouterOutcome.PRIMARY_SUCCESS, self.primary.role, primary_result
            )
        return RouterResult(RouterOutcome.PRIMARY_FAILURE, self.primary.role)

    async def _secondary(
        self, request: ProviderRequest, reservation_id: int | None
    ) -> RouterResult:
        try:
            result = await asyncio.wait_for(
                self.secondary.complete(request), self.secondary_timeout_ms / 1000
            )
        except TimeoutError:
            await self.secondary.cancel(request.request_id)
            return RouterResult(RouterOutcome.SECONDARY_FAILURE, self.secondary.role)
        except asyncio.CancelledError:
            await self.secondary.cancel(request.request_id)
            raise
        except Exception:  # noqa: BLE001 - normalize provider failures
            return RouterResult(RouterOutcome.SECONDARY_FAILURE, self.secondary.role)
        if not isinstance(result, ProviderResult):
            return RouterResult(RouterOutcome.SECONDARY_FAILURE, self.secondary.role)
        if result.status_code == 200:
            self.reservations.reconcile(reservation_id or 0, result.usage)
            return RouterResult(
                RouterOutcome.SECONDARY_SUCCESS, self.secondary.role, result
            )
        return RouterResult(RouterOutcome.SECONDARY_FAILURE, self.secondary.role)
