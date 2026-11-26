"""维护 Worker 测试（审计 R3 补测：该文件此前覆盖率 0%）。

覆盖备份（成功/pg_dump 失败/产物过小/子进程异常）、保留期裁剪、连接参数解析、
归档清扫（终态可删、PENDING/FAILED 必须保留、幂等）、积压健康指标、主循环退出。

设计约定：**备份路径用 monkeypatch 假 subprocess**（不真跑 pg_dump）；
**清扫与积压路径用真实 SQLite**（这是本文件的核心，也是此前完全没有测的部分）。
"""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
import sqlalchemy as sa
from app.core.config import Settings
from app.infrastructure.db.base import Base
from app.infrastructure.db.models.collection import (
    IdempotencyRecord,
)
from app.infrastructure.db.models.collection import (
    OutboxEvent as OutboxModel,
)
from app.infrastructure.db.models.identity import Project, User
from app.infrastructure.db.session import create_db_engine, create_session_factory
from app.workers.maintenance import MaintenanceWorker
from sqlalchemy import select

NOW = datetime(2026, 11, 27, 12, 0, tzinfo=UTC)
DB = "postgresql+psycopg://ddp:secretpw@localhost:55432/ddp"


def _engine_factory() -> tuple[sa.Engine, object]:
    engine = create_db_engine(
        "sqlite+pysqlite:///:memory:",
        pool_size=1,
        max_overflow=0,
        pool_timeout_seconds=5,
        connect_timeout_seconds=5,
    )
    Base.metadata.create_all(engine)
    return engine, create_session_factory(engine)


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "backup_dir": str(tmp_path),
        "backup_retention_days": 7,
        "outbox_retention_days": 30,
        "maintenance_poll_interval_seconds": 0.01,
        "maintenance_backup_interval_seconds": 60,
        "maintenance_enabled": True,
        "database_url": DB,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def _worker(tmp_path: Path, factory, **overrides: object) -> MaintenanceWorker:
    return MaintenanceWorker(factory, _settings(tmp_path, **overrides))


# ---------- 备份 ----------


def test_backup_once_writes_artifact_and_reports_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """pg_dump 成功：产物落盘、size 正确、返回值完整。"""
    payload = b"-- DDP dump\n" + b"x" * 2048

    def _fake_run(command, **kwargs):  # noqa: ANN001, ANN003
        target = Path(command[command.index("--file") + 1])
        target.write_bytes(payload)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", _fake_run)
    engine, factory = _engine_factory()
    worker = _worker(tmp_path, factory)

    result = worker.backup_once(now=NOW)

    assert result.path.read_bytes() == payload
    assert result.size_bytes == len(payload)
    assert result.removed == ()
    assert result.path.name == "ddp-20261127T120000Z.sql"
    engine.dispose()


def test_backup_once_removes_partial_file_when_pg_dump_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """pg_dump 返回非 0：删掉半成品并抛 RuntimeError（不许留下空文件冒充成功）。"""

    def _fake_run(command, **kwargs):  # noqa: ANN001, ANN003
        target = Path(command[command.index("--file") + 1])
        target.write_bytes(b"partial")
        return SimpleNamespace(returncode=1, stdout="", stderr="boom")

    monkeypatch.setattr(subprocess, "run", _fake_run)
    engine, factory = _engine_factory()
    worker = _worker(tmp_path, factory)

    with pytest.raises(RuntimeError, match="pg_dump 返回 1"):
        worker.backup_once(now=NOW)

    assert list(tmp_path.glob("ddp-*.sql")) == []
    engine.dispose()


def test_backup_once_rejects_undersized_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """产物 <1024 字节视为异常（备份被截断的风险）：删除并抛错。"""

    def _fake_run(command, **kwargs):  # noqa: ANN001, ANN003
        target = Path(command[command.index("--file") + 1])
        target.write_bytes(b"too small")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", _fake_run)
    engine, factory = _engine_factory()
    worker = _worker(tmp_path, factory)

    with pytest.raises(RuntimeError, match="产物异常小"):
        worker.backup_once(now=NOW)

    assert list(tmp_path.glob("ddp-*.sql")) == []
    engine.dispose()


def test_backup_once_raises_when_subprocess_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """子进程启动失败（如没装 pg_dump）：抛 RuntimeError，不静默跳过。"""

    def _boom(command, **kwargs):  # noqa: ANN001, ANN003
        raise OSError("pg_dump: command not found")

    monkeypatch.setattr(subprocess, "run", _boom)
    engine, factory = _engine_factory()
    worker = _worker(tmp_path, factory)

    with pytest.raises(RuntimeError, match="pg_dump 执行失败"):
        worker.backup_once(now=NOW)
    assert list(tmp_path.glob("ddp-*.sql")) == []
    engine.dispose()


