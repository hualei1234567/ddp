"""RawAsset 校验 outbox 事件 SQLAlchemy 消费仓储。

PostgreSQL 使用 ``SELECT ... FOR UPDATE SKIP LOCKED`` 让多个 Worker 各自抢占
不同事件；SQLite 无行级锁，依赖单写事务串行化（仅测试/本地使用）。
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.application.ingestion.ports import ClaimedValidationEvent
from app.domain.ingestion.errors import ConflictError
from app.infrastructure.db.models.collection import OutboxEvent as OutboxEventModel
from app.infrastructure.repositories._collection_sql import as_utc

_PENDING = "PENDING"
_PUBLISHED = "PUBLISHED"
_FAILED = "FAILED"


class SqlOutboxValidationQueue:
    def __init__(self, session: Session) -> None:
        self._session = session

    def claim_next(
        self,
        *,
        now: datetime,
        event_type: str,
    ) -> ClaimedValidationEvent | None:
        statement = (
            select(OutboxEventModel)
            .where(
                OutboxEventModel.status == _PENDING,
                OutboxEventModel.event_type == event_type,
                OutboxEventModel.available_at <= now,
            )
            .order_by(
                OutboxEventModel.available_at,
                OutboxEventModel.created_at,
                OutboxEventModel.id,
            )
            .limit(1)
        )
        if self._dialect() == "postgresql":
            statement = statement.with_for_update(skip_locked=True)
        row = self._session.scalar(statement)
        if row is None:
            return None
        created_at = as_utc(row.created_at) or now
        return ClaimedValidationEvent(
            event_id=row.id,
            project_id=row.project_id,
            aggregate_id=row.aggregate_id,
            payload=dict(row.payload or {}),
            attempts=row.attempts,
            created_at=created_at,
        )

    def mark_published(self, event_id: UUID, *, now: datetime) -> None:
        result = self._session.execute(
            update(OutboxEventModel)
            .where(
                OutboxEventModel.id == event_id,
                OutboxEventModel.status == _PENDING,
            )
            .values(
                status=_PUBLISHED,
                published_at=now,
                last_error=None,
            )
        )
        if result.rowcount != 1:
            raise ConflictError("outbox 事件状态已变化，拒绝重复发布")
        self._session.flush()

    def mark_retry(
        self,
        event_id: UUID,
        *,
        attempts: int,
        available_at: datetime,
        last_error: str,
    ) -> bool:
        result = self._session.execute(
            update(OutboxEventModel)
            .where(
                OutboxEventModel.id == event_id,
                OutboxEventModel.status == _PENDING,
            )
            .values(
                attempts=attempts,
                available_at=available_at,
                last_error=last_error,
            )
        )
        self._session.flush()
        return result.rowcount == 1

    def mark_dead_letter(
        self,
        event_id: UUID,
        *,
        attempts: int,
        last_error: str,
    ) -> bool:
        result = self._session.execute(
            update(OutboxEventModel)
            .where(
                OutboxEventModel.id == event_id,
                OutboxEventModel.status == _PENDING,
            )
            .values(
                status=_FAILED,
                attempts=attempts,
                last_error=last_error,
            )
        )
        self._session.flush()
        return result.rowcount == 1

    def _dialect(self) -> str:
        return self._session.get_bind().dialect.name
