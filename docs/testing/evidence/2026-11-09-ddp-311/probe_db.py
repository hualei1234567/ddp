"""DDP-311 数据库与 MinIO 版本探测（只读）。"""

from __future__ import annotations

import subprocess

import psycopg

CANDIDATES = (
    "postgresql://ddp:ddp@127.0.0.1:55432/postgres",
    "postgresql://ddp:ddp@127.0.0.1:5432/postgres",
    "postgresql://postgres:postgres@127.0.0.1:55432/postgres",
    "postgresql://postgres:postgres@127.0.0.1:5432/postgres",
)


def main() -> int:
    for url in CANDIDATES:
        try:
            with psycopg.connect(url, connect_timeout=3) as conn:
                row = conn.execute(
                    "select version(), current_user, current_database(), "
                    "current_setting('server_version_num')"
                ).fetchone()
                superuser = conn.execute(
                    "select rolsuper, rolcreatedb from pg_roles where rolname = current_user"
                ).fetchone()
                print(f"PG_OK url={url}")
                print(f"  version={row[0]}")
                print(f"  user={row[1]} db={row[2]} server_version_num={row[3]}")
                print(f"  rolsuper={superuser[0]} rolcreatedb={superuser[1]}")
        except Exception as exc:  # noqa: BLE001
            print(f"PG_FAIL url={url} error={exc.__class__.__name__}: {exc}")
    try:
        result = subprocess.run(
            ["/usr/local/bin/minio", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        print(f"MINIO_VERSION stdout={result.stdout.strip()!r}")
        print(f"MINIO_VERSION stderr={result.stderr.strip()!r}")
    except Exception as exc:  # noqa: BLE001
        print(f"MINIO_VERSION_FAIL {exc.__class__.__name__}: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
