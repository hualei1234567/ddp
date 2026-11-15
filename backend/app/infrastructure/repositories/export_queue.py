"""ExportJob outbox 事件消费仓储。"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.application.datasets.export_ports import ClaimedExportEvent
from app.domain.datasets.errors import ConflictError
from app.infrastructure.db.models.collection import OutboxEvent as OutboxEventModel
from app.infrastructure.repositories._collection_sql import as_utc

_PENDING = "PENDING"
_PUBLISHED = "PUBLISHED"
_FAILED = "FAILED"


class SqlExportQueue:
    def __init__(self, session: Session) -> None:
        self._session = session

    def claim_next(
        self,
        *,
        now: datetime,
        event_type: str,
    ) -> ClaimedExportEvent | None:
        statement = (
            select(OutboxEventModel)
            .where(
                # 只捞投递类：留痕类是 RECORDED 终态，永远不会到这里；
                # 显式再判 dispatch 是为了防止将来有人把留痕类写成 PENDING。
                OutboxEventModel.dispatch.is_(True),
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
        if self._session.get_bind().dialect.name == "postgresql":
            statement = statement.with_for_update(skip_locked=True)
        row = self._session.scalar(statement)
        if row is None:
            return None
        return ClaimedExportEvent(
            event_id=row.id,
            project_id=row.project_id,
            aggregate_id=row.aggregate_id,
            payload=dict(row.payload or {}),
            attempts=row.attempts,
            created_at=as_utc(row.created_at) or now,
        )

    def mark_published(self, event_id: UUID, *, now: datetime) -> None:
        result = self._session.execute(
            update(OutboxEventModel)
            .where(
                OutboxEventModel.id == event_id,
                OutboxEventModel.status == _PENDING,
            )
            .values(status=_PUBLISHED, published_at=now, last_error=None)
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
            .values(attempts=attempts, available_at=available_at, last_error=last_error)
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
            .values(status=_FAILED, attempts=attempts, last_error=last_error)
        )
        self._session.flush()
        return result.rowcount == 1
