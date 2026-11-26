"""写路径压测脚本的安全护栏测试（审计 R1）。

压测脚本能建库删库——护栏失效等于给生产库留了个 DROP DATABASE 后门。
本测试只验证护栏，不跑真实压测（那是 --execute 的事）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# CI 里 pytest 的 rootdir 是 backend/，仓库根的 scripts/ 不在 sys.path 上，
# 直接 import scripts.perf.core 会 ModuleNotFoundError（本地因 PYTHONPATH 巧合能过）。
_REPO = Path(__file__).resolve().parents[3]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from scripts.perf.core import (  # noqa: E402
    ensure_safe_base_dsn,
    ensure_safe_perf_db_name,
)


def test_rejects_non_loopback_host() -> None:
    """非本机数据库一律拒绝（防止对生产/共享库执行 DDL）。"""
    with pytest.raises(ValueError, match="非本机"):
        ensure_safe_base_dsn("postgresql+psycopg://ddp:ddp@db.prod.example.com:5432/postgres")


def test_rejects_non_whitelisted_base_database() -> None:
    """建库连接点只允许 postgres / ddp，避免误在业务库上跑 DDL。"""
    with pytest.raises(ValueError, match="白名单"):
        ensure_safe_base_dsn("postgresql+psycopg://ddp:ddp@localhost:55432/some_app_db")


def test_accepts_loopback_postgres() -> None:
    target = ensure_safe_base_dsn("postgresql+psycopg://ddp:ddp@127.0.0.1:55432/postgres")
    assert target.database == "postgres"


def test_temp_db_name_requires_prefix() -> None:
    """临时库名必须以 ddp_perf_ 开头，便于识别与清理。"""
    with pytest.raises(ValueError, match="ddp_perf_"):
        ensure_safe_perf_db_name("my_scratch_db")


def test_temp_db_name_rejects_unsafe_identifier() -> None:
    """库名会拼进 DDL，必须是小写安全标识符。"""
    with pytest.raises(ValueError):
        ensure_safe_perf_db_name("ddp_perf_write_; DROP DATABASE ddp; --")
