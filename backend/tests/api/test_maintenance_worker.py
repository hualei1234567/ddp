"""阶段 8 维护 worker：备份、归档与清扫。

备份路径用**真实的 pg_dump 子进程**验证——这是唯一能证明"备份真的能生成
可导入文件"的方式；用 mock 只会证明"我们调了一个函数"。
需要本机有 pg_dump 与真实 PostgreSQL；缺失时跳过并标注原因。
"""

from __future__ import annotations

import os
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from app.core.config import Settings
from app.infrastructure.db.models.collection import (
    IdempotencyRecord,
)
from app.infrastructure.db.models.collection import (
    OutboxEvent as OutboxModel,
)
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from tests.api.conftest import ApiIds

PG_URL = os.environ.get("DDP_TEST_POSTGRES_URL", "")

pytestmark = pytest.mark.skipif(
    not PG_URL or shutil.which("pg_dump") is None,
    reason="需要 DDP_TEST_POSTGRES_URL 与 pg_dump（BLOCKED）",
)

NOW = datetime(2026, 11, 15, 12, 0, 0, tzinfo=UTC)


def _settings(backup_dir: Path, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "database_url": PG_URL,
        "maintenance_enabled": True,
        "backup_dir": str(backup_dir),
        "outbox_retention_days": 30,
        "backup_retention_days": 7,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


@pytest.fixture()
def backup_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "backups"
    directory.mkdir()
    return directory


def test_backup_produces_importable_dump(
    api_db: tuple[sessionmaker[Session], ApiIds],
    backup_dir: Path,
) -> None:
    """真实 pg_dump：产物必须非空且含建表语句，不是空文件。"""
    from app.workers.maintenance import MaintenanceWorker

    factory, _ = api_db
    worker = MaintenanceWorker(factory, _settings(backup_dir))

    result = worker.backup_once(now=NOW)

    assert result.path.exists()
    assert result.size_bytes > 1024
    content = result.path.read_text(encoding="utf-8", errors="ignore")
    assert "CREATE TABLE" in content


def test_backup_failure_does_not_leave_empty_file(
    api_db: tuple[sessionmaker[Session], ApiIds],
    backup_dir: Path,
) -> None:
    """连不上数据库时必须抛错且不留空文件——空文件冒充成功是灾难。"""
    from app.workers.maintenance import MaintenanceWorker

    factory, _ = api_db
    settings = _settings(backup_dir, database_url="postgresql+psycopg://nouser@127.0.0.1:1/none")
    worker = MaintenanceWorker(factory, settings, database_url=settings.database_url)

    with pytest.raises(RuntimeError):
        worker.backup_once(now=NOW)
    assert list(backup_dir.glob("ddp-*.sql")) == []


def test_sweep_archives_only_terminal_events(
    api_db: tuple[sessionmaker[Session], ApiIds],
    backup_dir: Path,
) -> None:
    """终态事件按保留期删除；PENDING 与 FAILED 必须保留。"""
    from app.workers.maintenance import MaintenanceWorker

    factory, ids = api_db
    old = NOW - timedelta(days=90)
    with factory() as session:
        session.add_all(
            [
                OutboxModel(
                    id=uuid4(),
                    project_id=ids.project_a,
                    aggregate_type="DEVICE",
                    aggregate_id=uuid4(),
                    event_type="device.created",
                    payload={},
                    status="RECORDED",
                    dispatch=False,
                    attempts=0,
                    available_at=old,
                    created_at=old,
                ),
                OutboxModel(
                    id=uuid4(),
                    project_id=ids.project_a,
                    aggregate_type="RAW_ASSET",
                    aggregate_id=uuid4(),
                    event_type="ingestion.raw_asset.validation_requested",
                    payload={},
                    status="PENDING",
                    dispatch=True,
                    attempts=0,
                    available_at=old,
                    created_at=old,
                ),
                OutboxModel(
                    id=uuid4(),
                    project_id=ids.project_a,
                    aggregate_type="RAW_ASSET",
                    aggregate_id=uuid4(),
                    event_type="ingestion.raw_asset.validation_requested",
                    payload={},
                    status="FAILED",
                    dispatch=True,
                    attempts=5,
                    available_at=old,
                    created_at=old,
                ),
            ]
        )
        session.commit()

    worker = MaintenanceWorker(factory, _settings(backup_dir))
    result = worker.sweep_once(now=NOW)

    assert result.archived_events == 1
    with factory() as session:
        remaining = {row.status for row in session.scalars(select(OutboxModel)).all()}
    assert remaining == {"PENDING", "FAILED"}


def test_sweep_purges_expired_idempotency_keys(
    api_db: tuple[sessionmaker[Session], ApiIds],
    backup_dir: Path,
) -> None:
    from app.workers.maintenance import MaintenanceWorker

    factory, ids = api_db
    with factory() as session:
        session.add_all(
            [
                IdempotencyRecord(
                    project_id=ids.project_a,
                    user_id=ids.editor_a,
                    key="expired",
                    method="POST",
                    path="/api/v1/projects/x/devices",
                    request_hash="sha256:" + "a" * 64,
                    response_status=201,
                    response_ref=None,
                    created_at=NOW - timedelta(days=2),
                    expires_at=NOW - timedelta(days=1),
                ),
                IdempotencyRecord(
                    project_id=ids.project_a,
                    user_id=ids.editor_a,
                    key="alive",
                    method="POST",
                    path="/api/v1/projects/x/devices",
                    request_hash="sha256:" + "b" * 64,
                    response_status=201,
                    response_ref=None,
                    created_at=NOW,
                    expires_at=NOW + timedelta(days=1),
                ),
            ]
        )
        session.commit()

    worker = MaintenanceWorker(factory, _settings(backup_dir))
    result = worker.sweep_once(now=NOW)

    assert result.purged_idempotency == 1
    with factory() as session:
        keys = {row.key for row in session.scalars(select(IdempotencyRecord)).all()}
    assert keys == {"alive"}


def test_backup_retention_prunes_only_matching_names(
    api_db: tuple[sessionmaker[Session], ApiIds],
    backup_dir: Path,
) -> None:
    """保留期只清理自己命名的文件；人工放进去的备份不动。"""
    from app.workers.maintenance import MaintenanceWorker

    factory, _ = api_db
    stale = backup_dir / "ddp-20260101T000000Z.sql"
    stale.write_text("old", encoding="utf-8")
    manual = backup_dir / "manual-before-migration.sql"
    manual.write_text("keep me", encoding="utf-8")

    worker = MaintenanceWorker(factory, _settings(backup_dir))
    result = worker.backup_once(now=NOW)

    assert stale in result.removed
    assert not stale.exists()
    assert manual.exists()


def test_pending_backlog_counts_dispatch_and_dead_letters(
    api_db: tuple[sessionmaker[Session], ApiIds],
    backup_dir: Path,
) -> None:
    from app.workers.maintenance import MaintenanceWorker

    factory, ids = api_db
    with factory() as session:
        session.add_all(
            [
                OutboxModel(
                    id=uuid4(),
                    project_id=ids.project_a,
                    aggregate_type="RAW_ASSET",
                    aggregate_id=uuid4(),
                    event_type="ingestion.raw_asset.validation_requested",
                    payload={},
                    status="PENDING",
                    dispatch=True,
                    attempts=0,
                    available_at=NOW,
                    created_at=NOW,
                ),
                OutboxModel(
                    id=uuid4(),
                    project_id=ids.project_a,
                    aggregate_type="RAW_ASSET",
                    aggregate_id=uuid4(),
                    event_type="ingestion.raw_asset.validation_requested",
                    payload={},
                    status="FAILED",
                    dispatch=True,
                    attempts=5,
                    available_at=NOW,
                    created_at=NOW,
                ),
            ]
        )
        session.commit()

    worker = MaintenanceWorker(factory, _settings(backup_dir))
    assert worker.pending_backlog() == {"pending": 1, "dead_letter": 1}
