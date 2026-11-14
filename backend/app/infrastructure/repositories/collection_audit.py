"""审计、outbox 与幂等记录 SQLAlchemy 仓储。"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.application.collection.ports import (
    AuditRecord,
    OutboxEvent,
)
from app.domain.identity.models import ProjectScope
from app.infrastructure.db.models.collection import (
    AuditLog,
)
from app.infrastructure.db.models.collection import OutboxEvent as OutboxEventModel
from app.infrastructure.repositories.idempotency import (
    SqlIdempotencyRepository as SqlSharedIdempotencyRepository,
)


class SqlAuditRepository:
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
        # 审计写入失败必须让业务事务回滚（fail-closed）。
        self._session.flush()


class SqlOutboxRepository:
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
                status="PENDING",
                attempts=0,
                available_at=event.occurred_at,
                created_at=event.occurred_at,
            )
        )
        self._session.flush()


# 幂等仓储的实体实现统一在 app.infrastructure.repositories.idempotency；
# 这里保留旧类名做别名，避免改动既有依赖注入代码。
SqlIdempotencyRepository = SqlSharedIdempotencyRepository
