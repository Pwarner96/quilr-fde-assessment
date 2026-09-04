from __future__ import annotations

import json
import os
import subprocess
import sys


def _run(message: str) -> list[dict[str, object]]:
    result = subprocess.run(
        [sys.executable, "-m", "fde_assessment.task1_mcp_server"],
        input=message + "\n",
        text=True,
        capture_output=True,
        env={**os.environ, "PYTHONPATH": "src"},
        timeout=5,
        check=True,
    )
    return [json.loads(line) for line in result.stdout.splitlines()]


def test_pinned_sdk_malformed_json_and_invalid_request_are_silent() -> None:
    assert _run("not json") == []
    assert _run("{}") == []


def test_pinned_sdk_unknown_method_is_method_not_found() -> None:
    response = _run(
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "unknown/method"})
    )[0]
    assert response["error"]["code"] == -32601


def test_pinned_sdk_notification_has_no_response() -> None:
    assert (
        _run(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}))
        == []
    )


def test_pinned_sdk_metadata_and_non_object_arguments() -> None:
    message = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "get_customer_record",
            "arguments": [],
            "_meta": {"trace": "ignored"},
        },
    }
    response = _run(json.dumps(message))[0]
    assert response["error"]["code"] == -32602
