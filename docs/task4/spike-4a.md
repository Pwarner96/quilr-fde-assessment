# Spike 4A: file-backed atomic quota admission

This spike contains only the SQLite reservation ledger. `RateLimiter.initialize()` must
complete schema, index, and WAL setup before admission workers start. Each admission
uses a short `BEGIN IMMEDIATE` transaction, stores only an HMAC-SHA256 tenant
fingerprint, and reserves `estimated_input_tokens + max_output_tokens`.

Rows are active exactly when `created_at_ms > now_ms - 60_000`; stale rows are removed
inside the admission transaction. Duplicate request IDs are tenant-scoped. Provider
execution, reconciliation, HTTPX, fallback, and FastAPI behavior are deliberately
outside this spike and remain gated for a later authorization.

WAL improves reader/writer coexistence; it does not make concurrent SQLite writers
possible. Writers still serialize through `BEGIN IMMEDIATE`. A transient writer
conflict may wait within the configured busy policy, while busy exhaustion is distinct
from quota rejection and fails closed without creating a reservation.

The same request ID can be admitted independently for two tenants. Repeating it while
active for the same tenant returns `DUPLICATE_REQUEST`, creates no second reservation,
and has no provider behavior in this spike. Raw tenant keys and configured fingerprint
secrets are absent from the database, applicable `-wal`/`-shm`/`-journal` sidecars,
captured logs, and limiter error strings. The secret is configuration-supplied and is
never persisted.

Verification commands:

```text
uv run pytest tests/task4 -q
uv run pytest tests/task4/test_limiter.py -q -k killer_race_exactly_admits_14_and_never_oversubscribes
uv run ruff check src/fde_assessment/task4_model_router tests/task4 scripts/demo_task4.py scripts/inspect_rate_limit_db.py
uv run mypy src/fde_assessment/task4_model_router
uv run python scripts/demo_task4.py
make verify
git diff --check
```

The demo is limiter-only. Provider adapters, routing/fallback, reconciliation, HTTP,
and the public FastAPI completion endpoint remain explicitly gated.
