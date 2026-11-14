"""供应商与到货验收 HTTP 请求/响应模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.suppliers.metrics import SupplierMetrics
from app.domain.suppliers.models import (
    AcceptanceLedgerEntry,
    DeliveryBatch,
    InboundSegment,
    Supplier,
)


class SupplierCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=200)
    category: str = "DATA_COLLECTION"
    contact_name: str | None = Field(default=None, max_length=100)
    contact_email: str | None = Field(default=None, max_length=200)
    external_ref: str | None = Field(default=None, max_length=200)
    notes: str | None = Field(default=None, max_length=2000)


class SupplierPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    category: str | None = None
    contact_name: str | None = Field(default=None, max_length=100)
    contact_email: str | None = Field(default=None, max_length=200)
    external_ref: str | None = Field(default=None, max_length=200)
    notes: str | None = Field(default=None, max_length=2000)


class SupplierActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=2000)


class BatchCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    supplier_id: UUID
    batch_code: str = Field(min_length=1, max_length=64)
    title: str | None = Field(default=None, max_length=200)
    planned_segment_count: int = Field(default=0, ge=0)
    planned_arrival_date: datetime | None = None
    storage_prefix: str | None = Field(default=None, max_length=1024)
    notes: str | None = Field(default=None, max_length=2000)


class BatchActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=2000)


class SegmentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    segment_code: str = Field(min_length=1, max_length=64)
    raw_asset_id: UUID | None = None
    duration_ns: int | None = Field(default=None, ge=0)
    task_name: str | None = Field(default=None, max_length=200)
    diagnostics: dict[str, object] = Field(default_factory=dict)


class SegmentDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: str
    reason: str | None = Field(default=None, max_length=2000)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def supplier_out(supplier: Supplier) -> dict[str, Any]:
    return {
        "supplier_id": supplier.supplier_id,
        "project_id": supplier.project_id,
        "code": supplier.code,
        "name": supplier.name,
        "category": supplier.category,
        "status": supplier.status,
        "contact_name": supplier.contact_name,
        "contact_email": supplier.contact_email,
        "external_ref": supplier.external_ref,
        "notes": supplier.notes,
        "allowed_actions": sorted(supplier.allowed_actions),
        "version": supplier.row_version,
        "created_at": _iso(supplier.created_at),
        "updated_at": _iso(supplier.updated_at),
    }


def batch_out(batch: DeliveryBatch) -> dict[str, Any]:
    return {
        "batch_id": batch.batch_id,
        "project_id": batch.project_id,
        "supplier_id": batch.supplier_id,
        "batch_code": batch.batch_code,
        "title": batch.title,
        "status": batch.status,
        "planned_segment_count": batch.planned_segment_count,
        "received_segment_count": batch.received_segment_count,
        "planned_arrival_date": _iso(batch.planned_arrival_date),
        "arrived_at": _iso(batch.arrived_at),
        "settled_at": _iso(batch.settled_at),
        "rework_count": batch.rework_count,
        "storage_prefix": batch.storage_prefix,
        "notes": batch.notes,
        "allowed_actions": sorted(batch.allowed_actions),
        "version": batch.row_version,
        "created_at": _iso(batch.created_at),
        "updated_at": _iso(batch.updated_at),
    }


def segment_out(segment: InboundSegment) -> dict[str, Any]:
    return {
        "segment_id": segment.segment_id,
        "project_id": segment.project_id,
        "batch_id": segment.batch_id,
        "segment_code": segment.segment_code,
        "status": segment.status,
        "raw_asset_id": segment.raw_asset_id,
        "duration_ns": segment.duration_ns,
        "task_name": segment.task_name,
        "diagnostics": dict(segment.diagnostics or {}),
        "decision": segment.decision,
        "decision_reason": segment.decision_reason,
        "decided_by": segment.decided_by,
        "decided_at": _iso(segment.decided_at),
        "registered_by": segment.registered_by,
        "allowed_actions": sorted(segment.allowed_actions),
        "version": segment.row_version,
        "created_at": _iso(segment.created_at),
        "updated_at": _iso(segment.updated_at),
    }


def ledger_out(entry: AcceptanceLedgerEntry) -> dict[str, Any]:
    return {
        "ledger_id": entry.ledger_id,
        "project_id": entry.project_id,
        "batch_id": entry.batch_id,
        "supplier_id": entry.supplier_id,
        "resource_type": entry.resource_type,
        "resource_id": entry.resource_id,
        "action": entry.action,
        "from_status": entry.from_status,
        "to_status": entry.to_status,
        "actor_id": entry.actor_id,
        "request_id": entry.request_id,
        "reason": entry.reason,
        "snapshot": dict(entry.snapshot),
        "occurred_at": _iso(entry.occurred_at),
    }


def metrics_out(metrics: SupplierMetrics) -> dict[str, Any]:
    """缺失值一律 ``null``，前端显示「暂无」，不得用 0 冒充。"""
    return {
        "batch_count": metrics.batch_count,
        "settled_batch_count": metrics.settled_batch_count,
        "on_time_rate": metrics.on_time_rate,
        "first_pass_rate": metrics.first_pass_rate,
        "rework_rate": metrics.rework_rate,
        "average_cycle_hours": metrics.average_cycle_hours,
        "accepted_segments": metrics.accepted_segments,
        "decided_segments": metrics.decided_segments,
        "segment_pass_rate": metrics.segment_pass_rate,
    }