def test_prune_backups_keeps_recent_removes_expired_and_leaves_unknown_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """保留期裁剪：过期删除、未过期保留、无法解析命名的人工文件不动。"""

    def _fake_run(command, **kwargs):  # noqa: ANN001, ANN003
        target = Path(command[command.index("--file") + 1])
        target.write_bytes(b"x" * 2048)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", _fake_run)
    engine, factory = _engine_factory()
    worker = _worker(tmp_path, factory)

    # 制造三个历史备份：一个刚过期、一个远早于保留期、一个无法解析命名
    (tmp_path / "ddp-20261101T000000Z.sql").write_bytes(b"old")
    (tmp_path / "ddp-20261120T000000Z.sql").write_bytes(b"older")
    (tmp_path / "ddp-人工-备份.sql").write_bytes(b"unknown")

    result = worker.backup_once(now=NOW)

    assert result.removed == (
        tmp_path / "ddp-20261101T000000Z.sql",
        tmp_path / "ddp-20261120T000000Z.sql",
    )
    # 无法解析的命名保持不动（可能是人工放进去的）
    assert (tmp_path / "ddp-人工-备份.sql").exists()
    # 刚产生的备份不在裁剪列表里
    assert result.path in {p for p in tmp_path.glob("ddp-*.sql")}
    engine.dispose()


# ---------- 连接参数 ----------


def test_connection_args_and_password_parse_url(tmp_path: Path) -> None:
    engine, factory = _engine_factory()
    worker = _worker(tmp_path, factory)

    args = worker._connection_args()
    assert "--host" in args and args[args.index("--host") + 1] == "localhost"
    assert "--port" in args and args[args.index("--port") + 1] == "55432"
    assert "--username" in args and args[args.index("--username") + 1] == "ddp"
    assert "--dbname" in args and args[args.index("--dbname") + 1] == "ddp"
    assert worker._password() == "secretpw"
    engine.dispose()


def test_connection_args_handles_url_without_password(tmp_path: Path) -> None:
    engine, factory = _engine_factory()
    worker = _worker(tmp_path, factory, database_url="postgresql+psycopg://ddp@localhost:55432/ddp")

    assert worker._password() is None
    args = worker._connection_args()
    assert "--host" in args
    engine.dispose()


# ---------- 归档与清扫 ----------


def _seed_identity(factory, *, project_id: object, user_id: object) -> None:
    """outbox/幂等表有 project/user 外键，父行必须真实存在。"""
    with factory() as session:
        session.add(Project(id=project_id, code=f"perf-{project_id.hex[:8]}", name="测试项目"))
        session.add(User(id=user_id, display_name="测试用户"))
        session.commit()


def _seed_outbox_and_idempotency(factory, *, project_id: object, user_id: object) -> None:
    """造四类事件 + 三条幂等记录，覆盖保留/过期边界。"""
    _seed_identity(factory, project_id=project_id, user_id=user_id)
    with factory() as session:
        # 约束 ck_outbox_events_dispatch_status_consistent：
        # dispatch=false 时 status 必须为 RECORDED；dispatch=true 时不能是 RECORDED
        for status, created_at, dispatch in [
            ("PUBLISHED", NOW - timedelta(days=60), True),  # 超期终态 → 删
            ("RECORDED", NOW - timedelta(days=45), False),  # 超期终态 → 删
            ("PENDING", NOW - timedelta(days=90), True),  # 超期但未投递 → 必须保留
            ("FAILED", NOW - timedelta(days=90), True),  # 超期死信 → 必须保留
            ("PUBLISHED", NOW - timedelta(days=1), True),  # 未超期 → 保留
        ]:
            session.add(
                OutboxModel(
                    id=uuid4(),
                    project_id=project_id,
                    aggregate_type="TEST",
                    aggregate_id=uuid4(),
                    event_type="t.test",
                    status=status,
                    dispatch=dispatch,
                    attempts=0,
                    created_at=created_at,
                )
            )
        for expires_at in (
            NOW - timedelta(days=2),  # 过期 → 删
            NOW + timedelta(days=1),  # 未过期 → 保留
            NOW,  # 边界：等于 now → 保留（条件是 <）
        ):
            session.add(
                IdempotencyRecord(
                    project_id=project_id,
                    user_id=user_id,
                    key=uuid4().hex,
                    method="POST",
                    path="/api/v1/test",
                    request_hash=uuid4().hex,
                    response_status=200,
                    created_at=NOW - timedelta(days=10),
                    expires_at=expires_at,
                )
            )
        session.commit()


