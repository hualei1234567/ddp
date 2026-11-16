"""供应商与到货验收 HTTP 请求模型。

响应模型见 ``supplier_response_models.py``：入参 ``extra="forbid"``、出参完整，
两种语义分文件承载，避免一处改动同时影响两侧。
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


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
