"""供应商档案用例：登记、改档、状态动作与绩效查询。"""

from __future__ import annotations

from dataclasses import asdict, replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.application.suppliers.commands import NewSupplier, SupplierPatch
from app.application.suppliers.idempotency import IdempotencyGuard, validate_key
from app.application.suppliers.ledger import RESOURCE_SUPPLIER, append_ledger
from app.application.suppliers.ports import (
    AcceptanceLedgerRepository,
    AuditRepository,
    DeliveryBatchRepository,
    IdempotencyRepository,
    InboundSegmentRepository,
    OutboxRepository,
    SupplierRepository,
    UnitOfWork,
)
from app.domain.identity.models import ProjectScope
from app.domain.suppliers.errors import (
    DomainValidationError,
    InvalidStateTransitionError,
    ResourceNotFoundError,
    SupplierTerminalError,
    VersionConflictError,
)
from app.domain.suppliers.metrics import BatchSample, SupplierMetrics, summarize
from app.domain.suppliers.models import Supplier
from app.domain.suppliers.state_machines import (
    allowed_actions,
    target_status,
    validate_action_name,
)

# R-13：事件类型用字面量映射表，禁止 f-string——AST 提取脚本抓不到 f-string，
# 新增事件会静默逃过 ``scripts/check_outbox_events.py`` 的归类校验。
# 键集合 = ``domain/suppliers/state_machines`` 的 supplier 动作并集。
SUPPLIER_ACTION_EVENTS: dict[str, str] = {
    "activate": "supplier.activate",
    "suspend": "supplier.suspend",
    "terminate": "supplier.terminate",
    "resume": "supplier.resume",
}


def _utcnow() -> datetime:
    return datetime.now(UTC)


def new_supplier(
    *,
    project_id: UUID,
    command: NewSupplier,
    actor_id: UUID,
    now: datetime,
) -> Supplier:
    return Supplier(
        supplier_id=uuid4(),
        project_id=project_id,
        code=command.code,
        name=command.name,
        category=command.category,
        contact_name=command.contact_name,
        contact_email=command.contact_email,
        external_ref=command.external_ref,
        notes=command.notes,
        created_at=now,
        updated_at=now,
        created_by=actor_id,
        updated_by=actor_id,
    )


