# Requirements traceability

The exact QA-approved behavior/evidence base is `a4e3e5c781fec7d7eaed779bbe9b2f0ab4b1affa`; Thread 05's verdict is exactly `ACCEPT`.

| Requirement | Implementation | Exact evidence | Command | State |
| --- | --- | --- | --- | --- |
| Task 1: two strict MCP tools and distinct business failure | `src/fde_assessment/task1_mcp_server/server.py` | `test_exact_tools_and_generated_schemas`; `test_handlers_distinguish_success_business_failure_and_protocol_error` | `uv run --frozen pytest tests/task1/test_server.py -q` | QA-VERIFIED |
| Task 1: stdio wire/EOF/stdout-stderr contract | `src/fde_assessment/task1_mcp_server/__main__.py` | `test_stdio_wire_contract_and_eof`; characterization tests | `uv run --frozen pytest tests/task1/test_stdio_protocol.py tests/task1/test_sdk_characterization.py -q` | QA-VERIFIED |
| Task 2: HS256 auth, 401/403, viewer policy | `src/fde_assessment/task2_mcp_gateway/gateway.py` | `test_auth_matrix_and_allowed_forwarding`; `test_viewer_admin_denial_is_zero_call` | `uv run --frozen pytest tests/task2/test_gateway.py -q` | QA-VERIFIED |
| Task 2: duplicate keys `400/-32600`, null ID, zero calls | `src/fde_assessment/task2_mcp_gateway/gateway.py` | `test_duplicate_keys_are_invalid_requests_with_null_id_and_zero_forwarding`; QA-001 | `uv run --frozen pytest tests/adversarial -q` | QA-VERIFIED |
| Task 2: bearer stripping | `src/fde_assessment/task2_mcp_gateway/gateway.py` | `test_forwarding_allowlist_destination_service_credential_and_safe_correlation` | `uv run --frozen pytest tests/task2/test_gateway.py -q` | QA-VERIFIED |
| Task 3: bounded SSE framing and one-choice parsing | `src/fde_assessment/task3_stream_guardrail/core.py` | `test_sse_split_crlf_and_event_bound`; `test_strict_json_rejects_duplicates_nan_unknown_and_bad_index` | `uv run --frozen pytest tests/task3 -q` | QA-VERIFIED |
| Task 3: bounded redaction and abnormal termination | `src/fde_assessment/task3_stream_guardrail/core.py`, `src/fde_assessment/task3_stream_guardrail/email_redactor.py`, `src/fde_assessment/task3_stream_guardrail/app.py` | `test_overflow_replaces_once_and_drains_in_constant_memory`; `test_abnormal_provider_inputs_close_and_emit_no_terminal_or_error` | `uv run --frozen pytest tests/task3 -q` | QA-VERIFIED |
| Task 4: HMAC fingerprint and atomic admission | `src/fde_assessment/task4_model_router/limiter.py` | `test_only_hmac_fingerprint_is_persisted`; `test_killer_race_exactly_admits_14_and_never_oversubscribes` | `uv run --frozen pytest tests/task4/test_limiter.py -q` | QA-VERIFIED |
| Task 4: fallback and cancellation rules | `src/fde_assessment/task4_model_router/core.py` | `test_every_429_body_falls_back_once`; `test_caller_cancellation_does_not_fallback` | `uv run --frozen pytest tests/task4 -q` | QA-VERIFIED |
| Task 4: safe HTTP envelope | `src/fde_assessment/task4_model_router/app.py` | `test_endpoint_both_provider_failure_is_exact_sanitized_502`; `test_http_adapter_classifies_429_before_body_parsing` | `uv run --frozen pytest tests/task4 -q` | QA-VERIFIED |
| Cross-task adversarial review | `tests/adversarial/test_sprint04_findings.py` | Five adversarial tests; QA report | `uv run --frozen pytest tests/adversarial -q` | QA-VERIFIED |
| Integrated release behavior | `src/fde_assessment/task1_mcp_server/`, `src/fde_assessment/task2_mcp_gateway/`, `src/fde_assessment/task3_stream_guardrail/`, `src/fde_assessment/task4_model_router/` | 176-test full run; four demos | `make verify; for n in 1 2 3 4; do uv run --frozen python scripts/demo_task${n}.py; done` | QA-VERIFIED |

Observed full verification was 176 passed with one nonblocking Starlette `BlockingPortal` deprecation warning. CI is configured but has not run on a remote because this repository has no remote.
