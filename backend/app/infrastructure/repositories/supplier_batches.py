"""供应商到货批次 SQL 仓储（从 suppliers.py 拆出以控制单文件规模）。"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.identity.models import ProjectScope
from app.domain.suppliers.enums import BATCH_SETTLED_STATUSES
from app.domain.suppliers.errors import DuplicateResourceError, VersionConflictError
from app.domain.suppliers.models import DeliveryBatch
from app.infrastructure.db.models.suppliers import DeliveryBatchModel
from app.infrastructure.repositories.supplier_rows import batch_from_row


class SqlDeliveryBatchRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, scope: ProjectScope, batch: DeliveryBatch) -> DeliveryBatch:
        scope.ensure(batch.project_id)
        self._session.add(
            DeliveryBatchModel(
                id=batch.batch_id,
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
                version=batch.row_version,
                created_at=batch.created_at,
                updated_at=batch.updated_at,
                created_by=batch.created_by,
                updated_by=batch.updated_by,
            )
        )
        try:
            self._session.flush()
        except IntegrityError as exc:
            raise DuplicateResourceError("批次编码已存在") from exc
        return batch

    def get(self, scope: ProjectScope, batch_id: UUID) -> DeliveryBatch | None:
        row = self._session.get(DeliveryBatchModel, batch_id)
        if row is None or row.project_id != scope.project_id:
            return None
        return batch_from_row(row)

    def list(
        self,
        scope: ProjectScope,
        *,
        status: str | None = None,
        supplier_id: UUID | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[DeliveryBatch]:
        stmt = select(DeliveryBatchModel).where(DeliveryBatchModel.project_id == scope.project_id)
        if status is not None:
            stmt = stmt.where(DeliveryBatchModel.status == status)
        if supplier_id is not None:
            stmt = stmt.where(DeliveryBatchModel.supplier_id == supplier_id)
        if after_id is not None:
            stmt = stmt.where(DeliveryBatchModel.id > after_id)
        rows = self._session.scalars(stmt.order_by(DeliveryBatchModel.id).limit(limit)).all()
        return [batch_from_row(row) for row in rows]

    def save(
        self, scope: ProjectScope, batch: DeliveryBatch, *, expected_version: int
    ) -> DeliveryBatch:
        result = self._session.execute(
            update(DeliveryBatchModel)
            .where(
                DeliveryBatchModel.id == batch.batch_id,
                DeliveryBatchModel.project_id == scope.project_id,
                DeliveryBatchModel.version == expected_version,
            )
            .values(
                status=batch.status,
                title=batch.title,
                planned_segment_count=batch.planned_segment_count,
                received_segment_count=batch.received_segment_count,
                planned_arrival_date=batch.planned_arrival_date,
                arrived_at=batch.arrived_at,
                settled_at=batch.settled_at,
                rework_count=batch.rework_count,
                storage_prefix=batch.storage_prefix,
                notes=batch.notes,
                version=expected_version + 1,
                updated_at=batch.updated_at,
                updated_by=batch.updated_by,
            )
        )
        if result.rowcount != 1:
            raise VersionConflictError("到货批次")
        self._session.flush()
        refreshed = self.get(scope, batch.batch_id)
        assert refreshed is not None
        return refreshed

    def settled_batches(self, scope: ProjectScope, supplier_id: UUID) -> list[DeliveryBatch]:
        rows = self._session.scalars(
            select(DeliveryBatchModel)
            .where(
                DeliveryBatchModel.project_id == scope.project_id,
                DeliveryBatchModel.supplier_id == supplier_id,
                DeliveryBatchModel.status.in_(sorted(BATCH_SETTLED_STATUSES)),
            )
            .order_by(DeliveryBatchModel.id)
        ).all()
        return [batch_from_row(row) for row in rows]

    def all_batches(self, scope: ProjectScope, supplier_id: UUID) -> list[DeliveryBatch]:
        rows = self._session.scalars(
            select(DeliveryBatchModel)
            .where(
                DeliveryBatchModel.project_id == scope.project_id,
                DeliveryBatchModel.supplier_id == supplier_id,
            )
            .order_by(DeliveryBatchModel.id)
        ).all()
        return [batch_from_row(row) for row in rows]