class SupplierUseCases:
    def __init__(
        self,
        suppliers: SupplierRepository,
        batches: DeliveryBatchRepository,
        segments: InboundSegmentRepository,
        ledger: AcceptanceLedgerRepository,
        audit: AuditRepository,
        outbox: OutboxRepository,
        uow: UnitOfWork,
        idempotency: IdempotencyRepository,
    ) -> None:
        self._suppliers = suppliers
        self._batches = batches
        self._segments = segments
        self._ledger = ledger
        self._audit = audit
        self._outbox = outbox
        self._uow = uow
        self._idempotency = IdempotencyGuard(idempotency)

    def list(
        self,
        scope: ProjectScope,
        *,
        status: str | None = None,
        category: str | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[Supplier]:
        return self._suppliers.list(
            scope, status=status, category=category, after_id=after_id, limit=limit
        )

    def get(self, scope: ProjectScope, supplier_id: UUID) -> Supplier:
        supplier = self._suppliers.get(scope, supplier_id)
        if supplier is None:
            raise ResourceNotFoundError("供应商不存在或不属于当前项目")
        return supplier

    def create(
        self,
        scope: ProjectScope,
        command: NewSupplier,
        *,
        idempotency_key: str | None = None,
        method: str = "POST",
        path: str,
        request_id: str | None = None,
    ) -> Supplier:
        key = validate_key(idempotency_key)
        body = asdict(command)
        now = _utcnow()
        replay = self._idempotency.lookup(scope, key, method=method, path=path, body=body, now=now)
        if replay is not None:
            return self._replay_supplier(scope, replay.response_ref)
        supplier = new_supplier(
            project_id=scope.project_id, command=command, actor_id=scope.actor_id, now=now
        )
        created = self._suppliers.add(scope, supplier)
        append_ledger(
            self._ledger,
            self._audit,
            self._outbox,
            scope,
            batch_id=None,
            supplier_id=created.supplier_id,
            resource_type=RESOURCE_SUPPLIER,
            resource_id=created.supplier_id,
            action="register",
            from_status=None,
            to_status=created.status,
            reason=None,
            request_id=request_id,
            snapshot={"code": created.code, "name": created.name},
            occurred_at=now,
            event_type="supplier.registered",
        )
        self._idempotency.store(
            scope,
            key,
            method=method,
            path=path,
            body=body,
            response_status=201,
            response_ref=str(created.supplier_id),
            now=now,
        )
        self._uow.commit()
        return created

    def update(
        self,
        scope: ProjectScope,
        supplier_id: UUID,
        patch: SupplierPatch,
        *,
        expected_version: int,
    ) -> Supplier:
        supplier = self.get(scope, supplier_id)
        if supplier.row_version != expected_version:
            raise VersionConflictError("供应商")
        if supplier.is_terminated:
            raise SupplierTerminalError("已终止供应商不可修改")
        updates: dict[str, object] = {"updated_at": _utcnow(), "updated_by": scope.actor_id}
        for name in ("name", "category", "contact_name", "contact_email", "external_ref", "notes"):
            value = getattr(patch, name)
            if value is not None:
                updates[name] = value
        if len(updates) == 2:
            raise DomainValidationError("没有需要修改的字段")
        saved = self._suppliers.save(
            scope, replace(supplier, **updates), expected_version=expected_version
        )
        self._uow.commit()
        return saved

    def apply_action(
        self,
        scope: ProjectScope,
        supplier_id: UUID,
        action: str,
        *,
        expected_version: int,
        reason: str | None = None,
        idempotency_key: str | None = None,
        method: str = "POST",
        path: str,
        request_id: str | None = None,
    ) -> Supplier:
        key = validate_key(idempotency_key)
        action_name = validate_action_name(action, family="supplier")
        body = {
            "action": action_name,
            "expected_version": expected_version,
            "reason": reason,
        }
        replay = self._idempotency.lookup(
            scope, key, method=method, path=path, body=body, now=_utcnow()
        )
        if replay is not None:
            return self._replay_supplier(scope, replay.response_ref)
        supplier = self.get(scope, supplier_id)
        if supplier.row_version != expected_version:
            raise VersionConflictError("供应商")
        if action_name not in supplier.allowed_actions:
            raise InvalidStateTransitionError("供应商", supplier.status, action_name)
        if action_name == "terminate" and not (reason or "").strip():
            raise DomainValidationError("terminate 必须提供原因")
        target = target_status("supplier", supplier.status, action_name)
        now = _utcnow()
        saved = self._suppliers.save(
            scope,
            replace(
                supplier,
                status=target,
                updated_at=now,
                updated_by=scope.actor_id,
            ),
            expected_version=expected_version,
        )
        append_ledger(
            self._ledger,
            self._audit,
            self._outbox,
            scope,
            batch_id=None,
            supplier_id=saved.supplier_id,
            resource_type=RESOURCE_SUPPLIER,
            resource_id=saved.supplier_id,
            action=action_name,
            from_status=supplier.status,
            to_status=saved.status,
            reason=reason,
            request_id=request_id,
            snapshot={"code": saved.code},
            occurred_at=now,
            event_type=SUPPLIER_ACTION_EVENTS[action_name],
        )
        self._idempotency.store(
            scope,
            key,
            method=method,
            path=path,
            body=body,
            response_status=200,
            response_ref=str(saved.supplier_id),
            now=now,
        )
        self._uow.commit()
        return saved

    def metrics(self, scope: ProjectScope, supplier_id: UUID) -> SupplierMetrics:
        self.get(scope, supplier_id)
        batches = self._batches.all_batches(scope, supplier_id)
        samples = [
            BatchSample(
                settled=item.is_settled,
                status=item.status,
                arrived_at=item.arrived_at,
                settled_at=item.settled_at,
                planned_arrival_date=item.planned_arrival_date,
                rework_count=item.rework_count,
            )
            for item in batches
        ]
        accepted, decided = self._segments.count_decided(scope, supplier_id)
        return summarize(samples, accepted_segments=accepted, decided_segments=decided)

    def _replay_supplier(self, scope: ProjectScope, response_ref: str | None) -> Supplier:
        """按幂等记录重放：只返回当前项目内的资源，越权与缺失同样 404。"""
        if not response_ref:
            raise ResourceNotFoundError("供应商", response_ref)
        try:
            supplier_id = UUID(response_ref)
        except ValueError as exc:
            raise ResourceNotFoundError("供应商", response_ref) from exc
        return self.get(scope, supplier_id)


def supplier_actions(status: str) -> frozenset[str]:
    return allowed_actions("supplier", status)


__all__ = ["SupplierUseCases", "new_supplier", "supplier_actions"]
