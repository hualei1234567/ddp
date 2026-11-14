"""供应商、到货批次、片段与验收账本实体。

实体不可变；状态只能经具名动作迁移。跨对象前置条件（项目归属、RawAsset 存在性、
批次是否可裁决）由应用层校验，领域层只保证自身不变量。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from uuid import UUID

from app.domain.datasets.validation import (
    coerce_enum,
    non_negative_int,
    optional_aware_datetime,
    optional_text,
    optional_uuid,
    positive_int,
    required_uuid,
    text_value,
)
from app.domain.suppliers.enums import (
    BATCH_SETTLED_STATUSES,
    SEGMENT_TERMINAL_STATUSES,
    DeliveryBatchStatus,
    SegmentDecision,
    SegmentStatus,
    SupplierCategory,
    SupplierStatus,
)
from app.domain.suppliers.errors import (
    BatchAlreadySettledError,
    BatchNotScreeningError,
    BatchReviewIncompleteError,
    DomainValidationError,
    SegmentDecisionReasonRequiredError,
    SegmentTerminalError,
    SupplierTerminalError,
)
from app.domain.suppliers.state_machines import allowed_actions

# 判为"通过"的唯一取值；其余两种都必须写明原因。
_REASON_REQUIRED_DECISIONS = frozenset(
    {SegmentDecision.REJECT.value, SegmentDecision.NEED_MORE.value}
)


@dataclass(frozen=True, slots=True)
class Supplier:
    supplier_id: UUID
    project_id: UUID
    code: str
    name: str
    category: str = SupplierCategory.DATA_COLLECTION.value
    status: str = SupplierStatus.DRAFT.value
    contact_name: str | None = None
    contact_email: str | None = None
    external_ref: str | None = None
    notes: str | None = None
    row_version: int = 1
    created_at: datetime | None = None
    updated_at: datetime | None = None
    created_by: UUID | None = None
    updated_by: UUID | None = None

    def __post_init__(self) -> None:
        for name in ("supplier_id", "project_id"):
            required_uuid(getattr(self, name), name)
        object.__setattr__(self, "code", text_value(self.code, "code", max_length=64))
        object.__setattr__(self, "name", text_value(self.name, "name", max_length=200))
        object.__setattr__(
            self, "category", coerce_enum(self.category, SupplierCategory, "category").value
        )
        object.__setattr__(self, "status", coerce_enum(self.status, SupplierStatus, "status").value)
        object.__setattr__(
            self, "contact_name", optional_text(self.contact_name, "contact_name", max_length=100)
        )
        object.__setattr__(
            self,
            "contact_email",
            optional_text(self.contact_email, "contact_email", max_length=200),
        )
        object.__setattr__(
            self, "external_ref", optional_text(self.external_ref, "external_ref", max_length=200)
        )
        object.__setattr__(self, "notes", optional_text(self.notes, "notes", max_length=2000))
        object.__setattr__(self, "row_version", positive_int(self.row_version, "row_version"))

    @property
    def allowed_actions(self) -> frozenset[str]:
        return allowed_actions("supplier", self.status)

    @property
    def is_terminated(self) -> bool:
        return self.status == SupplierStatus.TERMINATED.value

    def assert_active(self) -> None:
        """只有 ACTIVE 供应商可以承接新批次。"""
        if self.status != SupplierStatus.ACTIVE.value:
            raise SupplierTerminalError("供应商未激活，不能承接新批次")


@dataclass(frozen=True, slots=True)
class DeliveryBatch:
    batch_id: UUID
    project_id: UUID
    supplier_id: UUID
    batch_code: str
    status: str = DeliveryBatchStatus.ANNOUNCED.value
    title: str | None = None
    planned_segment_count: int = 0
    received_segment_count: int = 0
    planned_arrival_date: datetime | None = None
    arrived_at: datetime | None = None
    settled_at: datetime | None = None
    rework_count: int = 0
    storage_prefix: str | None = None
    notes: str | None = None
    row_version: int = 1
    created_at: datetime | None = None
    updated_at: datetime | None = None
    created_by: UUID | None = None
    updated_by: UUID | None = None

    def __post_init__(self) -> None:
        for name in ("batch_id", "project_id", "supplier_id"):
            required_uuid(getattr(self, name), name)
        object.__setattr__(
            self, "batch_code", text_value(self.batch_code, "batch_code", max_length=64)
        )
        object.__setattr__(
            self, "status", coerce_enum(self.status, DeliveryBatchStatus, "status").value
        )
        object.__setattr__(self, "title", optional_text(self.title, "title", max_length=200))
        for name in ("planned_segment_count", "received_segment_count", "rework_count"):
            object.__setattr__(self, name, non_negative_int(getattr(self, name), name))
        for name in ("planned_arrival_date", "arrived_at", "settled_at"):
            object.__setattr__(self, name, optional_aware_datetime(getattr(self, name), name))
        object.__setattr__(
            self,
            "storage_prefix",
            optional_text(self.storage_prefix, "storage_prefix", max_length=1024),
        )
        object.__setattr__(self, "notes", optional_text(self.notes, "notes", max_length=2000))
        object.__setattr__(self, "row_version", positive_int(self.row_version, "row_version"))

    @property
    def allowed_actions(self) -> frozenset[str]:
        return allowed_actions("delivery-batch", self.status)

    @property
    def is_screening(self) -> bool:
        return self.status == DeliveryBatchStatus.SCREENING.value

    @property
    def is_settled(self) -> bool:
        return self.status in BATCH_SETTLED_STATUSES

    def assert_screening(self) -> None:
        if self.is_screening:
            return
        if self.is_settled:
            raise BatchAlreadySettledError("批次已终审，不能继续裁决片段")
        raise BatchNotScreeningError("只有 SCREENING 状态的批次可以裁决片段")


@dataclass(frozen=True, slots=True)
class InboundSegment:
    segment_id: UUID
    project_id: UUID
    batch_id: UUID
    segment_code: str
    status: str = SegmentStatus.REGISTERED.value
    raw_asset_id: UUID | None = None
    duration_ns: int | None = None
    task_name: str | None = None
    diagnostics: dict[str, object] | None = None
    decision: str | None = None
    decision_reason: str | None = None
    decided_by: UUID | None = None
    decided_at: datetime | None = None
    registered_by: UUID | None = None
    row_version: int = 1
    created_at: datetime | None = None
    updated_at: datetime | None = None
    created_by: UUID | None = None
    updated_by: UUID | None = None

    def __post_init__(self) -> None:
        for name in ("segment_id", "project_id", "batch_id"):
            required_uuid(getattr(self, name), name)
        object.__setattr__(
            self, "segment_code", text_value(self.segment_code, "segment_code", max_length=64)
        )
        object.__setattr__(self, "status", coerce_enum(self.status, SegmentStatus, "status").value)
        object.__setattr__(self, "raw_asset_id", optional_uuid(self.raw_asset_id, "raw_asset_id"))
        if self.duration_ns is not None:
            object.__setattr__(
                self, "duration_ns", non_negative_int(self.duration_ns, "duration_ns")
            )
        object.__setattr__(
            self, "task_name", optional_text(self.task_name, "task_name", max_length=200)
        )
        object.__setattr__(self, "diagnostics", dict(self.diagnostics or {}))
        if self.decision is not None:
            object.__setattr__(
                self, "decision", coerce_enum(self.decision, SegmentDecision, "decision").value
            )
        object.__setattr__(
            self,
            "decision_reason",
            optional_text(self.decision_reason, "decision_reason", max_length=2000),
        )
        for name in ("decided_by", "registered_by"):
            object.__setattr__(self, name, optional_uuid(getattr(self, name), name))
        for name in ("decided_at", "created_at", "updated_at"):
            object.__setattr__(self, name, optional_aware_datetime(getattr(self, name), name))
        object.__setattr__(self, "row_version", positive_int(self.row_version, "row_version"))

    @property
    def allowed_actions(self) -> frozenset[str]:
        return allowed_actions("inbound-segment", self.status)

    def decide(
        self,
        *,
        decision: str,
        reason: str | None,
        decided_by: UUID,
        now: datetime,
    ) -> InboundSegment:
        """人工裁决。原因与职责分离由应用层补校验，这里只保证领域不变量。"""
        if self.status in SEGMENT_TERMINAL_STATUSES:
            raise SegmentTerminalError("片段已裁决，不能再次裁决")
        if self.status != SegmentStatus.PENDING_REVIEW.value:
            raise DomainValidationError("片段必须处于 PENDING_REVIEW 才能裁决")
        parsed = coerce_enum(decision, SegmentDecision, "decision")
        cleaned_reason = optional_text(reason, "decision_reason", max_length=2000)
        if parsed.value in _REASON_REQUIRED_DECISIONS and cleaned_reason is None:
            raise SegmentDecisionReasonRequiredError("拒收或待补件必须填写原因")
        target = {
            SegmentDecision.ACCEPT: SegmentStatus.ACCEPTED.value,
            SegmentDecision.REJECT: SegmentStatus.REJECTED.value,
            SegmentDecision.NEED_MORE: SegmentStatus.NEED_MORE.value,
        }[parsed]
        return replace(
            self,
            status=target,
            decision=parsed.value,
            decision_reason=cleaned_reason,
            decided_by=decided_by,
            decided_at=now,
            updated_at=now,
            updated_by=decided_by,
        )


@dataclass(frozen=True, slots=True)
class AcceptanceLedgerEntry:
    """只追加的核验账本；没有 update/delete 通道。"""

    ledger_id: UUID
    project_id: UUID
    batch_id: UUID | None
    supplier_id: UUID
    action: str
    resource_type: str
    resource_id: UUID
    from_status: str | None
    to_status: str | None
    actor_id: UUID
    request_id: str | None
    reason: str | None
    snapshot: dict[str, object]
    occurred_at: datetime

    def __post_init__(self) -> None:
        for name in ("ledger_id", "project_id", "supplier_id", "resource_id"):
            required_uuid(getattr(self, name), name)
        object.__setattr__(self, "batch_id", optional_uuid(self.batch_id, "batch_id"))
        object.__setattr__(self, "action", text_value(self.action, "action", max_length=64))
        object.__setattr__(
            self,
            "resource_type",
            text_value(self.resource_type, "resource_type", max_length=32),
        )
        object.__setattr__(self, "actor_id", required_uuid(self.actor_id, "actor_id"))
        object.__setattr__(
            self, "from_status", optional_text(self.from_status, "from_status", max_length=32)
        )
        object.__setattr__(
            self, "to_status", optional_text(self.to_status, "to_status", max_length=32)
        )
        object.__setattr__(
            self, "request_id", optional_text(self.request_id, "request_id", max_length=128)
        )
        object.__setattr__(self, "reason", optional_text(self.reason, "reason", max_length=2000))
        object.__setattr__(self, "snapshot", dict(self.snapshot or {}))
        object.__setattr__(self, "occurred_at", _require_aware(self.occurred_at, "occurred_at"))


def _require_aware(value: object, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise DomainValidationError(f"{field_name} 必须是带时区时间")
    return value


@dataclass(frozen=True, slots=True)
class BatchSettlement:
    """终审判定结果；由片段统计计算，不接受客户端指定。"""

    status: str
    accepted: int
    rejected: int
    need_more: int

    @property
    def total(self) -> int:
        return self.accepted + self.rejected + self.need_more


def settle_batch(
    *,
    accepted: int,
    rejected: int,
    need_more: int,
    pending: int,
) -> BatchSettlement:
    """按片段统计判定批次终态。

    规则（PRD §5.2）：存在未裁决或待补件片段 → 不允许终审；全部通过 → ACCEPTED；
    全部拒收 → REJECTED；其余 → PARTIALLY_ACCEPTED。
    """
    counts = {
        "accepted": non_negative_int(accepted, "accepted"),
        "rejected": non_negative_int(rejected, "rejected"),
        "need_more": non_negative_int(need_more, "need_more"),
    }
    total = sum(counts.values()) + non_negative_int(pending, "pending")
    if total == 0:
        raise BatchReviewIncompleteError("批次没有片段，不能终审")
    if pending > 0:
        raise BatchReviewIncompleteError(f"仍有 {pending} 个片段未裁决")
    if need_more > 0:
        raise BatchReviewIncompleteError(f"仍有 {need_more} 个片段待补件")
    if rejected == 0:
        status = DeliveryBatchStatus.ACCEPTED.value
    elif accepted == 0:
        status = DeliveryBatchStatus.REJECTED.value
    else:
        status = DeliveryBatchStatus.PARTIALLY_ACCEPTED.value
    return BatchSettlement(
        status=status,
        accepted=counts["accepted"],
        rejected=counts["rejected"],
        need_more=counts["need_more"],
    )
