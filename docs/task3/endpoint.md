# Task 3 Streaming Endpoint

`create_app()` exposes `POST /v1/chat/completions` for a deliberately minimal
Chat-Completions-style compatibility subset. Requests require a nonblank model,
at least one nonblank message with role `user`, `assistant`, or `system`, and
`stream: true`. The endpoint uses the deterministic `MockProvider` by default;
`HTTPXProvider` accepts an application-owned `httpx.AsyncClient` and a fixed
destination. It is not a claim of universal OpenAI compatibility.

The response is pull-driven and passes upstream bytes through strict UTF-8,
bounded SSE framing, strict delta parsing, bounded PII redaction, and canonical
SSE serialization. Missing terminal `[DONE]`, malformed input, provider errors,
and cancellation fail closed without an error event or synthesized `[DONE]`.
The endpoint does not implement authentication, retries, provider selection,
or production deployment configuration.
