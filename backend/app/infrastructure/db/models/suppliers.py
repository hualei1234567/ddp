"""阶段 6 供应商与到货验收 ORM。

所有表强制 ``project_id`` 与 ``(project_id, ...)`` 复合唯一约束；
账本表 ``acceptance_ledger_entries`` 只追加，由 ``suppliers_guards`` 安装禁改触发器。
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.suppliers.enums import (
    DeliveryBatchStatus,
    SegmentDecision,
    SegmentStatus,
    SupplierCategory,
    SupplierStatus,
)
from app.infrastructure.db.base import Base

_JSON_TYPE = JSON().with_variant(JSONB, "postgresql")


def _enum_check(column: str, enum_type: type, name: str) -> CheckConstraint:
    values = ", ".join(repr(item.value) for item in enum_type)
    return CheckConstraint(f"{column} IN ({values})", name=name)


class SupplierModel(Base):
    __tablename__ = "suppliers"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    project_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    contact_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    contact_email: Mapped[str | None] = mapped_column(String(200), nullable=True)
    external_ref: Mapped[str | None] = mapped_column(String(200), nullable=True)
    notes: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    updated_by: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)

    __table_args__ = (
        UniqueConstraint("project_id", "code", name="uq_suppliers_project_code"),
        UniqueConstraint("project_id", "id", name="uq_suppliers_project_id_id"),
        _enum_check("status", SupplierStatus, "ck_suppliers_status"),
        _enum_check("category", SupplierCategory, "ck_suppliers_category"),
        Index("ix_suppliers_project_status", "project_id", "status"),
    )


class DeliveryBatchModel(Base):
    __tablename__ = "supplier_delivery_batches"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    project_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    supplier_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    batch_code: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    planned_segment_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    received_segment_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    planned_arrival_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    arrived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rework_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    storage_prefix: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    notes: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    updated_by: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "supplier_id"],
            ["suppliers.project_id", "suppliers.id"],
            name="fk_supplier_batches_supplier",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("project_id", "batch_code", name="uq_supplier_batches_code"),
        UniqueConstraint("project_id", "id", name="uq_supplier_batches_project_id_id"),
        _enum_check("status", DeliveryBatchStatus, "ck_supplier_batches_status"),
        CheckConstraint("planned_segment_count >= 0", name="ck_supplier_batches_planned"),
        CheckConstraint("received_segment_count >= 0", name="ck_supplier_batches_received"),
        CheckConstraint("rework_count >= 0", name="ck_supplier_batches_rework"),
        Index("ix_supplier_batches_project_status", "project_id", "status"),
        Index("ix_supplier_batches_supplier", "project_id", "supplier_id"),
    )


class InboundSegmentModel(Base):
    __tablename__ = "inbound_segments"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    project_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    batch_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    segment_code: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    raw_asset_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    # 纳秒级时长超出 32 位范围，与 Episode 时长字段一致使用 BigInteger。
    duration_ns: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    task_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    diagnostics: Mapped[dict] = mapped_column(_JSON_TYPE, nullable=False, default=dict)
    decision: Mapped[str | None] = mapped_column(String(16), nullable=True)
    decision_reason: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    decided_by: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    registered_by: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    updated_by: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "batch_id"],
            ["supplier_delivery_batches.project_id", "supplier_delivery_batches.id"],
            name="fk_inbound_segments_batch",
            ondelete="CASCADE",
        ),
        UniqueConstraint("project_id", "batch_id", "segment_code", name="uq_inbound_segments_code"),
        _enum_check("status", SegmentStatus, "ck_inbound_segments_status"),
        CheckConstraint(
            "decision IS NULL OR decision IN "
            f"({', '.join(repr(item.value) for item in SegmentDecision)})",
            name="ck_inbound_segments_decision",
        ),
        CheckConstraint(
            "duration_ns IS NULL OR duration_ns >= 0", name="ck_inbound_segments_duration"
        ),
        Index("ix_inbound_segments_batch_status", "project_id", "batch_id", "status"),
    )


class AcceptanceLedgerModel(Base):
    """只追加核验账本；触发器禁止 UPDATE/DELETE。"""

    __tablename__ = "acceptance_ledger_entries"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    project_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    # 供应商级事件（登记/激活/终止）没有批次，因此允许为空。
    batch_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    supplier_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    resource_type: Mapped[str] = mapped_column(String(32), nullable=False)
    resource_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    to_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    actor_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    request_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    reason: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    snapshot: Mapped[dict] = mapped_column(_JSON_TYPE, nullable=False, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "batch_id"],
            ["supplier_delivery_batches.project_id", "supplier_delivery_batches.id"],
            name="fk_acceptance_ledger_batch",
            ondelete="CASCADE",
        ),
        Index("ix_acceptance_ledger_batch", "project_id", "batch_id", "occurred_at"),
        Index("ix_acceptance_ledger_supplier", "project_id", "supplier_id", "occurred_at"),
    )
