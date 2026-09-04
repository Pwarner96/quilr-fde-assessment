"""Small reviewer demonstration of valid and invalid MCP wire calls."""

from __future__ import annotations

import json
import subprocess
import sys


def main() -> None:
    messages = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "demo", "version": "1"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "get_customer_record",
                "arguments": {"customer_id": "CUST-12345"},
            },
        },
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "get_customer_record",
                "arguments": {"customer_id": "bad"},
            },
        },
    ]
    proc = subprocess.Popen(
        [sys.executable, "-m", "fde_assessment.task1_mcp_server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert proc.stdin and proc.stdout and proc.stderr
    proc.stdin.write("\n".join(json.dumps(message) for message in messages) + "\n")
    proc.stdin.flush()
    output = [proc.stdout.readline().rstrip() for _ in range(3)]
    proc.stdin.close()
    for line in output:
        print(line)
    stderr = proc.stderr.read()
    code = proc.wait()
    print(f"demo exit={code}; diagnostics_on_stderr={bool(stderr)}")


if __name__ == "__main__":
    main()
