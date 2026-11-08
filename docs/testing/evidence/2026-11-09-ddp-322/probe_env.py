#!/usr/bin/env python3
"""DDP-322 环境探测：确认本地 PostgreSQL、依赖与基线提交。

只读操作，不修改数据库内容；仅执行 SELECT version()。
"""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "backend"))

PG_ADMIN = os.environ.get(
    "DDP322_PG_ADMIN",
    "postgresql+psycopg://ddp:ddp@127.0.0.1:55432/postgres",
)


def main() -> int:
    print("== 依赖版本 ==")
    for name in ("sqlalchemy", "psycopg", "httpx", "uvicorn", "pytest", "pydantic", "fastapi"):
        try:
            module = importlib.import_module(name)
        except Exception as exc:  # noqa: BLE001
            print(f"{name}: 不可用 ({exc})")
            continue
        print(f"{name}: {getattr(module, '__version__', 'unknown')}")
    try:
        module = importlib.import_module("alembic" + ".config")
        print(f"alembic.config: {module.__file__}")
    except Exception as exc:  # noqa: BLE001
        print(f"alembic.config: 不可用 ({exc})")

    print("== PostgreSQL 连接 ==")
    try:
        from sqlalchemy import create_engine, text

        engine = create_engine(PG_ADMIN)
        with engine.connect() as connection:
            row = connection.execute(
                text("SELECT version(), current_database(), current_user")
            ).one()
            print(f"version: {row[0]}")
            print(f"database: {row[1]}, user: {row[2]}")
        engine.dispose()
    except Exception as exc:  # noqa: BLE001
        print(f"连接失败: {type(exc).__name__}: {exc}")
        return 1
    print("PROBE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
