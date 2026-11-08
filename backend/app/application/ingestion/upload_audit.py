"""上传域共享审计、outbox 与引用解析。"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.application.collection.ports import (
    AuditRecord,
    AuditRepository,
    OutboxEvent,
    OutboxRepository,
)
from app.domain.identity.models import ProjectScope
from app.domain.ingestion.errors import ResourceNotFoundError

RESOURCE_TYPE = "UPLOAD_SESSION"


def utcnow() -> datetime:
    return datetime.now(UTC)


def uuid_ref(value: str | None) -> UUID:
    if not value:
        raise ResourceNotFoundError("上传会话", value)
    try:
        return UUID(value)
    except ValueError as exc:
        raise ResourceNotFoundError("上传会话", value) from exc


def make_outbox_event(
    scope: ProjectScope,
    *,
    aggregate_id: UUID,
    event_type: str,
    payload: Mapping[str, object],
    now: datetime,
    aggregate_type: str = RESOURCE_TYPE,
) -> OutboxEvent:
    return OutboxEvent(
        event_id=uuid4(),
        project_id=scope.project_id,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        event_type=event_type,
        payload=payload,
        occurred_at=now,
    )


def record_upload_audit(
    audit: AuditRepository,
    outbox: OutboxRepository,
    scope: ProjectScope,
    *,
    session_id: UUID,
    action: str,
    from_status: str | None,
    to_status: str | None,
    result: str,
    details: Mapping[str, object],
    request_id: str | None,
    event_type: str,
    now: datetime,
) -> None:
    audit.record(
        scope,
        AuditRecord(
            audit_id=uuid4(),
            project_id=scope.project_id,
            actor_user_id=scope.actor_id,
            actor_role=None,
            resource_type=RESOURCE_TYPE,
            resource_id=session_id,
            action=action,
            from_status=from_status,
            to_status=to_status,
            reason=None,
            request_id=request_id,
            result=result,
            details=details,
            occurred_at=now,
        ),
    )
    outbox.enqueue(
        scope,
        make_outbox_event(
            scope,
            aggregate_id=session_id,
            event_type=event_type,
            payload={
                "upload_session_id": str(session_id),
                "from_status": from_status,
                "to_status": to_status,
                "result": result,
                **dict(details),
            },
            now=now,
        ),
    )
