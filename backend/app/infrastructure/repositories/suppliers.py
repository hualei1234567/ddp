"""供应商与到货验收 SQL 仓储。

所有查询强制项目作用域；账本仓储只提供 ``append`` 与查询，没有更新/删除方法。
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.identity.models import ProjectScope
from app.domain.suppliers.enums import (
    SEGMENT_DECIDED_STATUSES,
    SegmentStatus,
)
from app.domain.suppliers.errors import DuplicateResourceError, VersionConflictError
from app.domain.suppliers.models import (
    AcceptanceLedgerEntry,
    InboundSegment,
    Supplier,
)
from app.infrastructure.db.models.suppliers import (
    AcceptanceLedgerModel,
    DeliveryBatchModel,
    InboundSegmentModel,
    SupplierModel,
)
from app.infrastructure.repositories.supplier_rows import aware_utc


def _supplier(row: SupplierModel) -> Supplier:
    return Supplier(
        supplier_id=row.id,
        project_id=row.project_id,
        code=row.code,
        name=row.name,
        category=row.category,
        status=row.status,
        contact_name=row.contact_name,
        contact_email=row.contact_email,
        external_ref=row.external_ref,
        notes=row.notes,
        row_version=row.version,
        created_at=aware_utc(row.created_at),
        updated_at=aware_utc(row.updated_at),
        created_by=row.created_by,
        updated_by=row.updated_by,
    )


def _segment(row: InboundSegmentModel) -> InboundSegment:
    return InboundSegment(
        segment_id=row.id,
        project_id=row.project_id,
        batch_id=row.batch_id,
        segment_code=row.segment_code,
        status=row.status,
        raw_asset_id=row.raw_asset_id,
        duration_ns=row.duration_ns,
        task_name=row.task_name,
        diagnostics=dict(row.diagnostics or {}),
        decision=row.decision,
        decision_reason=row.decision_reason,
        decided_by=row.decided_by,
        decided_at=aware_utc(row.decided_at),
        registered_by=row.registered_by,
        row_version=row.version,
        created_at=aware_utc(row.created_at),
        updated_at=aware_utc(row.updated_at),
        created_by=row.created_by,
        updated_by=row.updated_by,
    )


class SqlSupplierRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, scope: ProjectScope, supplier: Supplier) -> Supplier:
        scope.ensure(supplier.project_id)
        self._session.add(
            SupplierModel(
                id=supplier.supplier_id,
                project_id=supplier.project_id,
                code=supplier.code,
                name=supplier.name,
                category=supplier.category,
                status=supplier.status,
                contact_name=supplier.contact_name,
                contact_email=supplier.contact_email,
                external_ref=supplier.external_ref,
                notes=supplier.notes,
                version=supplier.row_version,
                created_at=supplier.created_at,
                updated_at=supplier.updated_at,
                created_by=supplier.created_by,
                updated_by=supplier.updated_by,
            )
        )
        try:
            self._session.flush()
        except IntegrityError as exc:
            raise DuplicateResourceError("供应商编码已存在") from exc
        return supplier

    def get(self, scope: ProjectScope, supplier_id: UUID) -> Supplier | None:
        row = self._session.get(SupplierModel, supplier_id)
        if row is None or row.project_id != scope.project_id:
            return None
        return _supplier(row)

    def list(
        self,
        scope: ProjectScope,
        *,
        status: str | None = None,
        category: str | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[Supplier]:
        stmt = select(SupplierModel).where(SupplierModel.project_id == scope.project_id)
        if status is not None:
            stmt = stmt.where(SupplierModel.status == status)
        if category is not None:
            stmt = stmt.where(SupplierModel.category == category)
        if after_id is not None:
            stmt = stmt.where(SupplierModel.id > after_id)
        rows = self._session.scalars(stmt.order_by(SupplierModel.id).limit(limit)).all()
        return [_supplier(row) for row in rows]

    def save(self, scope: ProjectScope, supplier: Supplier, *, expected_version: int) -> Supplier:
        result = self._session.execute(
            update(SupplierModel)
            .where(
                SupplierModel.id == supplier.supplier_id,
                SupplierModel.project_id == scope.project_id,
                SupplierModel.version == expected_version,
            )
            .values(
                name=supplier.name,
                category=supplier.category,
                status=supplier.status,
                contact_name=supplier.contact_name,
                contact_email=supplier.contact_email,
                external_ref=supplier.external_ref,
                notes=supplier.notes,
                version=expected_version + 1,
                updated_at=supplier.updated_at,
                updated_by=supplier.updated_by,
            )
        )
        if result.rowcount != 1:
            raise VersionConflictError("供应商")
        self._session.flush()
        refreshed = self.get(scope, supplier.supplier_id)
        assert refreshed is not None
        return refreshed


class SqlInboundSegmentRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, scope: ProjectScope, segment: InboundSegment) -> InboundSegment:
        scope.ensure(segment.project_id)
        self._session.add(
            InboundSegmentModel(
                id=segment.segment_id,
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
                version=segment.row_version,
                created_at=segment.created_at,
                updated_at=segment.updated_at,
                created_by=segment.created_by,
                updated_by=segment.updated_by,
            )
        )
        try:
            self._session.flush()
        except IntegrityError as exc:
            raise DuplicateResourceError("片段编码在批次内重复") from exc
        return segment

    def get(self, scope: ProjectScope, segment_id: UUID) -> InboundSegment | None:
        row = self._session.get(InboundSegmentModel, segment_id)
        if row is None or row.project_id != scope.project_id:
            return None
        return _segment(row)

    def list(
        self,
        scope: ProjectScope,
        batch_id: UUID,
        *,
        status: str | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[InboundSegment]:
        stmt = select(InboundSegmentModel).where(
            InboundSegmentModel.project_id == scope.project_id,
            InboundSegmentModel.batch_id == batch_id,
        )
        if status is not None:
            stmt = stmt.where(InboundSegmentModel.status == status)
        if after_id is not None:
            stmt = stmt.where(InboundSegmentModel.id > after_id)
        rows = self._session.scalars(stmt.order_by(InboundSegmentModel.id).limit(limit)).all()
        return [_segment(row) for row in rows]

    def save(
        self, scope: ProjectScope, segment: InboundSegment, *, expected_version: int
    ) -> InboundSegment:
        result = self._session.execute(
            update(InboundSegmentModel)
            .where(
                InboundSegmentModel.id == segment.segment_id,
                InboundSegmentModel.project_id == scope.project_id,
                InboundSegmentModel.version == expected_version,
            )
            .values(
                status=segment.status,
                raw_asset_id=segment.raw_asset_id,
                duration_ns=segment.duration_ns,
                task_name=segment.task_name,
                diagnostics=dict(segment.diagnostics or {}),
                decision=segment.decision,
                decision_reason=segment.decision_reason,
                decided_by=segment.decided_by,
                decided_at=segment.decided_at,
                version=expected_version + 1,
                updated_at=segment.updated_at,
                updated_by=segment.updated_by,
            )
        )
        if result.rowcount != 1:
            raise VersionConflictError("到货片段")
        self._session.flush()
        refreshed = self.get(scope, segment.segment_id)
        assert refreshed is not None
        return refreshed

    def status_counts(self, scope: ProjectScope, batch_id: UUID) -> dict[str, int]:
        rows = self._session.execute(
            select(InboundSegmentModel.status, func.count())
            .where(
                InboundSegmentModel.project_id == scope.project_id,
                InboundSegmentModel.batch_id == batch_id,
            )
            .group_by(InboundSegmentModel.status)
        ).all()
        counts = {status.value: 0 for status in SegmentStatus}
        for status, count in rows:
            counts[status] = int(count)
        return counts

    def decided_asset_refs_missing(self, scope: ProjectScope, batch_id: UUID) -> list[str]:
        """返回已通过但缺 RawAsset 引用的片段编码，用于终审门禁。"""
        rows = self._session.scalars(
            select(InboundSegmentModel.segment_code).where(
                InboundSegmentModel.project_id == scope.project_id,
                InboundSegmentModel.batch_id == batch_id,
                InboundSegmentModel.status == SegmentStatus.ACCEPTED.value,
                InboundSegmentModel.raw_asset_id.is_(None),
            )
        ).all()
        return list(rows)

    def count_decided(self, scope: ProjectScope, supplier_id: UUID) -> tuple[int, int]:
        """返回 (已通过片段数, 已裁决片段数)，统计该供应商名下所有批次。"""
        batch_ids = select(DeliveryBatchModel.id).where(
            DeliveryBatchModel.project_id == scope.project_id,
            DeliveryBatchModel.supplier_id == supplier_id,
        )
        base = (
            select(func.count())
            .select_from(InboundSegmentModel)
            .where(
                InboundSegmentModel.project_id == scope.project_id,
                InboundSegmentModel.batch_id.in_(batch_ids),
                InboundSegmentModel.status.in_(sorted(SEGMENT_DECIDED_STATUSES)),
            )
        )
        decided_total = int(self._session.scalar(base) or 0)
        accepted_total = int(
            self._session.scalar(
                base.where(InboundSegmentModel.status == SegmentStatus.ACCEPTED.value)
            )
            or 0
        )
        return accepted_total, decided_total


class SqlAcceptanceLedgerRepository:
    """只追加：没有 update / delete 方法，触发器在数据库层再兜底一次。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def append(self, scope: ProjectScope, entry: AcceptanceLedgerEntry) -> None:
        scope.ensure(entry.project_id)
        self._session.add(
            AcceptanceLedgerModel(
                id=entry.ledger_id,
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
        )
        self._session.flush()

    def list(
        self,
        scope: ProjectScope,
        *,
        batch_id: UUID | None = None,
        supplier_id: UUID | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[AcceptanceLedgerEntry]:
        stmt = select(AcceptanceLedgerModel).where(
            AcceptanceLedgerModel.project_id == scope.project_id
        )
        if batch_id is not None:
            stmt = stmt.where(AcceptanceLedgerModel.batch_id == batch_id)
        if supplier_id is not None:
            stmt = stmt.where(AcceptanceLedgerModel.supplier_id == supplier_id)
        if after_id is not None:
            stmt = stmt.where(AcceptanceLedgerModel.id > after_id)
        rows = self._session.scalars(stmt.order_by(AcceptanceLedgerModel.id).limit(limit)).all()
        return [
            AcceptanceLedgerEntry(
                ledger_id=row.id,
                project_id=row.project_id,
                batch_id=row.batch_id,
                supplier_id=row.supplier_id,
                action=row.action,
                resource_type=row.resource_type,
                resource_id=row.resource_id,
                from_status=row.from_status,
                to_status=row.to_status,
                actor_id=row.actor_id,
                request_id=row.request_id,
                reason=row.reason,
                snapshot=dict(row.snapshot or {}),
                occurred_at=aware_utc(row.occurred_at),
            )
            for row in rows
        ]

    def count(self, scope: ProjectScope, batch_id: UUID) -> int:
        return int(
            self._session.scalar(
                select(func.count()).where(
                    AcceptanceLedgerModel.project_id == scope.project_id,
                    AcceptanceLedgerModel.batch_id == batch_id,
                )
            )
            or 0
        )
