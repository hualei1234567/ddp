"""R-13 / T-1、T-2：outbox 投递类与留痕类的语义分离。

锁定的行为：

1. 留痕类事件写入即 ``RECORDED``，不进重试队列；
2. 投递类事件写入 ``PENDING``，可被 worker 抢占；
3. 抢占端**只**捞投递类——即便有人把留痕类误写成 ``PENDING``，
   也会被 ``dispatch`` 条件挡住；
4. 数据库层不允许"投递消息被标成已记录"这种自相矛盾的行。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from app.application.audit import record_change
from app.domain.identity.models import ProjectScope
from app.infrastructure.db.models.collection import OutboxEvent as OutboxModel
from app.infrastructure.repositories.collection_audit import (
    SqlAuditRepository,
    SqlOutboxRepository,
)
from app.infrastructure.repositories.outbox_validation import SqlOutboxValidationQueue
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

NOW = datetime(2026, 11, 15, tzinfo=UTC)


def _record(
    factory: sessionmaker[Session],
    scope: ProjectScope,
    *,
    event_type: str,
    dispatch: bool,
) -> None:
    with factory() as session:
        record_change(
            SqlAuditRepository(session),
            SqlOutboxRepository(session),
            scope,
            resource_type="DEVICE",
            resource_id=uuid4(),
            action="create",
            from_status=None,
            to_status="ACTIVE",
            reason=None,
            request_id=None,
            details={},
            event_type=event_type,
            event_payload={"k": "v"},
            occurred_at=NOW,
            dispatch=dispatch,
        )
        session.commit()


def _rows(factory: sessionmaker[Session]) -> list[OutboxModel]:
    with factory() as session:
        return list(session.scalars(select(OutboxModel)))


def test_audit_event_is_recorded_and_not_dispatchable(
    api_db: tuple[sessionmaker[Session], object],
) -> None:
    factory, ids = api_db
    scope = ProjectScope(ids.project_a, actor_id=ids.owner_a)

    _record(factory, scope, event_type="device.created", dispatch=False)

    rows = _rows(factory)
    assert len(rows) == 1
    assert rows[0].dispatch is False
    assert rows[0].status == "RECORDED"


def test_dispatch_event_is_pending(
    api_db: tuple[sessionmaker[Session], object],
) -> None:
    factory, ids = api_db
    scope = ProjectScope(ids.project_a, actor_id=ids.owner_a)

    _record(factory, scope, event_type="ingestion.raw_asset.validation_requested", dispatch=True)

    rows = _rows(factory)
    assert rows[0].dispatch is True
    assert rows[0].status == "PENDING"


def test_audit_event_cannot_be_forced_into_pending(
    api_db: tuple[sessionmaker[Session], object],
) -> None:
    """想绕过应用层把留痕类改成 PENDING 会被数据库挡下。

    这比"worker 白名单过滤"更强：脏数据根本进不了表，不依赖 worker 记得过滤。
    """
    from sqlalchemy.exc import IntegrityError

    factory, ids = api_db
    scope = ProjectScope(ids.project_a, actor_id=ids.owner_a)

    _record(factory, scope, event_type="device.created", dispatch=False)
    with factory() as session:
        row = session.scalars(select(OutboxModel)).one()
        row.status = "PENDING"
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()


def test_claim_picks_pending_dispatch_event(
    api_db: tuple[sessionmaker[Session], object],
) -> None:
    factory, ids = api_db
    scope = ProjectScope(ids.project_a, actor_id=ids.owner_a)

    _record(factory, scope, event_type="ingestion.raw_asset.validation_requested", dispatch=True)

    with factory() as session:
        claimed = SqlOutboxValidationQueue(session).claim_next(
            now=NOW, event_type="ingestion.raw_asset.validation_requested"
        )
    assert claimed is not None


def test_database_rejects_dispatch_marked_recorded(
    api_db: tuple[sessionmaker[Session], object],
) -> None:
    """数据库约束兜底：投递类不能是 RECORDED，留痕类不能是 PENDING。"""
    from sqlalchemy.exc import IntegrityError

    factory, ids = api_db
    scope = ProjectScope(ids.project_a, actor_id=ids.owner_a)
    _record(factory, scope, event_type="dataset.export.requested", dispatch=True)

    with factory() as session:
        row = session.scalars(select(OutboxModel)).one()
        row.status = "RECORDED"
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()
