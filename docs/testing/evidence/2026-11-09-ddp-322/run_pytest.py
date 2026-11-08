#!/usr/bin/env python3
"""DDP-322 pytest 运行器：启用 PostgreSQL 集成用例并输出 JUnit 证据。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
BACKEND = ROOT / "backend"
EVIDENCE = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND))

os.environ["DDP_ENV"] = "local"
os.environ["DDP_LOCAL_HEADER_AUTH"] = "true"
os.environ["DDP_TEST_POSTGRES_URL"] = (
    "postgresql+psycopg://ddp:ddp@127.0.0.1:55432/postgres"
)

import pytest  # noqa: E402

if __name__ == "__main__":
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
