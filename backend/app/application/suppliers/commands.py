"""供应商域命令 DTO。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True, slots=True)
class NewSupplier:
    code: str
    name: str
    category: str = "DATA_COLLECTION"
    contact_name: str | None = None
    contact_email: str | None = None
    external_ref: str | None = None
    notes: str | None = None


@dataclass(frozen=True, slots=True)
class SupplierPatch:
    name: str | None = None
    category: str | None = None
    contact_name: str | None = None
    contact_email: str | None = None
    external_ref: str | None = None
    notes: str | None = None


@dataclass(frozen=True, slots=True)
class NewDeliveryBatch:
    supplier_id: UUID
    batch_code: str
    title: str | None = None
    planned_segment_count: int = 0
    planned_arrival_date: datetime | None = None
    storage_prefix: str | None = None
    notes: str | None = None


@dataclass(frozen=True, slots=True)
class NewInboundSegment:
    segment_code: str
    raw_asset_id: UUID | None = None
    duration_ns: int | None = None
    task_name: str | None = None
    diagnostics: dict[str, object] | None = None


@dataclass(frozen=True, slots=True)
class SegmentDecisionCommand:
    decision: str
    reason: str | None = None
