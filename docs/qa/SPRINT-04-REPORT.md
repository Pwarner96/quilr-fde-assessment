# Sprint 04 adversarial QA report

## Scope

Audited integrated commit `a99f3507195a6ac65fb150df8298ae4610def6c3` on
`qa/adversarial`, using only synthetic data and local/offline providers.
Post-remediation QA was executed on the QA lineage containing remediation
cherry-pick `a753e3800a4ac099d1bcb269b0ef722dbe18ea1f`, corresponding to
integrated remediation `cf6aab05e14afde9feda72e7046be7c8e24a1a63`.

## Findings

### QA-001 — Task 2 duplicate-key JSON-RPC code — REMEDIATED

- Severity: high
- Owner: Thread 02
- Requirement: Duplicate JSON object keys must be rejected locally as controlled JSON-RPC `-32600`.
- Reproduction: POST `/mcp` with authenticated synthetic viewer token and body `{"jsonrpc":"2.0","id":1,"id":2,"method":"tools/list"}`.
- Observed: HTTP 400, JSON-RPC error code `-32700`, request ID `null`; forwarder call count `0`.
- Expected: HTTP 400, JSON-RPC error code `-32600`, with zero forwarded bytes.
- Impact: Scoring-critical Task 2 protocol invariant is violated, although the zero-forward security invariant passes.
- Regression path: `tests/adversarial/test_sprint04_findings.py::test_task2_duplicate_keys_are_invalid_request_and_forward_zero_bytes`.
- Remediation source: `70fbc5542dbb541cdf9c36f232e93cae9e225684`.
- Integrated remediation: `cf6aab05e14afde9feda72e7046be7c8e24a1a63`.
- Unchanged rerun: the strict-xfail test XPASSED before marker removal, proving the existing assertion now passes; after removing only the temporary xfail marker, the unchanged assertions pass.

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

The baseline and rerun isolated environment was `/tmp/quilr-qa-s4.Qh6zmG/.venv`,
using Python 3.12.14, uv 0.12.9, offline mode, and the mandated shared uv
cache. The earlier bare-`uv` result was a corrected harness invocation mistake:
the shell assignment was not exported, and GNU Make honors an actually exported
`UV` variable. No live network or real credentials were used. No new findings
were discovered after remediation.

## Current verdict

ACCEPT
