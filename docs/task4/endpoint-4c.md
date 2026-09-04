# Task 4 completion endpoint

`POST /v1/completions` requires `X-Tenant-API-Key` and `X-Request-ID`. The tenant
header is opaque quota identity only; it is never persisted or echoed. Request IDs
are untrimmed ASCII values of 1–128 characters matching `[A-Za-z0-9._-]`. The JSON
body contains exactly a nonblank `prompt` and positive integer `max_output_tokens`.

Success is HTTP 200 with `request_id`, logical `provider`, `completion`, and validated
usage when available. All errors use `{ "error": { "code", "message", "request_id",
"retryable" } }`. Invalid input is 400, duplicate is 409, quota is 429, unavailable
SQLite state is 503, and provider failure is 502. Messages are stable and do not
contain upstream bodies, URLs, credentials, prompts, completions, paths, or exceptions.

The lifespan initializes SQLite/schema/WAL before serving and owns one HTTPX client,
closing it once. Provider destinations and credentials are fixed configuration; callers
cannot select them. The router commits a reservation before provider START, falls back
only for primary 429 or primary deadline loss, cleans the primary before secondary
START, permits exactly one secondary attempt, and never retries or uses a tertiary.
Inbound cancellation cleans only the active attempt and propagates without fallback.

Complete trustworthy prompt-plus-completion usage may lower a reservation; missing,
malformed, incomplete, untrusted, equal, greater, or invalid usage retains it. The
reservation ledger is file-backed and tenant fingerprints are HMAC-SHA256 only.
`scripts/inspect_rate_limit_db.py` prints only IDs, fingerprint length, timestamps, and
charges. Run the deterministic demo with `uv run python scripts/demo_task4.py`.

WAL improves reader/writer coexistence but does not make concurrent SQLite writers
possible; write transactions still serialize. This implementation uses deterministic
mock-first tests and does not claim live provider/network behavior.
