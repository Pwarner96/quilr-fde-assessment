# Five-minute interview walkthrough

Run the full suite before the interview; use focused demos live and point to the recorded evidence.

## 0:00–0:45 — framing

Say: “This is four independent Python 3.12 components packaged together for review. `uv.lock` and the Makefile define the environment. Everything is mock-first and synthetic, so no provider account or customer setup is required.” Point to the root table and `docs/ARCHITECTURE.md`.

## 0:45–1:35 — Task 1

```bash
uv run --frozen python scripts/demo_task1.py
```

Show initialize, the synthetic customer result, and `-32602` invalid arguments. Explain the low-level MCP SDK boundary, exactly two tools, stdout protocol-only, and stderr diagnostics. A valid business decline is distinct from invalid input.

## 1:35–2:20 — Task 2

```bash
uv run --frozen python scripts/demo_task2.py
```

Show the permitted viewer call and `admin_reset_key` denial with `-32001`. Explain pre-forward policy, bearer stripping, service credentials, and QA-001 duplicate-key handling (`400/-32600`, zero calls).

## 2:20–3:15 — Task 3

```bash
uv run --frozen python scripts/demo_task3.py
```

Show safe prefix release followed by `Contact [REDACTED] now`. Explain the narrow Chat-Completions-style SSE subset, bounds, conservative overflow, and no synthesized `[DONE]` after abnormal termination.

## 3:15–4:10 — Task 4

```bash
uv run --frozen python scripts/demo_task4.py
```

Call out the deterministic race (14 admitted, 50 quota rejected, active charge 98), 429/deadline fallback, non-trigger 500, sanitized failure, and HMAC persistence evidence. Explain `BEGIN IMMEDIATE` and the project-choice secondary cap.

## 4:10–5:00 — evidence and tradeoffs

```bash
make verify
make build
```

The accepted result was 176 passed with one nonblocking Starlette deprecation warning. Thread 05's exact verdict is `ACCEPT`. Production work would add managed identity/secrets, rotation/replay controls, network policy, observability, migrations, provider contracts, and load testing. CI is configured but has not run because there is no remote.

Likely follow-ups: the lanes are independent by design; HS256 is local assessment identity, not production identity; bounded redaction favors fail-closed behavior; SQLite WAL still serializes writers; mocks do not prove live-provider behavior.
