# Security posture

This describes controls verified in the assessment build; it is not a claim of production security.

## Trust boundaries and controls

- The four components are independent. Untrusted input enters each protocol or HTTP boundary and reaches only its fixed handler/provider seam.
- Task 1 uses the low-level MCP SDK, exactly two strict tools, forbidden extra fields, strict IDs and amounts, and separates `-32602` protocol errors from valid business decline. stdout contains protocol traffic only; diagnostics go to stderr.
- Task 2 requires a local signed HS256 token with issuer, audience, subject, role, `iat`, and `exp`. Missing/invalid credentials are 401; unsupported authenticated roles are 403. Viewer `admin_*` calls return 200/`-32001` before forwarding.
- Duplicate JSON keys are rejected as 400/`-32600` with null ID. Tests prove zero forwarder and downstream calls. The caller bearer is stripped; only a safe correlation ID, content type, and separate service credential go down.
- Task 3 strictly parses UTF-8/SSE, bounds events and PII candidates, and fails closed on malformed or abnormal provider input without synthesizing `[DONE]`.
- Task 4 uses HMAC-SHA256 tenant fingerprints, atomic `BEGIN IMMEDIATE` admission, strict active-window accounting, pre-provider reservation, and fallback only for primary 429 or deadline loss. Cancellation does not retry.

## PII, secrets, and errors

All demos use synthetic customer IDs, email-like content, tokens, and providers. Task 3 supports bounded email, numeric, SSN, and card candidate redaction. Task 4 persists no raw tenant key or fingerprint secret; its inspection script emits only IDs, fingerprint lengths, timestamps, and charges. Tests check sensitive values do not appear in storage, logs, or public error strings.

Task 2 signing material and service credentials are process-local test material. There is no replay protection, key rotation, asymmetric JWT, OAuth client credential, or customer-system authorization. Public errors do not expose upstream bodies, URLs, credentials, prompts, completions, paths, or exceptions.

## QA and limitations

Thread 05's final verdict is exactly **ACCEPT**, recorded in [docs/qa/SPRINT-04-REPORT.md](qa/SPRINT-04-REPORT.md). QA-001 duplicate-key handling was remediated; QA-002 was corrected as a harness/reporting issue and is not an open finding.

Remaining limitations are process-local identity, fixed mock-first destinations, narrow protocol subsets, SQLite writer serialization, and no live provider or deployment validation.

## Production hardening

Add an external identity provider, managed secret storage/rotation, replay and nonce policy, TLS/egress controls, redacted audit logs, metrics/traces/alerts, retention and migration policy, provider contract tests, load testing, and explicit cancellation/deadline SLOs before deployment.
