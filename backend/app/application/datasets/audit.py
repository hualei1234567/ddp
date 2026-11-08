"""数据集审计与事务性 outbox 编排。"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from uuid import UUID, uuid4

from app.application.datasets.ports import (
    AuditRecord,
    AuditRepository,
    OutboxEvent,
    OutboxRepository,
)
from app.domain.identity.models import ProjectScope


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
    details: Mapping[str, object],
    event_type: str,
    event_payload: Mapping[str, object],
    occurred_at: datetime,
    result: str = "SUCCESS",
) -> None:
    audit.record(
        scope,
        AuditRecord(
            audit_id=uuid4(),
            project_id=scope.project_id,
            actor_user_id=scope.actor_id,
            actor_role=None,
            resource_type=resource_type,
            resource_id=resource_id,
            action=action,
            from_status=from_status,
            to_status=to_status,
            reason=reason,
            request_id=request_id,
            result=result,
            details=details,
            occurred_at=occurred_at,
        ),
    )
    outbox.enqueue(
        scope,
        OutboxEvent(
            event_id=uuid4(),
            project_id=scope.project_id,
            aggregate_type=resource_type,
            aggregate_id=resource_id,
            event_type=event_type,
            payload=event_payload,
            occurred_at=occurred_at,
        ),
    )
