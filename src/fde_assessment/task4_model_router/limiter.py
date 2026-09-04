"""File-backed, tenant-scoped sliding-window quota admission for Task 4."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final

WINDOW_MS: Final = 60_000


class AdmissionStatus(StrEnum):
    ADMITTED = "ADMITTED"
    QUOTA_EXCEEDED = "QUOTA_EXCEEDED"
    DUPLICATE_REQUEST = "DUPLICATE_REQUEST"


class LimiterBusyError(RuntimeError):
    """SQLite remained locked beyond the configured fail-closed wait policy."""


@dataclass(frozen=True)
class AdmissionResult:
    status: AdmissionStatus
    request_id: str
    charged_tokens: int
    reservation_id: int | None = None


class FakeClock:
    """A deterministic millisecond clock suitable for boundary tests."""

    def __init__(self, now_ms: int = 0) -> None:
        self._now_ms = now_ms

    def __call__(self) -> int:
        return self._now_ms

    def set(self, now_ms: int) -> None:
        self._now_ms = now_ms

    def advance(self, milliseconds: int) -> None:
        self._now_ms += milliseconds


class RateLimiter:
    """SQLite reservation ledger whose admission decision is one short transaction."""

    def __init__(
        self,
        database_path: str | Path,
        *,
        quota_limit: int,
        fingerprint_secret: bytes,
        clock: Callable[[], int] | None = None,
        busy_timeout_ms: int = 5_000,
    ) -> None:
        if quota_limit < 0:
            raise ValueError("quota_limit must be non-negative")
        if not fingerprint_secret or len(fingerprint_secret) < 32:
            raise ValueError("fingerprint_secret must be at least 32 bytes")
        if busy_timeout_ms < 0:
            raise ValueError("busy_timeout_ms must be non-negative")
        self.database_path = Path(database_path)
        self.quota_limit = quota_limit
        self._fingerprint_secret = bytes(fingerprint_secret)
        self._clock = clock or (lambda: time.time_ns() // 1_000_000)
        self.busy_timeout_ms = busy_timeout_ms

    def initialize(self) -> None:
        """Create schema/WAL before callers release any admission workers."""
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS reservations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_fingerprint BLOB NOT NULL,
                    request_id TEXT NOT NULL,
                    created_at_ms INTEGER NOT NULL,
                    charged_tokens INTEGER NOT NULL CHECK (charged_tokens >= 0),
                    UNIQUE (tenant_fingerprint, request_id)
                );
                CREATE INDEX IF NOT EXISTS idx_reservations_tenant_created
                    ON reservations (tenant_fingerprint, created_at_ms);
                """
            )

    def admit(
        self,
        tenant_key: str,
        request_id: str,
        *,
        estimated_input_tokens: int,
        max_output_tokens: int,
        before_commit: Callable[[sqlite3.Connection], None] | None = None,
    ) -> AdmissionResult:
        """Reserve estimated input plus maximum output before provider execution."""
        if estimated_input_tokens < 0 or max_output_tokens < 0:
            raise ValueError("token estimates must be non-negative")
        requested = estimated_input_tokens + max_output_tokens
        fingerprint = self.fingerprint(tenant_key)
        now_ms = self._clock()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "DELETE FROM reservations WHERE created_at_ms <= ?",
                (now_ms - WINDOW_MS,),
            )
            duplicate = connection.execute(
                """
                SELECT id FROM reservations
                WHERE tenant_fingerprint = ? AND request_id = ?
                """,
                (fingerprint, request_id),
            ).fetchone()
            if duplicate is not None:
                connection.commit()
                return AdmissionResult(
                    AdmissionStatus.DUPLICATE_REQUEST, request_id, 0, duplicate[0]
                )
            active_charge = connection.execute(
                """
                SELECT COALESCE(SUM(charged_tokens), 0) FROM reservations
                WHERE tenant_fingerprint = ? AND created_at_ms > ?
                """,
                (fingerprint, now_ms - WINDOW_MS),
            ).fetchone()[0]
            if active_charge + requested > self.quota_limit:
                connection.commit()
                return AdmissionResult(AdmissionStatus.QUOTA_EXCEEDED, request_id, 0)
            cursor = connection.execute(
                """
                INSERT INTO reservations
                    (tenant_fingerprint, request_id, created_at_ms, charged_tokens)
                VALUES (?, ?, ?, ?)
                """,
                (fingerprint, request_id, now_ms, requested),
            )
            if before_commit is not None:
                before_commit(connection)
            connection.commit()
            return AdmissionResult(
                AdmissionStatus.ADMITTED, request_id, requested, cursor.lastrowid
            )
        except sqlite3.OperationalError as error:
            connection.rollback()
            if "locked" in str(error).lower() or "busy" in str(error).lower():
                raise LimiterBusyError from None
            raise
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def fingerprint(self, tenant_key: str) -> bytes:
        return hmac.new(
            self._fingerprint_secret, tenant_key.encode(), hashlib.sha256
        ).digest()

    def active_charge(self, tenant_key: str) -> int:
        fingerprint = self.fingerprint(tenant_key)
        now_ms = self._clock()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COALESCE(SUM(charged_tokens), 0) FROM reservations "
                "WHERE tenant_fingerprint = ? AND created_at_ms > ?",
                (fingerprint, now_ms - WINDOW_MS),
            ).fetchone()
        return int(row[0])

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.database_path, timeout=self.busy_timeout_ms / 1000
        )
        connection.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection


def new_fingerprint_secret() -> bytes:
    """Generate configuration material; callers must keep it outside the database."""
    return secrets.token_bytes(32)
