"""标注审计、outbox 与幂等记录 SQLAlchemy 仓储。"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.application.annotation.overdue_alerts import OVERDUE_ALERT_EVENT
from app.application.annotation.ports import (
    AuditRecord,
    OutboxEvent,
)
from app.domain.identity.models import ProjectScope
from app.infrastructure.db.models.collection import AuditLog
from app.infrastructure.db.models.collection import OutboxEvent as OutboxEventModel
from app.infrastructure.repositories.idempotency import (
    SqlIdempotencyRepository as SqlSharedIdempotencyRepository,
)


class SqlAnnotationAuditRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def record(self, scope: ProjectScope, record: AuditRecord) -> None:
        scope.ensure(record.project_id)
        self._session.add(
            AuditLog(
                id=record.audit_id,
                project_id=record.project_id,
                actor_user_id=record.actor_user_id,
                actor_role=record.actor_role,
                resource_type=record.resource_type,
                resource_id=record.resource_id,
                action=record.action,
                from_status=record.from_status,
                to_status=record.to_status,
                reason=record.reason,
                request_id=record.request_id,
                result=record.result,
                correlation_id=None,
                details=dict(record.details),
                occurred_at=record.occurred_at,
            )
        )
        self._session.flush()


class SqlAnnotationOutboxRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def enqueue(self, scope: ProjectScope, event: OutboxEvent) -> None:
        scope.ensure(event.project_id)
        self._session.add(
            OutboxEventModel(
                id=event.event_id,
                project_id=event.project_id,
                aggregate_type=event.aggregate_type,
                aggregate_id=event.aggregate_id,
                event_type=event.event_type,
                payload=dict(event.payload),
                # 投递类进 PENDING 等待消费者；留痕类写入即终态 RECORDED。
                status="PENDING" if event.dispatch else "RECORDED",
                dispatch=event.dispatch,
                attempts=0,
                available_at=event.occurred_at,
                created_at=event.occurred_at,
            )
        )
        self._session.flush()

    def recorded_overdue_windows(
        self,
        scope: ProjectScope,
        task_ids: list[UUID],
    ) -> set[str]:
        """返回给定任务已落库的逾期提醒窗口键（严格按项目隔离）。

        ``payload`` 是 JSON 列，窗口键由应用层写入 ``overdue_window``；
        这里只读取、不猜测结构，非字符串值一律忽略。
        """
        if not task_ids:
            return set()
        payloads = self._session.scalars(
            select(OutboxEventModel.payload).where(
                OutboxEventModel.project_id == scope.project_id,
                OutboxEventModel.event_type == OVERDUE_ALERT_EVENT,
                OutboxEventModel.aggregate_id.in_(task_ids),
            )
        )
        windows: set[str] = set()
        for payload in payloads:
            if not isinstance(payload, dict):
                continue
            window = payload.get("overdue_window")
            if isinstance(window, str):
                windows.add(window)
        return windows


# 幂等仓储的实体实现统一在 app.infrastructure.repositories.idempotency；
# 这里保留旧类名做别名，避免改动既有依赖注入代码。
SqlAnnotationIdempotencyRepository = SqlSharedIdempotencyRepository
