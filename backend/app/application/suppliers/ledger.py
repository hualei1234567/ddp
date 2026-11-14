"""验收账本写入：在业务事务内追加，并与审计/outbox 同步。"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from app.application.datasets.audit import record_change
from app.application.suppliers.ports import (
    AcceptanceLedgerRepository,
    AuditRepository,
    OutboxRepository,
)
from app.domain.identity.models import ProjectScope
from app.domain.suppliers.models import AcceptanceLedgerEntry

RESOURCE_SUPPLIER = "SUPPLIER"
RESOURCE_BATCH = "INBOUND_BATCH"
RESOURCE_SEGMENT = "INBOUND_SEGMENT"


def append_ledger(
    ledger: AcceptanceLedgerRepository,
    audit: AuditRepository,
    outbox: OutboxRepository,
    scope: ProjectScope,
    *,
    batch_id: UUID | None,
    supplier_id: UUID,
    resource_type: str,
    resource_id: UUID,
    action: str,
    from_status: str | None,
    to_status: str | None,
    reason: str | None,
    request_id: str | None,
    snapshot: dict[str, object],
    occurred_at: datetime,
    event_type: str,
) -> None:
    """写一条只追加账本，同时写审计与 outbox（同一事务）。"""
    ledger.append(
        scope,
        AcceptanceLedgerEntry(
            ledger_id=uuid4(),
            project_id=scope.project_id,
            batch_id=batch_id,
            supplier_id=supplier_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            from_status=from_status,
            to_status=to_status,
            actor_id=scope.actor_id,
            request_id=request_id,
            reason=reason,
            snapshot=snapshot,
            occurred_at=occurred_at,
        ),
    )
    record_change(
        audit,
        outbox,
        scope,
        resource_type=resource_type,
        resource_id=resource_id,
        action=action,
        from_status=from_status,
        to_status=to_status,
        reason=reason,
        request_id=request_id,
        details=snapshot,
        event_type=event_type,
        event_payload={
            "batch_id": str(batch_id) if batch_id else None,
            "supplier_id": str(supplier_id),
            "resource_id": str(resource_id),
            "to_status": to_status,
        },
        occurred_at=occurred_at,
    )


__all__ = [
    "RESOURCE_BATCH",
    "RESOURCE_SEGMENT",
    "RESOURCE_SUPPLIER",
    "append_ledger",
]
