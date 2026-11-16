"""供应商与到货验收 HTTP 响应模型（``response_model`` 声明与契约比对用）。

**为什么单独成文件**：``supplier_schemas.py`` 承载请求模型（``extra="forbid"``），
本文件只放响应模型，与 ``dataset_response_models.py`` / ``modelops_response_models.py``
的既有做法一致，避免同一文件同时承载"严格入参"与"完整出参"两种语义。

**字段口径**：以运行时真实返回为准，并与
``packages/contracts/fragments/schemas.stage6.yaml`` 的同名 schema 对齐：

- 契约必填且实际恒非空的字段（``created_at`` / ``updated_at`` / ``version`` 等）
  声明为必填——它们分别来自 DB ``nullable=False`` 列与域对象的 ``positive_int``
  不变量，不会为 ``None``；
- 契约可空字段一律 ``| None`` 且给默认值 ``None``，避免导出成"运行时更严"的
  必填性差异（服务端总会输出该键，客户端照契约读是安全的）；
- ``allowed_actions`` 是运行时真实返回（前端按它渲染动作按钮），保留在模型里，
  不得为了通过比对而丢弃。

序列化点在此一次性收口（``from_domain``），不再保留旧的 dict 序列化函数。
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from app.domain.suppliers.metrics import SupplierMetrics
from app.domain.suppliers.models import (
    AcceptanceLedgerEntry,
    DeliveryBatch,
    InboundSegment,
    Supplier,
)


class SupplierOut(BaseModel):
    supplier_id: UUID
    project_id: UUID
    code: str
    name: str
    category: str
    status: str
    contact_name: str | None = None
    contact_email: str | None = None
    external_ref: str | None = None
    notes: str | None = None
    allowed_actions: list[str]
    version: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, supplier: Supplier) -> SupplierOut:
        return cls(
            supplier_id=supplier.supplier_id,
            project_id=supplier.project_id,
            code=supplier.code,
            name=supplier.name,
            category=supplier.category,
            status=supplier.status,
            contact_name=supplier.contact_name,
            contact_email=supplier.contact_email,
            external_ref=supplier.external_ref,
            notes=supplier.notes,
            allowed_actions=sorted(supplier.allowed_actions),
            version=supplier.row_version,
            created_at=supplier.created_at,
            updated_at=supplier.updated_at,
        )


class DeliveryBatchOut(BaseModel):
    batch_id: UUID
    project_id: UUID
    supplier_id: UUID
    batch_code: str
    title: str | None = None
    status: str
    planned_segment_count: int
    received_segment_count: int
    planned_arrival_date: datetime | None = None
    arrived_at: datetime | None = None
    settled_at: datetime | None = None
    rework_count: int
    storage_prefix: str | None = None
    notes: str | None = None
    allowed_actions: list[str]
    version: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, batch: DeliveryBatch) -> DeliveryBatchOut:
        return cls(
            batch_id=batch.batch_id,
            project_id=batch.project_id,
            supplier_id=batch.supplier_id,
            batch_code=batch.batch_code,
            title=batch.title,
            status=batch.status,
            planned_segment_count=batch.planned_segment_count,
            received_segment_count=batch.received_segment_count,
            planned_arrival_date=batch.planned_arrival_date,
            arrived_at=batch.arrived_at,
            settled_at=batch.settled_at,
            rework_count=batch.rework_count,
            storage_prefix=batch.storage_prefix,
            notes=batch.notes,
            allowed_actions=sorted(batch.allowed_actions),
            version=batch.row_version,
            created_at=batch.created_at,
            updated_at=batch.updated_at,
        )


class InboundSegmentOut(BaseModel):
    segment_id: UUID
    project_id: UUID
    batch_id: UUID
    segment_code: str
    status: str
    raw_asset_id: UUID | None = None
    duration_ns: int | None = None
    task_name: str | None = None
    diagnostics: dict[str, object]
    decision: str | None = None
    decision_reason: str | None = None
    decided_by: UUID | None = None
    decided_at: datetime | None = None
    registered_by: UUID | None = None
    allowed_actions: list[str]
    version: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, segment: InboundSegment) -> InboundSegmentOut:
        return cls(
            segment_id=segment.segment_id,
            project_id=segment.project_id,
            batch_id=segment.batch_id,
            segment_code=segment.segment_code,
            status=segment.status,
            raw_asset_id=segment.raw_asset_id,
            duration_ns=segment.duration_ns,
            task_name=segment.task_name,
            diagnostics=dict(segment.diagnostics or {}),
            decision=segment.decision,
            decision_reason=segment.decision_reason,
            decided_by=segment.decided_by,
            decided_at=segment.decided_at,
            registered_by=segment.registered_by,
            allowed_actions=sorted(segment.allowed_actions),
            version=segment.row_version,
            created_at=segment.created_at,
            updated_at=segment.updated_at,
        )


class AcceptanceLedgerEntryOut(BaseModel):
    ledger_id: UUID
    project_id: UUID
    batch_id: UUID | None = None
    supplier_id: UUID
    resource_type: str
    resource_id: UUID
    action: str
    from_status: str | None = None
    to_status: str | None = None
    actor_id: UUID
    request_id: str | None = None
    reason: str | None = None
    snapshot: dict[str, object]
    occurred_at: datetime

    @classmethod
    def from_domain(cls, entry: AcceptanceLedgerEntry) -> AcceptanceLedgerEntryOut:
        return cls(
            ledger_id=entry.ledger_id,
            project_id=entry.project_id,
            batch_id=entry.batch_id,
            supplier_id=entry.supplier_id,
            resource_type=entry.resource_type,
            resource_id=entry.resource_id,
            action=entry.action,
            from_status=entry.from_status,
            to_status=entry.to_status,
            actor_id=entry.actor_id,
            request_id=entry.request_id,
            reason=entry.reason,
            snapshot=dict(entry.snapshot),
            occurred_at=entry.occurred_at,
        )


class SupplierMetricsOut(BaseModel):
    """缺失值一律 ``null``，前端显示「暂无」，不得用 0 冒充。"""

    batch_count: int
    settled_batch_count: int
    on_time_rate: float | None = None
    first_pass_rate: float | None = None
    rework_rate: float | None = None
    average_cycle_hours: float | None = None
    accepted_segments: int
    decided_segments: int
    segment_pass_rate: float | None = None

    @classmethod
    def from_domain(cls, metrics: SupplierMetrics) -> SupplierMetricsOut:
        return cls(
            batch_count=metrics.batch_count,
            settled_batch_count=metrics.settled_batch_count,
            on_time_rate=metrics.on_time_rate,
            first_pass_rate=metrics.first_pass_rate,
            rework_rate=metrics.rework_rate,
            average_cycle_hours=metrics.average_cycle_hours,
            accepted_segments=metrics.accepted_segments,
            decided_segments=metrics.decided_segments,
            segment_pass_rate=metrics.segment_pass_rate,
        )


__all__ = [
    "AcceptanceLedgerEntryOut",
    "DeliveryBatchOut",
    "InboundSegmentOut",
    "SupplierMetricsOut",
    "SupplierOut",
]


class SupplierPortfolioEntryOut(BaseModel):
    supplier_id: str
    code: str
    name: str
    status: str
    has_settled_batches: bool
    metrics: SupplierMetricsOut


class SupplierPortfolioTotalsOut(BaseModel):
    """组合汇总。

    比率字段为 `null` 表示**没有任何供应商在该指标上有可计算数据**，
    不是"整体为 0"。前端不得渲染成 0%。
    """

    total_suppliers: int
    active_suppliers: int
    suppliers_without_settled_batches: int
    total_batches: int
    settled_batches: int
    on_time_rate: float | None = None
    first_pass_rate: float | None = None
    rework_rate: float | None = None
    average_cycle_hours: float | None = None
    segment_pass_rate: float | None = None


class SupplierPortfolioOut(BaseModel):
    totals: SupplierPortfolioTotalsOut
    suppliers: list[SupplierPortfolioEntryOut]