def test_sweep_once_archives_terminal_events_and_keeps_pending_and_failed(tmp_path: Path) -> None:
    """核心不变量：只删终态超期事件，PENDING/FAILED 与未超期的一律保留。"""
    engine, factory = _engine_factory()
    worker = _worker(tmp_path, factory)
    project_id, user_id = uuid4(), uuid4()
    _seed_outbox_and_idempotency(factory, project_id=project_id, user_id=user_id)

    result = worker.sweep_once(now=NOW)

    assert result.archived_events == 2  # PUBLISHED(60d) + RECORDED(45d)
    assert result.purged_idempotency == 1  # 只有 expires_at 早于 NOW 的那条

    with factory() as session:
        statuses = {row.status for row in session.scalars(select(OutboxModel)).all()}
        assert statuses == {"PENDING", "FAILED", "PUBLISHED"}  # 后两者是未超期的
        assert len(session.scalars(select(IdempotencyRecord)).all()) == 2
    engine.dispose()


def test_sweep_once_is_idempotent(tmp_path: Path) -> None:
    """重复清扫：第二次不重复删除（幂等）。"""
    engine, factory = _engine_factory()
    worker = _worker(tmp_path, factory)
    project_id, user_id = uuid4(), uuid4()
    _seed_outbox_and_idempotency(factory, project_id=project_id, user_id=user_id)

    first = worker.sweep_once(now=NOW)
    # 用**同一时刻**再扫一次——时间不前进才能证明"幂等"而非"时间推移"
    second = worker.sweep_once(now=NOW)

    assert (first.archived_events, first.purged_idempotency) == (2, 1)
    assert (second.archived_events, second.purged_idempotency) == (0, 0)
    engine.dispose()


def test_sweep_once_without_seed_returns_zero(tmp_path: Path) -> None:
    """空库清扫：返回 0 而不是报错或异常。"""
    engine, factory = _engine_factory()
    worker = _worker(tmp_path, factory)

    result = worker.sweep_once(now=NOW)
    assert (result.archived_events, result.purged_idempotency) == (0, 0)
    engine.dispose()


# ---------- 积压指标 ----------


def test_pending_backlog_counts_pending_and_dead_letters(tmp_path: Path) -> None:
    """积压指标口径：PENDING 且 dispatch 才算积压；FAILED 一律算死信。"""
    engine, factory = _engine_factory()
    worker = _worker(tmp_path, factory)
    project_id = uuid4()
    _seed_identity(factory, project_id=project_id, user_id=uuid4())

    with factory() as session:
        session.add_all(
            [
                OutboxModel(
                    id=uuid4(),
                    project_id=project_id,
                    aggregate_type="T",
                    aggregate_id=uuid4(),
                    event_type="t",
                    status="PENDING",
                    dispatch=True,
                    attempts=0,
                    created_at=NOW,
                ),
                OutboxModel(
                    id=uuid4(),
                    project_id=project_id,
                    aggregate_type="T",
                    aggregate_id=uuid4(),
                    event_type="t",
                    status="RECORDED",
                    dispatch=False,
                    attempts=0,
                    created_at=NOW,
                    payload="{}",
                ),
                OutboxModel(
                    id=uuid4(),
                    project_id=project_id,
                    aggregate_type="T",
                    aggregate_id=uuid4(),
                    event_type="t",
                    status="FAILED",
                    dispatch=True,
                    attempts=1,
                    created_at=NOW,
                    payload="{}",
                ),
                OutboxModel(
                    id=uuid4(),
                    project_id=project_id,
                    aggregate_type="T",
                    aggregate_id=uuid4(),
                    event_type="t",
                    status="PUBLISHED",
                    dispatch=True,
                    attempts=0,
                    created_at=NOW,
                    payload="{}",
                ),
            ]
        )
        session.commit()

    assert worker.pending_backlog() == {"pending": 1, "dead_letter": 1}
    engine.dispose()


# ---------- 主循环 ----------


def test_run_executes_backup_then_stops_on_stop_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """主循环：先执行一次备份，stop_event 置位后退出（不挂死）。"""
    import threading

    calls: list[list[str]] = []

    def _fake_run(command, **kwargs):  # noqa: ANN001, ANN003
        calls.append(list(command))
        target = Path(command[command.index("--file") + 1])
        target.write_bytes(b"x" * 2048)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", _fake_run)
    engine, factory = _engine_factory()
    worker = _worker(
        tmp_path,
        factory,
        maintenance_backup_interval_seconds=60,
        maintenance_poll_interval_seconds=0.01,
    )

    stop_event = threading.Event()
    run_thread = threading.Thread(target=worker.run, kwargs={"stop_event": stop_event}, daemon=True)
    run_thread.start()

    # 给循环留出跑一轮的时间
    threading.Event().wait(0.15)
    stop_event.set()
    run_thread.join(timeout=5)

    assert not run_thread.is_alive(), "stop_event 置位后主循环未退出"
    assert calls, "主循环未执行备份"
    engine.dispose()
