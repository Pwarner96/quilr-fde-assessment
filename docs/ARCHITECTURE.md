# Architecture

## Four independent lanes

```text
Task 1: stdin/stdout -> low-level MCP Server -> synthetic tools
Task 2: HTTP /mcp -> security gateway -> fixed downstream mock
Task 3: HTTP /v1/chat/completions -> SSE guardrail -> provider mock
Task 4: HTTP /v1/completions -> quota/router -> provider mocks

No runtime Task 1 -> Task 2 -> Task 3 -> Task 4 chain; no shared runtime state.
```

### Task 1 — MCP stdio server

`task1_mcp_server.server` owns the low-level MCP SDK `Server`, two tool schemas, strict Pydantic validation, and synthetic outcomes. The stdio adapter owns newline-delimited protocol traffic. stdout is protocol-only; the safe logger writes diagnostics to stderr. Invalid arguments and unknown tools become top-level `-32602`; a valid refund declined by synthetic rules is a normal tool result with `isError=true`.

### Task 2 — security gateway

`task2_mcp_gateway.gateway` owns HTTP authentication, duplicate-key-aware JSON parsing, role authorization, a fixed downstream destination, and a forwarder interface. `mocks.py` supplies deterministic downstream behavior. The caller bearer is stripped and replaced with a synthetic service credential. Gateway tests count forwarding and downstream calls, proving blocked calls do neither.

```text
HTTP -> HS256/claims (401/403) -> strict JSON-RPC (400 codes)
     -> viewer admin_* policy (200/-32001, no forward)
     -> allowlisted headers + service credential -> fixed downstream
```

### Task 3 — streaming guardrail

`task3_stream_guardrail.core` owns strict UTF-8 decoding, SSE framing, one-choice delta parsing, bounded candidate scanning, and canonical serialization. `app.py` owns FastAPI and provider lifecycle; the default provider is deterministic. The contract is a Chat-Completions-style SSE compatibility subset, not universal OpenAI compatibility: one streamed text choice, 65,536-byte unfinished-event bound, 320-character email bound, 64-character numeric bound, LF/CR/CRLF framing, conservative overflow, and no custom event frame. Abnormal termination drops pending candidates and omits `[DONE]`.

### Task 4 — quota and model router

`limiter.py` owns SQLite reservations, `core.py` owns typed provider routing, and `app.py` assembles FastAPI, fixed adapters, lifecycle, and public errors.

```text
request -> strict body/headers -> HMAC fingerprint -> BEGIN IMMEDIATE reservation
        -> primary: 200 normalize | 429/deadline cleanup then one secondary
        -> other failure/cancellation: no fallback -> stable public response
```

Active rows satisfy `created_at_ms > now_ms - 60_000`; reservation precedes provider invocation. WAL helps reader/writer coexistence but writers serialize. The secondary 3000 ms cap is a project safety choice. Only fingerprints, request IDs, timestamps, and charges are retained in the ledger.

## Production evolution

Production would add managed identity/secrets and rotation, replay policy, TLS and egress controls, migrations and retention, telemetry, capacity testing, provider contract/version management, and explicit SLOs. The repository does not fabricate a composition layer or claim production readiness.
