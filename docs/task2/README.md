# Task 2 MCP security gateway

Run the deterministic demo with `uv run python scripts/demo_task2.py`; it mints
synthetic local HS256 credentials and demonstrates an allowed viewer call and a
viewer `admin_` denial. The gateway uses a fixed destination and forwards only
content type, a safe correlation ID, and a separate synthetic service credential.

Authentication is 401 for missing, malformed, invalid, expired, or incorrectly
claimed tokens, and 403 for a valid token with an unsupported role. Viewer tool
names beginning literally with `admin_` receive local JSON-RPC `-32001`; this
decision happens before the forwarder.

Downstream failures are mapped to a stable 502 response without exception text,
paths, hosts, credentials, or request bodies. This is a test gateway: the key is
synthetic and process-local, and `iat` is required metadata rather than replay
protection. Run `uv run pytest tests/task2 -q`, the owned-path Ruff and mypy
commands, and `make verify` for verification.
