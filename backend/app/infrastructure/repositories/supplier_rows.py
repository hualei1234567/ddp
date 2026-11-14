"""供应商域 ORM 行到领域实体的映射（多仓储共享，避免复制字段映射）。"""

from __future__ import annotations

from datetime import UTC, datetime

from app.domain.suppliers.models import DeliveryBatch
from app.infrastructure.db.models.suppliers import DeliveryBatchModel


def aware_utc(value: datetime | None) -> datetime | None:
    """数据库可能返回无时区时间（SQLite）；统一按 UTC 解释。"""
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value


def batch_from_row(row: DeliveryBatchModel) -> DeliveryBatch:
    return DeliveryBatch(
        batch_id=row.id,
        project_id=row.project_id,
        supplier_id=row.supplier_id,
        batch_code=row.batch_code,
        status=row.status,
        title=row.title,
        planned_segment_count=row.planned_segment_count,
        received_segment_count=row.received_segment_count,
        planned_arrival_date=aware_utc(row.planned_arrival_date),
        arrived_at=aware_utc(row.arrived_at),
        settled_at=aware_utc(row.settled_at),
        rework_count=row.rework_count,
        storage_prefix=row.storage_prefix,
        notes=row.notes,
        row_version=row.version,
        created_at=aware_utc(row.created_at),
        updated_at=aware_utc(row.updated_at),
        created_by=row.created_by,
        updated_by=row.updated_by,
    )
