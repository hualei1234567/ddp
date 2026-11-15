"""到货批次与片段用例：登记、状态迁移、人工裁决与终审。

终审结果由片段统计计算（``settle_batch``），不接受客户端指定；
裁决人不得与片段登记人相同（职责分离，复用阶段 4 的同一原则）。
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.application.suppliers.commands import (
    NewDeliveryBatch,
    NewInboundSegment,
    SegmentDecisionCommand,
)
from app.application.suppliers.idempotency import IdempotencyGuard, validate_key
from app.application.suppliers.ledger import (
    RESOURCE_BATCH,
    RESOURCE_SEGMENT,
    append_ledger,
)
from app.application.suppliers.ports import (
    AcceptanceLedgerRepository,
    AuditRepository,
    DeliveryBatchRepository,
    IdempotencyRepository,
    InboundSegmentRepository,
    OutboxRepository,
    RawAssetExistence,
    SupplierRepository,
    UnitOfWork,
)
from app.domain.identity.models import ProjectScope
from app.domain.suppliers.enums import DeliveryBatchStatus
from app.domain.suppliers.errors import (
    BatchAlreadySettledError,
    BatchReviewIncompleteError,
    DomainValidationError,
    InvalidStateTransitionError,
    ResourceNotFoundError,
    SegmentDecisionForbiddenError,
    SegmentRawAssetMissingError,
    SupplierNotActiveError,
    VersionConflictError,
)
from app.domain.suppliers.models import DeliveryBatch, InboundSegment, settle_batch
from app.domain.suppliers.state_machines import (
    allowed_actions,
    target_status,
    validate_action_name,
)

# R-13：事件类型用字面量映射表，禁止 f-string——AST 提取脚本抓不到 f-string，
# 新增事件会静默逃过 ``scripts/check_outbox_events.py`` 的归类校验。
# 键集合 = ``domain/suppliers/state_machines`` 的 delivery-batch / inbound-segment
# 动作并集，两者互不重叠，故可各自成表。
DELIVERY_BATCH_ACTION_EVENTS: dict[str, str] = {
    "dispatch": "supplier.batch.dispatch",
    "arrive": "supplier.batch.arrive",
    "start-screening": "supplier.batch.start-screening",
    "finalize": "supplier.batch.finalize",
    "rework": "supplier.batch.rework",
    "close": "supplier.batch.close",
}
INBOUND_SEGMENT_ACTION_EVENTS: dict[str, str] = {
    "queue-review": "supplier.segment.queue-review",
    "system-accept": "supplier.segment.system-accept",
    "system-reject": "supplier.segment.system-reject",
    "system-need-more": "supplier.segment.system-need-more",
    "resubmit": "supplier.segment.resubmit",
}


def _utcnow() -> datetime:
    return datetime.now(UTC)


class InboundBatchUseCases:
    def __init__(
        self,
        suppliers: SupplierRepository,
        batches: DeliveryBatchRepository,
        segments: InboundSegmentRepository,
        ledger: AcceptanceLedgerRepository,
        audit: AuditRepository,
        outbox: OutboxRepository,
        raw_assets: RawAssetExistence,
        uow: UnitOfWork,
        idempotency: IdempotencyRepository,
    ) -> None:
        self._suppliers = suppliers
        self._batches = batches
        self._segments = segments
        self._ledger = ledger
        self._audit = audit
        self._outbox = outbox
        self._raw_assets = raw_assets
        self._idempotency = IdempotencyGuard(idempotency)
        self._uow = uow

    # ---------- 批次 ----------

    def list(
        self,
        scope: ProjectScope,
        *,
        status: str | None = None,
        supplier_id: UUID | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[DeliveryBatch]:
        return self._batches.list(
            scope,
            status=status,
            supplier_id=supplier_id,
            after_id=after_id,
            limit=limit,
        )

    def get(self, scope: ProjectScope, batch_id: UUID) -> DeliveryBatch:
        batch = self._batches.get(scope, batch_id)
        if batch is None:
            raise ResourceNotFoundError("到货批次不存在或不属于当前项目")
        return batch

    def create(
        self,
        scope: ProjectScope,
        command: NewDeliveryBatch,
        *,
        idempotency_key: str | None = None,
        method: str = "POST",
        path: str,
        request_id: str | None = None,
    ) -> DeliveryBatch:
        key = validate_key(idempotency_key)
        body = {
            "supplier_id": str(command.supplier_id),
            "batch_code": command.batch_code,
            "title": command.title,
            "planned_segment_count": command.planned_segment_count,
            "planned_arrival_date": (
                command.planned_arrival_date.isoformat()
                if command.planned_arrival_date is not None
                else None
            ),
            "storage_prefix": command.storage_prefix,
            "notes": command.notes,
        }
        now = _utcnow()
        replay = self._idempotency.lookup(scope, key, method=method, path=path, body=body, now=now)
        if replay is not None:
            return self._replay_batch(scope, replay.response_ref)
        supplier = self._suppliers.get(scope, command.supplier_id)
        if supplier is None:
            raise ResourceNotFoundError("供应商不存在或不属于当前项目")
        if supplier.status != "ACTIVE":
            raise SupplierNotActiveError("供应商未激活，不能登记到货批次")
        now = _utcnow()
        batch = DeliveryBatch(
            batch_id=uuid4(),
            project_id=scope.project_id,
            supplier_id=command.supplier_id,
            batch_code=command.batch_code,
            title=command.title,
            planned_segment_count=command.planned_segment_count,
            planned_arrival_date=command.planned_arrival_date,
            storage_prefix=command.storage_prefix,
            notes=command.notes,
            created_at=now,
            updated_at=now,
            created_by=scope.actor_id,
            updated_by=scope.actor_id,
        )
        created = self._batches.add(scope, batch)
        append_ledger(
            self._ledger,
            self._audit,
            self._outbox,
            scope,
            batch_id=created.batch_id,
            supplier_id=created.supplier_id,
            resource_type=RESOURCE_BATCH,
            resource_id=created.batch_id,
            action="register",
            from_status=None,
            to_status=created.status,
            reason=None,
            request_id=request_id,
            snapshot={"batch_code": created.batch_code, "supplier_id": str(created.supplier_id)},
            occurred_at=now,
            event_type="supplier.batch.registered",
        )
        self._idempotency.store(
            scope,
            key,
            method=method,
            path=path,
            body=body,
            response_status=201,
            response_ref=str(created.batch_id),
            now=now,
        )
        self._uow.commit()
        return created

    def apply_action(
        self,
        scope: ProjectScope,
        batch_id: UUID,
        action: str,
        *,
        expected_version: int,
        reason: str | None = None,
        idempotency_key: str | None = None,
        method: str = "POST",
        path: str,
        request_id: str | None = None,
    ) -> DeliveryBatch:
        key = validate_key(idempotency_key)
        action_name = validate_action_name(action, family="delivery-batch")
        body = {
            "batch_id": str(batch_id),
            "action": action_name,
            "expected_version": expected_version,
            "reason": reason,
        }
        replay = self._idempotency.lookup(
            scope, key, method=method, path=path, body=body, now=_utcnow()
        )
        if replay is not None:
            return self._replay_batch(scope, replay.response_ref)
        batch = self.get(scope, batch_id)
        if batch.row_version != expected_version:
            raise VersionConflictError("到货批次")
        if action_name not in batch.allowed_actions:
            raise InvalidStateTransitionError("到货批次", batch.status, action_name)
        now = _utcnow()
        updates: dict[str, object] = {"updated_at": now, "updated_by": scope.actor_id}

        if action_name == "finalize":
            settlement = self._settle(scope, batch)
            updates.update(
                status=settlement.status,
                settled_at=now,
                received_segment_count=(
                    settlement.accepted + settlement.rejected + settlement.need_more
                ),
            )
        else:
            target = target_status("delivery-batch", batch.status, action_name)
            updates["status"] = target
            if action_name == "arrive":
                updates["arrived_at"] = now
            if action_name == "rework":
                if not (reason or "").strip():
                    raise DomainValidationError("rework 必须提供原因")
                updates["rework_count"] = batch.rework_count + 1

        saved = self._batches.save(
            scope, replace(batch, **updates), expected_version=expected_version
        )
        append_ledger(
            self._ledger,
            self._audit,
            self._outbox,
            scope,
            batch_id=saved.batch_id,
            supplier_id=saved.supplier_id,
            resource_type=RESOURCE_BATCH,
            resource_id=saved.batch_id,
            action=action_name,
            from_status=batch.status,
            to_status=saved.status,
            reason=reason,
            request_id=request_id,
            snapshot={"batch_code": saved.batch_code},
            occurred_at=now,
            event_type=DELIVERY_BATCH_ACTION_EVENTS[action_name],
        )
        self._idempotency.store(
            scope,
            key,
            method=method,
            path=path,
            body=body,
            response_status=200,
            response_ref=str(saved.batch_id),
            now=now,
        )
        self._uow.commit()
        return saved

    def _settle(self, scope: ProjectScope, batch: DeliveryBatch):
        if batch.status != DeliveryBatchStatus.SCREENING.value:
            raise InvalidStateTransitionError("到货批次", batch.status, "finalize")
        missing = self._segments.decided_asset_refs_missing(scope, batch.batch_id)
        if missing:
            raise SegmentRawAssetMissingError(
                "以下已通过片段缺少 RawAsset 引用：" + ", ".join(sorted(missing))
            )
        counts = self._segments.status_counts(scope, batch.batch_id)
        return settle_batch(
            accepted=counts.get("ACCEPTED", 0),
            rejected=counts.get("REJECTED", 0),
            need_more=counts.get("NEED_MORE", 0),
            pending=counts.get("REGISTERED", 0) + counts.get("PENDING_REVIEW", 0),
        )

    # ---------- 片段 ----------

    def list_segments(
        self,
        scope: ProjectScope,
        batch_id: UUID,
        *,
        status: str | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[InboundSegment]:
        self.get(scope, batch_id)
        return self._segments.list(scope, batch_id, status=status, after_id=after_id, limit=limit)

    def get_segment(self, scope: ProjectScope, batch_id: UUID, segment_id: UUID) -> InboundSegment:
        segment = self._segments.get(scope, segment_id)
        if segment is None or segment.batch_id != batch_id:
            raise ResourceNotFoundError("片段不存在或不属于该批次")
        return segment

    def add_segment(
        self,
        scope: ProjectScope,
        batch_id: UUID,
        command: NewInboundSegment,
        *,
        idempotency_key: str | None = None,
        method: str = "POST",
        path: str,
        request_id: str | None = None,
    ) -> InboundSegment:
        key = validate_key(idempotency_key)
        body = {
            "batch_id": str(batch_id),
            "segment_code": command.segment_code,
            "raw_asset_id": str(command.raw_asset_id) if command.raw_asset_id else None,
            "duration_ns": command.duration_ns,
            "task_name": command.task_name,
            "diagnostics": command.diagnostics,
        }
        now = _utcnow()
        replay = self._idempotency.lookup(scope, key, method=method, path=path, body=body, now=now)
        if replay is not None:
            return self._replay_segment(scope, batch_id, replay.response_ref)
        batch = self.get(scope, batch_id)
        if batch.is_settled:
            raise BatchAlreadySettledError("批次已终审，不能新增片段")
        if command.raw_asset_id is not None and not self._raw_assets.exists(
            scope.project_id, command.raw_asset_id
        ):
            raise ResourceNotFoundError("RawAsset 不存在或不属于当前项目")
        now = _utcnow()
        segment = InboundSegment(
            segment_id=uuid4(),
            project_id=scope.project_id,
            batch_id=batch_id,
            segment_code=command.segment_code,
            raw_asset_id=command.raw_asset_id,
            duration_ns=command.duration_ns,
            task_name=command.task_name,
            diagnostics=command.diagnostics,
            registered_by=scope.actor_id,
            created_at=now,
            updated_at=now,
            created_by=scope.actor_id,
            updated_by=scope.actor_id,
        )
        created = self._segments.add(scope, segment)
        append_ledger(
            self._ledger,
            self._audit,
            self._outbox,
            scope,
            batch_id=batch_id,
            supplier_id=batch.supplier_id,
            resource_type=RESOURCE_SEGMENT,
            resource_id=created.segment_id,
            action="register",
            from_status=None,
            to_status=created.status,
            reason=None,
            request_id=request_id,
            snapshot={"segment_code": created.segment_code},
            occurred_at=now,
            event_type="supplier.segment.registered",
        )
        self._idempotency.store(
            scope,
            key,
            method=method,
            path=path,
            body=body,
            response_status=201,
            response_ref=str(created.segment_id),
            now=now,
        )
        self._uow.commit()
        return created

    def apply_segment_action(
        self,
        scope: ProjectScope,
        batch_id: UUID,
        segment_id: UUID,
        action: str,
        *,
        expected_version: int,
        idempotency_key: str | None = None,
        method: str = "POST",
        path: str,
        request_id: str | None = None,
    ) -> InboundSegment:
        key = validate_key(idempotency_key)
        action_name = validate_action_name(action, family="inbound-segment")
        body = {
            "batch_id": str(batch_id),
            "segment_id": str(segment_id),
            "action": action_name,
            "expected_version": expected_version,
        }
        replay = self._idempotency.lookup(
            scope, key, method=method, path=path, body=body, now=_utcnow()
        )
        if replay is not None:
            return self._replay_segment(scope, batch_id, replay.response_ref)
        batch = self.get(scope, batch_id)
        segment = self.get_segment(scope, batch_id, segment_id)
        if segment.row_version != expected_version:
            raise VersionConflictError("到货片段")
        if action_name not in segment.allowed_actions:
            raise InvalidStateTransitionError("到货片段", segment.status, action_name)
        if action_name == "resubmit" and segment.raw_asset_id is None:
            raise SegmentRawAssetMissingError("补件后重新提交前必须提供 RawAsset 引用")
        target = target_status("inbound-segment", segment.status, action_name)
        now = _utcnow()
        saved = self._segments.save(
            scope,
            replace(segment, status=target, updated_at=now, updated_by=scope.actor_id),
            expected_version=expected_version,
        )
        append_ledger(
            self._ledger,
            self._audit,
            self._outbox,
            scope,
            batch_id=batch_id,
            supplier_id=batch.supplier_id,
            resource_type=RESOURCE_SEGMENT,
            resource_id=saved.segment_id,
            action=action_name,
            from_status=segment.status,
            to_status=saved.status,
            reason=None,
            request_id=request_id,
            snapshot={"segment_code": saved.segment_code},
            occurred_at=now,
            event_type=INBOUND_SEGMENT_ACTION_EVENTS[action_name],
        )
        self._idempotency.store(
            scope,
            key,
            method=method,
            path=path,
            body=body,
            response_status=200,
            response_ref=str(saved.segment_id),
            now=now,
        )
        self._uow.commit()
        return saved

    def decide_segment(
        self,
        scope: ProjectScope,
        batch_id: UUID,
        segment_id: UUID,
        command: SegmentDecisionCommand,
        *,
        expected_version: int,
        idempotency_key: str | None = None,
        method: str = "POST",
        path: str,
        request_id: str | None = None,
    ) -> InboundSegment:
        key = validate_key(idempotency_key)
        body = {
            "batch_id": str(batch_id),
            "segment_id": str(segment_id),
            "decision": command.decision,
            "reason": command.reason,
            "expected_version": expected_version,
        }
        now = _utcnow()
        replay = self._idempotency.lookup(scope, key, method=method, path=path, body=body, now=now)
        if replay is not None:
            return self._replay_segment(scope, batch_id, replay.response_ref)
        batch = self.get(scope, batch_id)
        batch.assert_screening()
        segment = self.get_segment(scope, batch_id, segment_id)
        if segment.row_version != expected_version:
            raise VersionConflictError("到货片段")
        if segment.registered_by is not None and segment.registered_by == scope.actor_id:
            raise SegmentDecisionForbiddenError("片段登记人不得裁决自己登记的片段")
        now = _utcnow()
        decided = segment.decide(
            decision=command.decision,
            reason=command.reason,
            decided_by=scope.actor_id,
            now=now,
        )
        saved = self._segments.save(scope, decided, expected_version=expected_version)
        append_ledger(
            self._ledger,
            self._audit,
            self._outbox,
            scope,
            batch_id=batch_id,
            supplier_id=batch.supplier_id,
            resource_type=RESOURCE_SEGMENT,
            resource_id=saved.segment_id,
            action=f"decide:{saved.decision}",
            from_status=segment.status,
            to_status=saved.status,
            reason=saved.decision_reason,
            request_id=request_id,
            snapshot={
                "segment_code": saved.segment_code,
                "decision": saved.decision,
                "raw_asset_id": str(saved.raw_asset_id) if saved.raw_asset_id else None,
            },
            occurred_at=now,
            event_type="supplier.segment.decided",
        )
        self._idempotency.store(
            scope,
            key,
            method=method,
            path=path,
            body=body,
            response_status=200,
            response_ref=str(saved.segment_id),
            now=now,
        )
        self._uow.commit()
        return saved

    def ledger(
        self,
        scope: ProjectScope,
        *,
        batch_id: UUID | None = None,
        supplier_id: UUID | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ):
        return self._ledger.list(
            scope,
            batch_id=batch_id,
            supplier_id=supplier_id,
            after_id=after_id,
            limit=limit,
        )

    def _replay_batch(self, scope: ProjectScope, response_ref: str | None) -> DeliveryBatch:
        """按幂等记录重放：只返回当前项目内的资源，越权与缺失同样 404。"""
        if not response_ref:
            raise ResourceNotFoundError("到货批次", response_ref)
        try:
            batch_id = UUID(response_ref)
        except ValueError as exc:
            raise ResourceNotFoundError("到货批次", response_ref) from exc
        return self.get(scope, batch_id)

    def _replay_segment(
        self, scope: ProjectScope, batch_id: UUID, response_ref: str | None
    ) -> InboundSegment:
        self.get(scope, batch_id)  # 批次必须属于当前项目；不存在则 404
        if not response_ref:
            raise ResourceNotFoundError("到货片段", response_ref)
        try:
            segment_id = UUID(response_ref)
        except ValueError as exc:
            raise ResourceNotFoundError("到货片段", response_ref) from exc
        return self.get_segment(scope, batch_id, segment_id)


def batch_actions(status: str) -> frozenset[str]:
    return allowed_actions("delivery-batch", status)


def segment_actions(status: str) -> frozenset[str]:
    return allowed_actions("inbound-segment", status)


__all__ = [
    "BatchReviewIncompleteError",
    "InboundBatchUseCases",
    "batch_actions",
    "segment_actions",
]
