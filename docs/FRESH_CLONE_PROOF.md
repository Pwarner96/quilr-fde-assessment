# SHA A Fresh-Clone Proof

This proof records a fresh-clone verification of SHA A. It is intentionally
written before the proof commit exists, so it does not contain the proof
commit's own SHA.

## Recorded environment

- Verification time: 2026-09-04 18:50 EDT (-0400)
- Host OS: macOS 15.7.7, x86_64
- Python: 3.12.14 (approved bundled CPython runtime)
- uv: 0.12.9 (approved exact executable)
- SHA A: `d2cc0426f8b1ab7e837dacea5da76e938cb8f5c0`
- Clone: a new `git clone --no-local --branch integration/assessment` checkout
- Environment: external to the clone, under `/tmp/quilr-release-v0.qMqZdt/environment/.venv`
- Dependency mode: offline, no Python downloads, pre-populated shared uv cache, frozen `uv.lock`

The clone started at SHA A with an empty Git status and no copied `.venv`,
`.env`, SQLite database, SQLite sidecar, cache, build output, or credential
material. The clone-local origin was removed after cloning; the project
repository has no remotes.

## Commands and results

All commands ran from the fresh clone with the exact Python and uv paths above.

| Command | Result |
| --- | --- |
| `make UV="$UV" PYTHON="$PYTHON" install` | exit 0; 52 locked packages installed |
| `make UV="$UV" PYTHON="$PYTHON" format-check` | exit 0; 52 files already formatted |
| `make UV="$UV" PYTHON="$PYTHON" lint` | exit 0; all checks passed |
| `make UV="$UV" PYTHON="$PYTHON" typecheck` | exit 0; no issues in 18 source files |
| `make UV="$UV" PYTHON="$PYTHON" test` | exit 0; **176 passed** |
| `make UV="$UV" PYTHON="$PYTHON" verify` | exit 0; **176 passed** |
| `make UV="$UV" PYTHON="$PYTHON" build` | exit 0; source distribution and wheel built |

The test and verify runs emitted one nonblocking Starlette/AnyIO deprecation
warning for `anyio.abc.BlockingPortal`.

## Demo evidence

All four exact demo commands exited 0:

- `uv run --frozen python scripts/demo_task1.py`: initialized the MCP server,
  returned the synthetic customer, and returned top-level `-32602` for invalid
  arguments; diagnostics remained on stderr.
- `uv run --frozen python scripts/demo_task2.py`: allowed `echo`, denied the
  viewer admin call with `-32001`, and reported `downstream_calls=1`.
- `uv run --frozen python scripts/demo_task3.py`: produced final semantic text
  `Contact [REDACTED] now` across a split stream.
- `uv run --frozen python scripts/demo_task4.py`: reported the deterministic
  race result `admitted=14`, `quota_rejected=50`, `active_charge=98`; exercised
  success, 429 fallback, non-triggering 500, deadline fallback, and sanitized
  both-provider failure; persisted fields were `(32, 7)`.

## SQLite evidence

Using the public `RateLimiter` API, the proof created a separate synthetic
ledger at `/tmp/quilr-release-v0.qMqZdt/evidence/synthetic-ledger.sqlite`.
It used the opaque tenant key `v0-synthetic-tenant-canary`, request ID
`v0-request-001`, and a synthetic 32-byte HMAC secret. One deterministic
reservation was admitted:

- status: `ADMITTED`
- reservation ID: `1`
- charged tokens: `20`
- active charge: `20`
- inspected row facts: `(1, 32, 'v0-request-001', <created_at_ms>, 20)`

The database inspection script printed only those safe facts. Scans of the
database and any `-wal`, `-shm`, or journal sidecars found neither the raw
tenant canary nor the raw synthetic secret. The ledger was outside the clone;
no database or state file was written into the clone.

## Security, privacy, and artifact scans

- Tracked clone content was scanned for private keys, cloud/API token patterns,
  absolute local home paths, and provider credentials: no findings.
- The generated clone outputs were scanned for the same sensitive material and
  for runtime databases/logs/caches: no sensitive findings.
- Build/test/typecheck generated only disposable ignored caches, bytecode, and
  `dist/`; those exact paths were removed afterward.
- No live provider or customer call was made. Fixtures and the SQLite ledger
  used synthetic values only.

## README and workflow checks

README links and referenced repository paths exist, and the documented commands
are runnable. The workflow parses as YAML, calls `make verify`, and pins:

- checkout `11bd71901bbe5b1630ceea73d27597364c9af683` (`v4.2.2`)
- setup-python `42375524e23c412d93fb67b49958b491fce71c38` (`v5.4.0`)
- setup-uv `cec208311dfd045dd5311c1add060b2062131d57` (`v8.0.0`)

GitHub CI has not run: the project has no remote. The workflow is configured
and its local equivalent was verified above.

## Cleanliness and limitations

After cleanup, the fresh clone remained at SHA A with empty `git status` and no
remotes. `git diff --check` passed. This is a local, offline, synthetic proof;
it does not establish external GitHub CI execution, live provider behavior,
production deployment readiness, or real-customer data handling.

Verifier: Codex, GPT-5.6 Luna, medium reasoning effort.
