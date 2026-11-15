"""数据集审计、outbox 与幂等记录 SQLAlchemy 仓储。"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.application.datasets.ports import AuditRecord, OutboxEvent
from app.domain.identity.models import ProjectScope
from app.infrastructure.db.models.collection import AuditLog
from app.infrastructure.db.models.collection import OutboxEvent as OutboxModel
from app.infrastructure.repositories.idempotency import (
    SqlIdempotencyRepository as SqlSharedIdempotencyRepository,
)


class SqlDatasetAuditRepository:
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


class SqlDatasetOutboxRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def enqueue(self, scope: ProjectScope, event: OutboxEvent) -> None:
        scope.ensure(event.project_id)
        self._session.add(
            OutboxModel(
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


# 幂等仓储的实体实现统一在 app.infrastructure.repositories.idempotency；
# 这里保留旧类名做别名，避免改动既有依赖注入代码。
SqlDatasetIdempotencyRepository = SqlSharedIdempotencyRepository
