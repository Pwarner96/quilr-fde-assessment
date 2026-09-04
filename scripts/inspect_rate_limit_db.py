"""Print non-secret reservation facts for a local SQLite ledger."""

import argparse
import sqlite3
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("database", type=Path)
args = parser.parse_args()
with sqlite3.connect(args.database) as connection:
    for row in connection.execute(
        "SELECT id, length(tenant_fingerprint), request_id, created_at_ms, charged_tokens "
        "FROM reservations ORDER BY id"
    ):
        print(row)
