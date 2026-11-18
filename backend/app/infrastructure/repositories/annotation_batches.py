"""标注批次 SQLAlchemy 仓储。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.domain.annotation.batch import AnnotationBatch
from app.domain.annotation.errors import DomainValidationError
from app.domain.identity.models import ProjectScope
from app.infrastructure.db.models.annotation_work import (
    AnnotationBatch as AnnotationBatchModel,
)
from app.infrastructure.repositories._annotation_sql import (
    as_utc,
    ensure_expected_version,
    ensure_initial_version,
    enum_value,
    flush_or_duplicate,
    raise_update_failure,
    scoped_select,
)

_TERMINAL_STATUSES = ("ACCEPTED", "CANCELLED")


class SqlAnnotationBatchRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, scope: ProjectScope, batch: AnnotationBatch) -> AnnotationBatch:
        scope.ensure(batch.project_id)
        ensure_initial_version(batch.version)
        now = datetime.now(UTC)
        row = AnnotationBatchModel(
            id=batch.batch_id,
            project_id=batch.project_id,
            task_id=batch.task_id,
            code=batch.code,
            name=batch.name,
            sequence_no=batch.sequence_no,
            status=enum_value(batch.status),
            stratum=batch.stratum,
            item_refs=[str(item) for item in batch.item_refs],
            planned_item_count=batch.planned_item_count,
            assignee_ids=[str(item) for item in batch.assignee_ids],
            reviewer_pool_ids=[str(item) for item in batch.reviewer_pool_ids],
            due_at=batch.due_at,
            submitted_item_count=batch.submitted_item_count,
            approved_item_count=batch.approved_item_count,
            rejected_item_count=batch.rejected_item_count,
            rework_count=batch.rework_count,
            quality_summary=dict(batch.quality_summary),
            status_reason=batch.status_reason,
            version=batch.version,
            created_at=batch.created_at or now,
            updated_at=batch.updated_at or now,
            created_by=scope.actor_id,
            updated_by=scope.actor_id,
        )
        self._session.add(row)
        flush_or_duplicate(self._session)
        return batch

    def get(self, scope: ProjectScope, batch_id: UUID) -> AnnotationBatch | None:
        row = self._session.scalar(
            select(AnnotationBatchModel).where(
                AnnotationBatchModel.id == batch_id,
                AnnotationBatchModel.project_id == scope.project_id,
            )
        )
        return self._from_row(row) if row is not None else None

    def list(
        self,
        scope: ProjectScope,
        *,
        task_id: UUID,
        status: str | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[AnnotationBatch]:
        if limit < 1 or limit > 101:
            raise DomainValidationError("limit 必须在 1 到 101 之间")
        statement = scoped_select(AnnotationBatchModel, scope).where(
            AnnotationBatchModel.task_id == task_id
        )
        if status is not None:
            statement = statement.where(AnnotationBatchModel.status == status)
        if after_id is not None:
            statement = statement.where(AnnotationBatchModel.id > after_id)
        statement = statement.order_by(AnnotationBatchModel.id).limit(limit)
        return [self._from_row(row) for row in self._session.scalars(statement)]

    def list_non_terminal(
        self,
        scope: ProjectScope,
        *,
        task_id: UUID,
    ) -> list[AnnotationBatch]:
        statement = (
            scoped_select(AnnotationBatchModel, scope)
            .where(
                AnnotationBatchModel.task_id == task_id,
                AnnotationBatchModel.status.not_in(_TERMINAL_STATUSES),
            )
            .order_by(AnnotationBatchModel.sequence_no)
        )
        return [self._from_row(row) for row in self._session.scalars(statement)]

    def next_sequence_no(self, scope: ProjectScope, task_id: UUID) -> int:
        current = self._session.scalar(
            select(func.max(AnnotationBatchModel.sequence_no)).where(
                AnnotationBatchModel.project_id == scope.project_id,
                AnnotationBatchModel.task_id == task_id,
            )
        )
        return int(current or 0) + 1

    def sum_planned_item_count(self, scope: ProjectScope, task_id: UUID) -> int:
        total = self._session.scalar(
            select(func.coalesce(func.sum(AnnotationBatchModel.planned_item_count), 0)).where(
                AnnotationBatchModel.project_id == scope.project_id,
                AnnotationBatchModel.task_id == task_id,
                AnnotationBatchModel.status != "CANCELLED",
            )
        )
        return int(total or 0)

    def update_counts(
        self,
        scope: ProjectScope,
        batch_id: UUID,
        counts: dict[str, int],
    ) -> None:
        """只写派生的计数字段，不推进乐观锁版本（理由同任务 ``update_progress``）。"""
        self._session.execute(
            update(AnnotationBatchModel)
            .where(
                AnnotationBatchModel.id == batch_id,
                AnnotationBatchModel.project_id == scope.project_id,
            )
            .values(**counts)
            .execution_options(synchronize_session=False)
        )

    def save(
        self,
        scope: ProjectScope,
        batch: AnnotationBatch,
        *,
        expected_version: int,
    ) -> AnnotationBatch:
        scope.ensure(batch.project_id)
        ensure_expected_version(batch.version, expected_version)
        now = batch.updated_at or datetime.now(UTC)
        result = self._session.execute(
            update(AnnotationBatchModel)
            .where(
                AnnotationBatchModel.id == batch.batch_id,
                AnnotationBatchModel.project_id == scope.project_id,
                AnnotationBatchModel.version == expected_version,
            )
            .values(
                name=batch.name,
                sequence_no=batch.sequence_no,
                status=enum_value(batch.status),
                stratum=batch.stratum,
                item_refs=[str(item) for item in batch.item_refs],
                planned_item_count=batch.planned_item_count,
                assignee_ids=[str(item) for item in batch.assignee_ids],
                reviewer_pool_ids=[str(item) for item in batch.reviewer_pool_ids],
                due_at=batch.due_at,
                submitted_item_count=batch.submitted_item_count,
                approved_item_count=batch.approved_item_count,
                rejected_item_count=batch.rejected_item_count,
                rework_count=batch.rework_count,
                quality_summary=dict(batch.quality_summary),
                status_reason=batch.status_reason,
                updated_at=now,
                updated_by=scope.actor_id,
                version=expected_version + 1,
            )
        )
        if result.rowcount == 0:
            raise_update_failure(
                self._session,
                AnnotationBatchModel,
                scope,
                batch.batch_id,
                "标注批次",
            )
        return replace(
            batch,
            version=expected_version + 1,
            updated_at=now,
            updated_by=scope.actor_id,
        )

    def _from_row(self, row: AnnotationBatchModel) -> AnnotationBatch:
        return AnnotationBatch(
            batch_id=row.id,
            project_id=row.project_id,
            task_id=row.task_id,
            code=row.code,
            name=row.name,
            sequence_no=row.sequence_no,
            status=row.status,
            stratum=row.stratum,
            item_refs=tuple(UUID(item) for item in (row.item_refs or ())),
            planned_item_count=row.planned_item_count,
            assignee_ids=tuple(UUID(item) for item in (row.assignee_ids or ())),
            reviewer_pool_ids=tuple(UUID(item) for item in (row.reviewer_pool_ids or ())),
            due_at=as_utc(row.due_at),
            submitted_item_count=row.submitted_item_count,
            approved_item_count=row.approved_item_count,
            rejected_item_count=row.rejected_item_count,
            rework_count=row.rework_count,
            quality_summary=row.quality_summary or {},
            status_reason=row.status_reason,
            version=row.version,
            created_at=as_utc(row.created_at),
            updated_at=as_utc(row.updated_at),
            created_by=row.created_by,
            updated_by=row.updated_by,
        )
