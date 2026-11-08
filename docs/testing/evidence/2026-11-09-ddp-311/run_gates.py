"""DDP-311 回归门禁：ruff、Secret 扫描、真实 PostgreSQL pytest。

临时数据库仅用于 pytest 的 schema 级 PG 用例，结束后删除。
输出 gates-311.log、pytest-311.log、pytest-311.xml。
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parents[4]
BACKEND = ROOT / "backend"
EVIDENCE = ROOT / "docs" / "testing" / "evidence" / "2026-11-09-ddp-311"
PG_ADMIN_DSN = "postgresql://ddp:ddp@127.0.0.1:55432/postgres"
PYTEST_URL_TEMPLATE = "postgresql+psycopg://ddp:ddp@127.0.0.1:55432/{dbname}"


def run(command: list[str], *, env: dict[str, str] | None = None, timeout: int = 900) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def create_database(dbname: str) -> None:
    with psycopg.connect(PG_ADMIN_DSN, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(dbname)))


def drop_database(dbname: str) -> None:
    with psycopg.connect(PG_ADMIN_DSN, autocommit=True) as conn:
        conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = %s AND pid <> pg_backend_pid()",
            (dbname,),
        )
        conn.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(dbname)))


def main() -> int:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    log_path = EVIDENCE / "gates-311.log"
    pytest_log = EVIDENCE / "pytest-311.log"
    junit = EVIDENCE / "pytest-311.xml"
    lines: list[str] = []
    failures = 0

    ruff = run(
        [
            str(BACKEND / ".venv" / "bin" / "ruff"),
            "check",
            "backend/app",
            "backend/tests",
            "backend/migrations",
        ]
    )
    lines.append("$ ruff check backend/app backend/tests backend/migrations")
    lines.append(ruff.stdout + ruff.stderr)
    lines.append(f"exit={ruff.returncode}\n")
    if ruff.returncode != 0:
        failures += 1
    print(f"ruff exit={ruff.returncode}")
    print((ruff.stdout + ruff.stderr).strip()[:2000])

    secrets = run([sys.executable, "scripts/check_secrets.py"])
    lines.append("$ python scripts/check_secrets.py")
    lines.append(secrets.stdout + secrets.stderr)
    lines.append(f"exit={secrets.returncode}\n")
    if secrets.returncode != 0:
        failures += 1
    print(f"check_secrets exit={secrets.returncode}")
    print((secrets.stdout + secrets.stderr).strip()[:2000])

    dbname = f"ddp_qa_311_pytest_{uuid4().hex[:8]}"
    create_database(dbname)
    try:
        env = dict(os.environ)
        env["DDP_ENV"] = "local"
        env["DDP_TEST_POSTGRES_URL"] = PYTEST_URL_TEMPLATE.format(dbname=dbname)
        env["DDP_DATABASE_URL"] = PYTEST_URL_TEMPLATE.format(dbname=dbname)
        pytest = run(
            [
                sys.executable,
                "-m",
                "pytest",
                "backend/tests",
                "-q",
                "-rs",
                f"--junitxml={junit}",
            ],
            env=env,
            timeout=1200,
        )
        pytest_log.write_text(pytest.stdout + pytest.stderr, encoding="utf-8")
        lines.append(f"$ pytest backend/tests -q -rs（DDP_TEST_POSTGRES_URL={dbname}）")
        lines.append(pytest.stdout[-8000:] + pytest.stderr[-4000:])
        lines.append(f"exit={pytest.returncode}\n")
        if pytest.returncode != 0:
            failures += 1
        print(f"pytest exit={pytest.returncode}")
        print((pytest.stdout + pytest.stderr).strip()[-3000:])
    finally:
        drop_database(dbname)
        lines.append(f"临时 pytest 数据库 {dbname} 已删除\n")

    log_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"门禁结果：{'PASS' if failures == 0 else 'FAIL'}；日志={log_path}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
