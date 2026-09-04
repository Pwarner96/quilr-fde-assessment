# Task 1 MCP server

Run the server with `python -m fde_assessment.task1_mcp_server`. Run the reviewer
demo with `python scripts/demo_task1.py` (with the project environment active).

The server advertises exactly two tools:

- `get_customer_record`: `{customer_id}` where the value is a strict,
  case-sensitive `CUST-` plus five ASCII digits.
- `trigger_refund`: `{customer_id, amount, reason}`. Amount is a finite positive
  JSON integer or fractional number. Reason is a strict string with at least 10
  characters after surrounding whitespace is trimmed.

Malformed or invalid tool arguments, including unknown tools, are JSON-RPC
errors with code `-32602`. A valid request declined by the synthetic business
rules is instead a normal tool result with `isError=true`.

The server uses newline-delimited MCP JSON on stdin/stdout. Stdout contains
protocol messages only; diagnostics go to stderr. EOF exits cleanly with status
0. The pinned SDK is `mcp==2.1.1`; tests observed negotiation of protocol
revision `2025-11-25`.

The generated reason schema exposes `minLength=10` and documents trimming.
JSON Schema cannot express the runtime's surrounding-whitespace trim before
length counting, so runtime validation remains stricter for padded values.

Verification:

```text
uv run pytest tests/task1 -q
uv run pytest tests/task1/test_stdio_protocol.py -q
uv run ruff check src/fde_assessment/task1_mcp_server tests/task1 scripts/demo_task1.py
uv run mypy src/fde_assessment/task1_mcp_server
uv run python scripts/demo_task1.py
make verify
git diff --check
```
