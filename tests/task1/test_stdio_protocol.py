from __future__ import annotations

import json
import os
import select
import subprocess
import sys


def _run_wire(
    messages: list[dict[str, object]], response_count: int
) -> tuple[list[dict[str, object]], str, int]:
    env = {**os.environ, "PYTHONPATH": "src"}
    proc = subprocess.Popen(
        [sys.executable, "-m", "fde_assessment.task1_mcp_server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    assert proc.stdin and proc.stdout and proc.stderr
    proc.stdin.write("\n".join(json.dumps(message) for message in messages) + "\n")
    proc.stdin.flush()
    lines: list[str] = []
    for _ in range(response_count):
        ready, _, _ = select.select([proc.stdout], [], [], 5)
        assert ready, "timed out waiting for MCP response"
        line = proc.stdout.readline()
        assert line, "server closed stdout before the expected response count"
        lines.append(line)
    proc.stdin.close()
    stdout_tail = proc.stdout.read()
    stderr = proc.stderr.read()
    code = proc.wait(timeout=5)
    return (
        [json.loads(line) for line in lines + stdout_tail.splitlines(True)],
        stderr,
        code,
    )


def test_stdio_wire_contract_and_eof() -> None:
    messages = [
        {
            "jsonrpc": "2.0",
            "id": "string-id",
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "get_customer_record",
                "arguments": {"customer_id": "CUST-12345"},
            },
        },
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "trigger_refund",
                "arguments": {
                    "customer_id": "CUST-12345",
                    "amount": 1.25,
                    "reason": "customer requested refund",
                },
            },
        },
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {
                "name": "trigger_refund",
                "arguments": {
                    "customer_id": "CUST-99999",
                    "amount": 1,
                    "reason": "customer requested refund",
                },
            },
        },
        {
            "jsonrpc": "2.0",
            "id": 6,
            "method": "tools/call",
            "params": {
                "name": "get_customer_record",
                "arguments": {"customer_id": "bad"},
            },
        },
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {"name": "get_customer_record", "arguments": {}},
        },
        {
            "jsonrpc": "2.0",
            "id": 8,
            "method": "tools/call",
            "params": {
                "name": "get_customer_record",
                "arguments": {
                    "customer_id": "CUST-12345",
                    "extra": "unique-secret-sentinel",
                },
            },
        },
        {
            "jsonrpc": "2.0",
            "id": 9,
            "method": "tools/call",
            "params": {"name": "unknown", "arguments": {}},
        },
    ]
    responses, stderr, code = _run_wire(messages, 9)
    assert code == 0
    assert all(response["jsonrpc"] == "2.0" for response in responses)
    assert responses[0]["id"] == "string-id"
    assert responses[0]["result"]["protocolVersion"] == "2025-11-25"
    assert [tool["name"] for tool in responses[1]["result"]["tools"]] == [
        "get_customer_record",
        "trigger_refund",
    ]
    assert responses[2]["id"] == 3 and responses[2]["result"]["isError"] is False
    assert (
        responses[3]["id"] == 4
        and responses[3]["result"]["structuredContent"]["amount"] == 1.25
    )
    assert responses[4]["id"] == 5 and responses[4]["result"]["isError"] is True
    assert [responses[index]["error"]["code"] for index in (5, 6, 7, 8)] == [-32602] * 4
    assert "starting MCP stdio server" in stderr
    assert "unique-secret-sentinel" not in stderr
