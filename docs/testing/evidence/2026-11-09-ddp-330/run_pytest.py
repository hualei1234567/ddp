#!/usr/bin/env python3
"""DDP-330 pytest 运行器：创建独立 PostgreSQL 测试库并输出 JUnit 证据。

不删除测试库，便于复核；不触碰 staging 数据。
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[4]
BACKEND = ROOT / "backend"
EVIDENCE = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND))

os.environ["DDP_ENV"] = "local"
os.environ["DDP_LOCAL_HEADER_AUTH"] = "true"

ADMIN = "postgresql://ddp:ddp@127.0.0.1:55432/postgres"
DB_NAME = f"ddp_qa_d330_pytest_{os.getpid()}_{int(time.time())}"
with psycopg.connect(ADMIN, autocommit=True) as connection:
    connection.execute(f'CREATE DATABASE "{DB_NAME}"')

os.environ["DDP_TEST_POSTGRES_URL"] = (
    f"postgresql+psycopg://ddp:ddp@127.0.0.1:55432/{DB_NAME}"
)

import pytest  # noqa: E402

if __name__ == "__main__":
    print(f"DDP-330 pytest PostgreSQL 测试库：{DB_NAME}")
    raise SystemExit(
        pytest.main(
            [
                "-o",
                "addopts=",
                "-q",
                "--junitxml",
                str(EVIDENCE / "pytest.postgres.xml"),
                str(ROOT / "backend" / "tests"),
            ]
        )
    )
