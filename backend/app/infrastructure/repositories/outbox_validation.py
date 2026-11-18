"""RawAsset 校验 outbox 事件 SQLAlchemy 消费仓储。

PostgreSQL 使用 ``SELECT ... FOR UPDATE SKIP LOCKED`` 让多个 Worker 各自抢占
不同事件；SQLite 无行级锁，依赖单写事务串行化（仅测试/本地使用）。
"""

from __future__ import annotations

from datetime import datetime, timedelta
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

    def mark_claimed(
        self,
        event_id: UUID,
        *,
        now: datetime,
        lease_seconds: float,
    ) -> bool:
        """把事件的可见时间推后一个租约，防止处理期间被第二个 Worker 再领取。

        ``claim_next`` 的行锁只在当前事务内有效；处理流程中途提交（例如每条
        阶段步骤各自提交）会释放锁，行仍停在 PENDING，另一个 Worker 能再次
        捞到同一事件。这里用现有 ``available_at`` 做租约，不新增列与迁移：
        提交后事件在租约内不可见；处理完成由 mark_published/mark_retry 覆盖；
        Worker 崩溃则租约到期后自动重新可见。

        返回 ``False`` 表示事件已不在 PENDING（被并发方处理掉了）。
        """
        result = self._session.execute(
            update(OutboxEventModel)
            .where(
                OutboxEventModel.id == event_id,
                OutboxEventModel.status == _PENDING,
            )
            .values(available_at=now + timedelta(seconds=lease_seconds))
        )
        return bool(result.rowcount)

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
