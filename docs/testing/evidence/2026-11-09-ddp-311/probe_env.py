"""DDP-311 环境探测：解释器、依赖、PostgreSQL、MinIO 二进制。

只读探测，不修改任何业务数据。用法：
    backend/.venv/bin/python docs/testing/evidence/2026-11-09-ddp-311/probe_env.py
"""

from __future__ import annotations

import importlib
import os
import shutil
import socket
import sys
from pathlib import Path


def check_module(name: str) -> str:
    try:
        module = importlib.import_module(name)
    except Exception as exc:  # noqa: BLE001
        return f"MISSING ({exc.__class__.__name__}: {exc})"
    version = getattr(module, "__version__", None)
    return f"OK {version or ''}".strip()


def tcp_probe(host: str, port: int) -> str:
    try:
        with socket.create_connection((host, port), timeout=2):
            return "OPEN"
    except OSError as exc:
        return f"CLOSED ({exc.__class__.__name__}: {exc})"


def main() -> int:
    print(f"python={sys.version}")
    print(f"cwd={Path.cwd()}")
    for name in ("fastapi", "uvicorn", "sqlalchemy", "psycopg", "boto3", "httpx", "alembic"):
        print(f"module[{name}]={check_module(name)}")
    print(f"minio_binary={shutil.which('minio')}")
    for path in ("/usr/local/bin/minio", "/usr/bin/minio"):
        candidate = Path(path)
        print(f"exists[{path}]={candidate.exists()}")
    for host, port in (
        ("127.0.0.1", 55432),
        ("127.0.0.1", 5432),
        ("127.0.0.1", 9100),
        ("127.0.0.1", 8010),
    ):
        print(f"tcp[{host}:{port}]={tcp_probe(host, port)}")
    print(f"env_DDP_DATABASE_URL={'set' if os.environ.get('DDP_DATABASE_URL') else 'unset'}")
    print(
        "env_DDP_TEST_POSTGRES_URL="
        f"{'set' if os.environ.get('DDP_TEST_POSTGRES_URL') else 'unset'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
