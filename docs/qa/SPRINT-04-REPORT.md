# Sprint 04 adversarial QA report

## Scope

Audited integrated commit `a99f3507195a6ac65fb150df8298ae4610def6c3` on
`qa/adversarial`, using only synthetic data and local/offline providers.

## Confirmed findings

### QA-001 — Task 2 duplicate-key JSON-RPC code

- Severity: high
- Owner: Thread 02
- Requirement: Duplicate JSON object keys must be rejected locally as controlled JSON-RPC `-32600`.
- Reproduction: POST `/mcp` with authenticated synthetic viewer token and body `{"jsonrpc":"2.0","id":1,"id":2,"method":"tools/list"}`.
- Observed: HTTP 400, JSON-RPC error code `-32700`, request ID `null`; forwarder call count `0`.
- Expected: HTTP 400, JSON-RPC error code `-32600`, with zero forwarded bytes.
- Impact: Scoring-critical Task 2 protocol invariant is violated, although the zero-forward security invariant passes.
- Regression path: `tests/adversarial/test_sprint04_findings.py::test_task2_duplicate_keys_are_invalid_request_and_forward_zero_bytes`.

The earlier QA transcript incorrectly described a harness invocation issue as
QA-002. The shell assignment used for that attempt was not exported. GNU Make
does honor an actually exported `UV` environment variable, so QA-002 is
removed and is not a repository finding.

## Passing high-risk areas

- Task 1 stdio protocol test: pass.
- Task 2 invalid-auth zero-forward probe: pass.
- Task 3 split-token redaction, framing, and early safe-prefix probes: pass.
- Task 4 exact 64-way, 7-token concurrent admission: 14 admitted, 50 quota rejected, zero busy/state failures, active charge 98.
- Existing integrated suite: 170 passed before QA tests.

## Evidence and limitations

The baseline isolated environment was `/tmp/quilr-qa-s4.Qh6zmG/.venv`, using
Python 3.12.14, uv 0.12.9, offline mode, and the mandated shared uv cache.
The initial bare-`uv` Makefile failure and the direct reproductions above were
captured in the Thread 05 execution transcript. No live network or real
credentials were used. The QA tests are strict expected-behavior regressions
marked xfail only while the production defects remain unresolved.

## Current verdict

DO NOT SUBMIT — solely because confirmed high finding QA-001 remains open.
