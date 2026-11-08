"""DDP-311 收尾检查：临时数据库、端口与临时目录均已清理。"""

from __future__ import annotations

import socket
from pathlib import Path

import psycopg

with psycopg.connect(
    "postgresql://ddp:ddp@127.0.0.1:55432/postgres", autocommit=True
) as conn:
    databases = [
        row[0]
        for row in conn.execute(
            "select datname from pg_database where datname like 'ddp_qa_311%'"
        )
    ]
print(f"残留临时数据库={databases}")

for port in (9100, 8010):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            print(f"端口 {port}=OPEN（未清理）")
    except OSError:
        print(f"端口 {port}=CLOSED（已清理）")

leftovers = [str(path) for path in Path("/tmp").glob("ddp311*")]
print(f"残留临时目录={leftovers}")
