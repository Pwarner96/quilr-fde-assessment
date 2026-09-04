# Quilr FDE assessment

This repository demonstrates four independently runnable Python components for a fictional customer-data and completion platform assessment. They are separate assessment lanes, not one deployed request chain: each has its own boundary, tests, mocks, and state model.

| Task | Component | What is demonstrated |
| --- | --- | --- |
| 1 | MCP stdio server | Low-level MCP protocol, two strict tools, protocol-safe diagnostics |
| 2 | MCP security gateway | HS256 authentication, authorization, validation, and safe forwarding |
| 3 | Streaming guardrail | Bounded Chat-Completions-style SSE parsing and PII redaction |
| 4 | Model router/limiter | Atomic tenant quota admission, provider fallback, and safe HTTP envelope |

## Architecture at a glance

```text
Lane 1: stdin/stdout -> MCP low-level Server -> synthetic tool handlers
Lane 2: HTTP client -> security gateway -> fixed downstream mock
Lane 3: HTTP client -> SSE guardrail -> deterministic provider mock
Lane 4: HTTP client -> quota/router -> primary or secondary provider mock

        independent boundaries; no runtime Task 1 -> Task 2 -> Task 3 -> Task 4 chain
```

Detailed boundaries and state are in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Stack and prerequisites

- Python 3.12 (`.python-version`); the project requires `>=3.12,<3.13`.
- `uv` with the locked dependency set in `uv.lock` (the build backend is `uv_build==0.12.9`).
- No paid provider account, customer system, real credential, or undocumented `.env` is needed. Tests and demos are mock-first and synthetic.

## Install and verify

```bash
make install
make verify
```

For a machine without `make`, derive the equivalent commands from the Makefile; the canonical local commands are `uv sync --frozen --all-groups`, followed by the Make targets. The accepted release-candidate evidence recorded 176 passed tests. Re-run locally rather than treating that historical count as a promise; one nonblocking Starlette `BlockingPortal` deprecation warning was observed.

## Focused demos

Each demo is deterministic and local:

```bash
uv run --frozen python scripts/demo_task1.py
uv run --frozen python scripts/demo_task2.py
uv run --frozen python scripts/demo_task3.py
uv run --frozen python scripts/demo_task4.py
```

The demos show, respectively, MCP initialization/validation, gateway allow and deny behavior, split-fragment email redaction, and quota/fallback scenarios.

## Repository map

```text
src/fde_assessment/task1_mcp_server/        Task 1 MCP boundary
src/fde_assessment/task2_mcp_gateway/      Task 2 gateway and mocks
src/fde_assessment/task3_stream_guardrail/ Task 3 SSE core and endpoint
src/fde_assessment/task4_model_router/     Task 4 limiter, router, endpoint
tests/task1..task4/                         component tests
tests/adversarial/                          independent QA regressions
scripts/demo_task*.py                       focused reviewer demos
docs/task1..task4/                          task-specific contracts
docs/qa/                                     QA evidence and verdict
docs/ARCHITECTURE.md                        boundaries and flows
docs/SECURITY.md                            implemented controls and limits
docs/TEST_MATRIX.md                         requirement-to-evidence map
docs/INTERVIEW_WALKTHROUGH.md               five-minute demonstration
```

## Key design decisions

- The four tasks remain independently testable and runnable.
- Invalid protocol input is separated from valid business/provider failure.
- Streaming and quota paths use bounded state and deterministic mocks.
- Tenant identity is represented in SQLite only by an HMAC-SHA256 fingerprint.
- Reviewer commands use the repository Makefile as the command authority.

See [docs/SECURITY.md](docs/SECURITY.md) for the security posture and [docs/TEST_MATRIX.md](docs/TEST_MATRIX.md) for exact test evidence.

## QA, assumptions, and production evolution

Independent QA verdict: **ACCEPT**. The evidence is in [docs/qa/SPRINT-04-REPORT.md](docs/qa/SPRINT-04-REPORT.md). Assumptions and limitations are in [docs/ASSUMPTIONS.md](docs/ASSUMPTIONS.md), and requirement coverage is in [docs/REQUIREMENTS_TRACEABILITY.md](docs/REQUIREMENTS_TRACEABILITY.md).

This is assessment code, not a production security or reliability claim. A production version would add managed identity and secret storage, operational telemetry, deployment policy, real provider contract tests, durable migrations, load testing, and explicit availability/retry budgets. See the architecture and security documents for component-specific tradeoffs.

## CI status

`.github/workflows/verify.yml` is configured to run the same locked Makefile checks and package build on GitHub-hosted Linux. There is currently no Git remote, so CI has not run externally; local verification is the observed evidence.
