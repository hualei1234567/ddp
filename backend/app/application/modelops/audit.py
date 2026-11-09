"""阶段 5 审计与 outbox 记录。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from app.application.modelops.ports import (
    AuditRepository,
    OutboxRepository,
    UnitOfWork,
)
from app.domain.identity.models import ProjectScope


@dataclass(frozen=True, slots=True)
class AuditRecordData:
    audit_id: UUID
    project_id: UUID
    actor_user_id: UUID | None
    actor_role: str | None
    resource_type: str
    resource_id: UUID
    action: str
    from_status: str | None
    to_status: str | None
    reason: str | None
    request_id: str | None
    result: str
    details: dict[str, object]
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class OutboxEventData:
    event_id: UUID
    project_id: UUID
    aggregate_type: str
    aggregate_id: UUID
    event_type: str
    payload: dict[str, object]
    occurred_at: datetime


def record_change(
    audit: AuditRepository,
    outbox: OutboxRepository,
    scope: ProjectScope,
    *,
    resource_type: str,
    resource_id: UUID,
    action: str,
    from_status: str | None,
    to_status: str | None,
    reason: str | None,
    request_id: str | None,
    details: dict[str, object],
    event_type: str,
    event_payload: dict[str, object],
    occurred_at: datetime,
) -> None:
    audit.record(
        scope,
        AuditRecordData(
            audit_id=uuid4(),
            project_id=scope.project_id,
            actor_user_id=scope.actor_id,
            actor_role=getattr(scope, "actor_role", None),
            resource_type=resource_type,
            resource_id=resource_id,
            action=action,
            from_status=from_status,
            to_status=to_status,
            reason=reason,
            request_id=request_id,
            result="SUCCESS",
            details=dict(details),
            occurred_at=occurred_at,
        ),
    )
    outbox.enqueue(
        scope,
        OutboxEventData(
            event_id=uuid4(),
            project_id=scope.project_id,
            aggregate_type=resource_type,
            aggregate_id=resource_id,
            event_type=event_type,
            payload=dict(event_payload),
            occurred_at=occurred_at,
        ),
    )


__all__ = ["AuditRecordData", "OutboxEventData", "record_change", "UnitOfWork"]
